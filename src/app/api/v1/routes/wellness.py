import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from math import isfinite
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.api.v1.routes.records import _record_response
from app.db.models import (
    BadgeAward,
    DietPlan,
    Food,
    Goal,
    GoalLog,
    HealthRecord,
    MealItem,
    MemberProfile,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import CurrentAuthenticatedSession, DbSession
from app.security.permissions import Action, Visibility, evaluate_access

router = APIRouter(tags=["diet and wellness"])

DIET_DISCLAIMER = (
    "Demo estimate only; not medical advice or a substitute for a registered dietitian. "
    "Calorie and macro values use simplified equations and illustrative food data. "
    "Health conditions, allergies, and laboratory results require clinician review."
)
ACTIVITY_FACTORS = {
    "sedentary": 1.2,
    "light": 1.375,
    "moderate": 1.55,
    "high": 1.725,
}
MEAL_TYPES = ("breakfast", "lunch", "dinner")
GOAL_UNITS = {"steps": "steps", "water": "ml", "sleep": "hours", "weight": "kg"}
ALLERGEN_ALIASES = {
    "peanut": {"peanut", "peanuts"},
    "tree nut": {"almond", "brazil nut", "cashew", "hazelnut", "pecan", "pistachio", "walnut"},
    "milk": {"dairy", "lactose", "milk", "yogurt", "cheese"},
    "egg": {"egg", "eggs"},
    "fish": {"fish", "salmon", "tuna"},
    "shellfish": {"crab", "lobster", "prawn", "shrimp", "shellfish"},
    "wheat": {"gluten", "wheat"},
    "soy": {"soya", "soy", "soybean"},
    "sesame": {"sesame"},
}


class DietPlanGenerate(BaseModel):
    weight_kg: float = Field(gt=0, le=300)
    height_cm: float = Field(gt=0, le=240)
    activity_level: Literal["sedentary", "light", "moderate", "high"]
    starts_on: date = Field(default_factory=lambda: datetime.now(UTC).date())
    visibility: Visibility = Visibility.PRIVATE

    @field_validator("weight_kg")
    @classmethod
    def validate_weight(cls, value: float) -> float:
        if value < 30:
            raise ValueError("weight_kg must be at least 30")
        return value

    @field_validator("height_cm")
    @classmethod
    def validate_height(cls, value: float) -> float:
        if value < 120:
            raise ValueError("height_cm must be at least 120")
        return value


class NutrientTotals(BaseModel):
    energy_kcal: float = 0
    protein_g: float = 0
    carbohydrate_g: float = 0
    fat_g: float = 0
    fiber_g: float = 0


class MealResponse(BaseModel):
    id: UUID
    name: str
    meal_type: str
    scheduled_on: date
    sort_order: int
    serving_size_grams: float
    allergens: list[str]
    allergen_warnings: list[str]
    nutrients: NutrientTotals


class DietPlanResponse(BaseModel):
    id: UUID
    profile_id: UUID
    name: str
    starts_on: date
    ends_on: date
    target_calories: int
    macro_targets: dict[str, float]
    safety_notes: list[str]
    excluded_foods: list[str]
    meals: list[MealResponse]
    daily_totals: dict[str, NutrientTotals]
    weekly_totals: NutrientTotals
    disclaimer: str


class DietPlanListResponse(BaseModel):
    items: list[DietPlanResponse]
    disclaimer: str


class MealMove(BaseModel):
    scheduled_on: date
    meal_type: Literal["breakfast", "lunch", "dinner", "snack"]
    sort_order: int = Field(default=0, ge=0, le=100)


class MealSwap(BaseModel):
    other_meal_id: UUID


GoalKind = Literal["steps", "water", "sleep", "weight"]


class GoalCreate(BaseModel):
    kind: GoalKind
    title: str = Field(min_length=1, max_length=160)
    target_value: float = Field(gt=0, le=1000000)
    target_date: date | None = None
    visibility: Visibility = Visibility.PRIVATE

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Title must not be blank")
        return value


class GoalLogCreate(BaseModel):
    value: float = Field(gt=0, le=1000000)
    logged_on: date = Field(default_factory=lambda: datetime.now(UTC).date())


class GoalResponse(BaseModel):
    id: UUID
    profile_id: UUID
    kind: GoalKind
    title: str
    target_value: float
    unit: str
    target_date: date | None
    status: str
    current_streak: int


class GoalLogResponse(BaseModel):
    goal: GoalResponse
    logged_on: date
    value: float
    target_met: bool
    awarded_badges: list[str]


class GoalDailyLogResponse(BaseModel):
    logged_on: date
    value: float
    target_met: bool


class BadgeResponse(BaseModel):
    id: UUID
    badge_code: str
    awarded_at: datetime
    details: dict[str, object]


def _profile_access(
    session: DbSession,
    actor: User,
    profile_id: UUID,
    action: Action,
    resource: str,
    visibility: str = Visibility.PRIVATE.value,
    record_id: UUID | None = None,
) -> MemberProfile:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    decision = evaluate_access(
        session,
        actor=actor,
        profile_id=profile_id,
        visibility=visibility,
        action=action,
        record_id=record_id,
        resource=resource,
    )
    if not decision.allowed:
        append_audit_event(
            session,
            actor_user_id=actor.id,
            target_profile_id=profile_id,
            action=f"{resource}_access_denied",
            entity_type=resource,
            details={"reason": decision.reason, "action": action.value},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")
    return profile


def _age_on(birth_date: date, on_date: date) -> int:
    return (
        on_date.year
        - birth_date.year
        - ((on_date.month, on_date.day) < (birth_date.month, birth_date.day))
    )


def _normalise(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


def _matches_tag(tag: str, terms: set[str]) -> bool:
    normalized_tag = _normalise(tag)
    tag_words = set(normalized_tag.split())
    return any(
        normalized_tag == term
        or term in normalized_tag
        or normalized_tag in term
        or bool(tag_words & set(term.split()))
        for term in terms
    )


def _allergen_terms(values: set[str]) -> set[str]:
    terms = {_normalise(value) for value in values}
    for category, aliases in ALLERGEN_ALIASES.items():
        if any(category in term or any(alias in term for alias in aliases) for term in terms):
            terms.update(aliases)
            terms.add(category)
    return terms


def _health_context(
    session: DbSession,
    actor: User,
    profile_id: UUID,
) -> tuple[set[str], set[str], bool, set[str], dict[str, float]]:
    allergies: set[str] = set()
    conditions: set[str] = set()
    has_lab_records = False
    explicit_food_exclusions: set[str] = set()
    declared_targets: dict[str, float] = {}
    records = session.scalars(
        select(HealthRecord).where(
            HealthRecord.profile_id == profile_id,
            HealthRecord.deleted_at.is_(None),
        )
    )
    for record in records:
        data = _record_response(record, actor, session).data
        record_status = str(data.get("status", "active")).casefold()
        active_condition = record.type == "condition" and record_status not in {
            "resolved",
            "inactive",
            "historical",
        }
        if record.type == "allergy":
            name = data.get("name")
            if not isinstance(name, str) or not name.strip():
                raise HTTPException(
                    status_code=422,
                    detail="Allergy records must include a non-blank name before plan generation",
                )
            allergies.add(name.strip())
        if record.type == "lab" or active_condition:
            targets = data.get("nutrition_targets")
            if targets is not None:
                if not isinstance(targets, dict):
                    raise HTTPException(
                        status_code=422,
                        detail="Health-record nutrition_targets must be an object",
                    )
                for key, value in targets.items():
                    if key not in {
                        "calories_kcal",
                        "protein_g",
                        "carbohydrate_g",
                        "fat_g",
                    }:
                        raise HTTPException(
                            status_code=422,
                            detail=f"Unsupported health-record nutrition target: {key}",
                        )
                    if (
                        isinstance(value, bool)
                        or not isinstance(value, (int, float))
                        or not isfinite(value)
                        or value < (1000 if key == "calories_kcal" else 0.1)
                        or value > (6000 if key == "calories_kcal" else 500)
                    ):
                        raise HTTPException(
                            status_code=422,
                            detail=f"Health-record nutrition target {key} is invalid",
                        )
                    if key in declared_targets and declared_targets[key] != float(value):
                        raise HTTPException(
                            status_code=422,
                            detail=f"Conflicting health-record nutrition target: {key}",
                        )
                    declared_targets[key] = float(value)
        if record.type == "condition":
            name = data.get("name")
            if active_condition and (not isinstance(name, str) or not name.strip()):
                raise HTTPException(
                    status_code=422,
                    detail="Active condition records must include a non-blank name",
                )
            if isinstance(name, str) and name.strip() and active_condition:
                conditions.add(name.strip())
        if record.type == "lab":
            has_lab_records = True
            exclusions = data.get("dietary_exclusions", [])
            if not isinstance(exclusions, list) or any(
                not isinstance(item, str) or not item.strip() for item in exclusions
            ):
                raise HTTPException(
                    status_code=422,
                    detail="Lab dietary_exclusions must be a list of non-blank food names",
                )
            explicit_food_exclusions.update(item.strip() for item in exclusions)
    return allergies, conditions, has_lab_records, explicit_food_exclusions, declared_targets


def _targets(
    profile: MemberProfile,
    on_date: date,
    weight_kg: float,
    height_cm: float,
    activity_level: str,
    declared_targets: dict[str, float],
) -> tuple[int, dict[str, float]]:
    if profile.date_of_birth is None:
        raise HTTPException(
            status_code=422,
            detail="A date of birth is required to estimate a diet plan",
        )
    age = _age_on(profile.date_of_birth, on_date)
    if age < 18 or age > 100:
        raise HTTPException(
            status_code=422,
            detail="The demo diet engine only supports adults aged 18 through 100",
        )
    sex = (profile.sex or "").casefold()
    sex_offset = 5 if sex == "male" else -161 if sex == "female" else -78
    bmr = 10 * weight_kg + 6.25 * height_cm - 5 * age + sex_offset
    calories = round(declared_targets.get("calories_kcal", bmr * ACTIVITY_FACTORS[activity_level]))
    protein = declared_targets.get("protein_g", weight_kg)
    fat = declared_targets.get("fat_g", calories * 0.30 / 9)
    carbohydrate = max(0.0, (calories - protein * 4 - fat * 9) / 4)
    carbohydrate = declared_targets.get("carbohydrate_g", carbohydrate)
    if protein * 4 + carbohydrate * 4 + fat * 9 > calories * 1.1:
        raise HTTPException(
            status_code=422,
            detail="Recorded macro targets conflict with the calorie target",
        )
    return calories, {
        "protein_g": round(protein, 1),
        "carbohydrate_g": round(carbohydrate, 1),
        "fat_g": round(declared_targets.get("fat_g", fat), 1),
    }


def _meal_nutrients(food: Food, grams: float) -> NutrientTotals:
    return NutrientTotals(
        **{
            key: round(float(value) * grams / 100, 2)
            for key, value in food.nutrients_per_100g.items()
            if key in NutrientTotals.model_fields
        }
    )


def _sum_nutrients(items: list[MealItem], foods: dict[UUID, Food]) -> NutrientTotals:
    totals = {key: 0.0 for key in NutrientTotals.model_fields}
    for item in items:
        food = foods.get(item.food_id) if item.food_id else None
        if food is None:
            continue
        nutrients = _meal_nutrients(food, float(item.serving_size_grams))
        for key in totals:
            totals[key] += getattr(nutrients, key)
    return NutrientTotals(**{key: round(value, 2) for key, value in totals.items()})


def _can_read_private_health(session: DbSession, actor: User, profile_id: UUID) -> bool:
    return evaluate_access(
        session,
        actor=actor,
        profile_id=profile_id,
        visibility=Visibility.PRIVATE,
        action=Action.READ,
        resource="health_records",
    ).allowed


def _plan_response(
    session: DbSession,
    plan: DietPlan,
    *,
    include_safety_details: bool = True,
) -> DietPlanResponse:
    meals = list(
        session.scalars(
            select(MealItem)
            .where(MealItem.diet_plan_id == plan.id, MealItem.deleted_at.is_(None))
            .order_by(MealItem.scheduled_on, MealItem.sort_order, MealItem.id)
        )
    )
    food_ids = {item.food_id for item in meals if item.food_id is not None}
    foods = (
        {food.id: food for food in session.scalars(select(Food).where(Food.id.in_(food_ids)))}
        if food_ids
        else {}
    )
    daily_meals: dict[date, list[MealItem]] = {}
    rendered_meals: list[MealResponse] = []
    for item in meals:
        food = foods.get(item.food_id) if item.food_id else None
        if item.scheduled_on is None:
            continue
        daily_meals.setdefault(item.scheduled_on, []).append(item)
        rendered_meals.append(
            MealResponse(
                id=item.id,
                name=item.name,
                meal_type=item.meal_type,
                scheduled_on=item.scheduled_on,
                sort_order=item.sort_order,
                serving_size_grams=float(item.serving_size_grams),
                allergens=list(food.allergens) if food is not None else [],
                allergen_warnings=list(food.allergens) if food is not None else [],
                nutrients=_meal_nutrients(food, float(item.serving_size_grams))
                if food is not None
                else NutrientTotals(),
            )
        )
    daily_totals = {
        day.isoformat(): _sum_nutrients(items, foods) for day, items in daily_meals.items()
    }
    safety_notes = []
    if include_safety_details and plan.notes:
        safety_notes = [line for line in plan.notes.splitlines() if line]
    return DietPlanResponse(
        id=plan.id,
        profile_id=plan.profile_id,
        name=plan.name,
        starts_on=plan.starts_on or date.min,
        ends_on=plan.ends_on or date.min,
        target_calories=plan.target_calories or 0,
        macro_targets=plan.macro_targets,
        safety_notes=safety_notes,
        excluded_foods=plan.excluded_foods if include_safety_details else [],
        meals=rendered_meals,
        daily_totals=daily_totals,
        weekly_totals=_sum_nutrients(meals, foods),
        disclaimer=DIET_DISCLAIMER,
    )


@router.post(
    "/profiles/{profile_id}/diet/plans/generate",
    response_model=DietPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
@router.post(
    "/diet/plans/generate",
    response_model=DietPlanResponse,
    status_code=status.HTTP_201_CREATED,
)
def generate_diet_plan(
    profile_id: UUID,
    body: DietPlanGenerate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> DietPlanResponse:
    profile = _profile_access(
        session,
        authenticated.user,
        profile_id,
        Action.WRITE,
        "diet_plans",
        body.visibility.value,
    )
    health_access = evaluate_access(
        session,
        actor=authenticated.user,
        profile_id=profile_id,
        visibility=Visibility.PRIVATE,
        action=Action.READ,
        resource="health_records",
    )
    if not health_access.allowed:
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile_id,
            action="diet_plan_health_context_denied",
            entity_type="health_records",
            details={"reason": health_access.reason},
        )
        session.commit()
        raise HTTPException(
            status_code=403,
            detail="Private health-record access is required to apply profile safety exclusions",
        )
    allergies, conditions, has_labs, explicit_exclusions, declared_targets = _health_context(
        session, authenticated.user, profile_id
    )
    calories, macros = _targets(
        profile,
        body.starts_on,
        body.weight_kg,
        body.height_cm,
        body.activity_level,
        declared_targets,
    )
    foods = list(
        session.scalars(select(Food).where(Food.deleted_at.is_(None)).order_by(Food.name, Food.id))
    )
    if not foods:
        raise HTTPException(status_code=409, detail="No active foods are available for planning")

    excluded: list[Food] = []
    safe_foods: list[Food] = []
    allergy_terms = _allergen_terms(allergies)
    condition_terms = {_normalise(item) for item in conditions}
    exclusion_terms = {_normalise(item) for item in explicit_exclusions}
    for food in foods:
        if (
            not isinstance(food.allergens, list)
            or any(not isinstance(item, str) for item in food.allergens)
            or not isinstance(food.excluded_conditions, list)
            or any(not isinstance(item, str) for item in food.excluded_conditions)
            or not isinstance(food.nutrients_per_100g, dict)
            or any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(value)
                or value < 0
                for value in food.nutrients_per_100g.values()
            )
        ):
            raise HTTPException(
                status_code=409,
                detail=f"Food safety or nutrient metadata is invalid for {food.name}",
            )
        blocked = any(_matches_tag(allergen, allergy_terms) for allergen in food.allergens)
        blocked = blocked or any(
            _matches_tag(condition, condition_terms) for condition in food.excluded_conditions
        )
        blocked = blocked or _matches_tag(food.name, exclusion_terms)
        if blocked:
            excluded.append(food)
        elif float(food.nutrients_per_100g.get("energy_kcal", 0)) > 0:
            safe_foods.append(food)
        else:
            excluded.append(food)
    if not safe_foods:
        raise HTTPException(
            status_code=422,
            detail="No foods remain after applying the profile's allergy and condition exclusions",
        )

    notes = [
        "Health conditions and clinician-entered lab exclusions are treated as "
        "hard food exclusions.",
        "Meal portions and nutrient totals are illustrative and may not exactly meet targets.",
    ]
    if has_labs:
        notes.append(
            "Laboratory records were present; raw lab values are not clinically interpreted "
            "by this demo engine."
        )
    if conditions:
        notes.append(
            "Active condition records were present; matching condition exclusions apply. "
            "Use condition-specific advice from a clinician."
        )
    if declared_targets:
        notes.append(
            "Explicit nutrition targets in condition/lab records were applied; verify them "
            "with a clinician."
        )
    excluded_food_names = [food.name for food in excluded]
    plan = DietPlan(
        profile_id=profile_id,
        name=f"Demo weekly plan starting {body.starts_on.isoformat()}",
        starts_on=body.starts_on,
        ends_on=body.starts_on + timedelta(days=6),
        notes="\n".join(notes),
        target_calories=calories,
        macro_targets=macros,
        excluded_foods=excluded_food_names,
        visibility=body.visibility.value,
    )
    session.add(plan)
    session.flush()
    for day_offset in range(7):
        meal_day = body.starts_on + timedelta(days=day_offset)
        for meal_index, meal_type in enumerate(MEAL_TYPES):
            food = safe_foods[(day_offset * len(MEAL_TYPES) + meal_index) % len(safe_foods)]
            energy = float(food.nutrients_per_100g["energy_kcal"])
            grams = min(600.0, max(25.0, calories / 3 * 100 / energy))
            session.add(
                MealItem(
                    diet_plan_id=plan.id,
                    food_id=food.id,
                    name=food.name,
                    meal_type=meal_type,
                    serving_size_grams=Decimal(f"{grams:.2f}"),
                    scheduled_on=meal_day,
                    sort_order=meal_index,
                )
            )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="diet_plan_generated",
        entity_type="diet_plans",
        entity_id=plan.id,
        details={
            "meal_count": 21,
            "excluded_food_count": len(excluded),
            "has_lab_records": has_labs,
        },
    )
    session.commit()
    session.refresh(plan)
    result = _plan_response(session, plan)
    return result


@router.get("/profiles/{profile_id}/diet/plans", response_model=DietPlanListResponse)
def list_diet_plans(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> DietPlanListResponse:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    plans = session.scalars(
        select(DietPlan)
        .where(DietPlan.profile_id == profile_id, DietPlan.deleted_at.is_(None))
        .order_by(DietPlan.created_at.desc(), DietPlan.id)
    )
    visible = [
        plan
        for plan in plans
        if evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=profile_id,
            visibility=plan.visibility,
            action=Action.READ,
            record_id=plan.id,
            resource="diet_plans",
        ).allowed
    ]
    response.headers["Cache-Control"] = "no-store"
    include_safety_details = _can_read_private_health(session, authenticated.user, profile_id)
    return DietPlanListResponse(
        items=[
            _plan_response(session, plan, include_safety_details=include_safety_details)
            for plan in visible
        ],
        disclaimer=DIET_DISCLAIMER,
    )


def _owned_plan(
    session: DbSession,
    actor: User,
    plan_id: UUID,
    action: Action,
) -> DietPlan:
    plan = session.get(DietPlan, plan_id)
    if plan is None or plan.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Diet plan not found")
    _profile_access(
        session,
        actor,
        plan.profile_id,
        action,
        "diet_plans",
        plan.visibility,
        plan.id,
    )
    return plan


@router.get("/diet/plans/{plan_id}", response_model=DietPlanResponse)
def get_diet_plan(
    plan_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> DietPlanResponse:
    plan = _owned_plan(session, authenticated.user, plan_id, Action.READ)
    response.headers["Cache-Control"] = "no-store"
    return _plan_response(
        session,
        plan,
        include_safety_details=_can_read_private_health(
            session, authenticated.user, plan.profile_id
        ),
    )


@router.patch("/diet/plans/{plan_id}/meals/{meal_id}", response_model=DietPlanResponse)
@router.post(
    "/diet/plans/{plan_id}/meals/{meal_id}/move",
    response_model=DietPlanResponse,
    include_in_schema=False,
)
def move_meal(
    plan_id: UUID,
    meal_id: UUID,
    body: MealMove,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> DietPlanResponse:
    plan = _owned_plan(session, authenticated.user, plan_id, Action.WRITE)
    meal = session.scalar(
        select(MealItem).where(
            MealItem.id == meal_id,
            MealItem.diet_plan_id == plan.id,
            MealItem.deleted_at.is_(None),
        )
    )
    if meal is None:
        raise HTTPException(status_code=404, detail="Meal not found in this plan")
    if (
        plan.starts_on is None
        or plan.ends_on is None
        or not plan.starts_on <= body.scheduled_on <= plan.ends_on
    ):
        raise HTTPException(status_code=422, detail="Meal date must be within the plan week")
    meal.scheduled_on = body.scheduled_on
    meal.meal_type = body.meal_type
    meal.sort_order = body.sort_order
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=plan.profile_id,
        action="diet_meal_moved",
        entity_type="meal_items",
        entity_id=meal.id,
        details={"diet_plan_id": str(plan.id), "scheduled_on": body.scheduled_on.isoformat()},
    )
    session.commit()
    return _plan_response(
        session,
        plan,
        include_safety_details=_can_read_private_health(
            session, authenticated.user, plan.profile_id
        ),
    )


@router.post("/diet/plans/{plan_id}/meals/{meal_id}/swap", response_model=DietPlanResponse)
def swap_meals(
    plan_id: UUID,
    meal_id: UUID,
    body: MealSwap,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> DietPlanResponse:
    plan = _owned_plan(session, authenticated.user, plan_id, Action.WRITE)
    meals = list(
        session.scalars(
            select(MealItem).where(
                MealItem.id.in_([meal_id, body.other_meal_id]),
                MealItem.diet_plan_id == plan.id,
                MealItem.deleted_at.is_(None),
            )
        )
    )
    if len(meals) != 2 or meal_id == body.other_meal_id:
        raise HTTPException(status_code=404, detail="Both meals must exist in this plan")
    first = next(item for item in meals if item.id == meal_id)
    second = next(item for item in meals if item.id == body.other_meal_id)
    first.scheduled_on, second.scheduled_on = second.scheduled_on, first.scheduled_on
    first.meal_type, second.meal_type = second.meal_type, first.meal_type
    first.sort_order, second.sort_order = second.sort_order, first.sort_order
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=plan.profile_id,
        action="diet_meals_swapped",
        entity_type="diet_plans",
        entity_id=plan.id,
    )
    session.commit()
    return _plan_response(
        session,
        plan,
        include_safety_details=_can_read_private_health(
            session, authenticated.user, plan.profile_id
        ),
    )


def _goal_streak(session: DbSession, goal: Goal) -> int:
    logs = list(
        session.scalars(
            select(GoalLog).where(GoalLog.goal_id == goal.id).order_by(GoalLog.logged_on.desc())
        )
    )
    if not logs:
        return 0
    target = float(goal.target_value or 0)
    streak = 0
    expected_day = logs[0].logged_on
    for log in logs:
        if log.logged_on != expected_day:
            break
        if goal.goal_kind != "weight" and float(log.value) < target:
            break
        streak += 1
        expected_day -= timedelta(days=1)
    return streak


def _goal_response(session: DbSession, goal: Goal) -> GoalResponse:
    if goal.goal_kind not in GOAL_UNITS:
        raise HTTPException(
            status_code=409, detail="This legacy goal has no supported wellness kind"
        )
    if goal.goal_kind == "steps":
        kind: GoalKind = "steps"
    elif goal.goal_kind == "water":
        kind = "water"
    elif goal.goal_kind == "sleep":
        kind = "sleep"
    else:
        kind = "weight"
    return GoalResponse(
        id=goal.id,
        profile_id=goal.profile_id,
        kind=kind,
        title=goal.title,
        target_value=float(goal.target_value or 0),
        unit=GOAL_UNITS[goal.goal_kind],
        target_date=goal.target_date,
        status=goal.status,
        current_streak=_goal_streak(session, goal),
    )


def _award_badge(
    session: DbSession,
    *,
    profile_id: UUID,
    badge_code: str,
    goal_id: UUID,
    details: dict[str, object],
) -> bool:
    existing = session.scalar(
        select(BadgeAward.id).where(
            BadgeAward.profile_id == profile_id,
            BadgeAward.badge_code == badge_code,
            BadgeAward.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return False
    session.add(
        BadgeAward(
            profile_id=profile_id,
            badge_code=badge_code,
            details={"goal_id": str(goal_id), **details},
        )
    )
    return True


@router.post(
    "/profiles/{profile_id}/goals",
    response_model=GoalResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_goal(
    profile_id: UUID,
    body: GoalCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> GoalResponse:
    _profile_access(
        session,
        authenticated.user,
        profile_id,
        Action.WRITE,
        "goals",
        body.visibility.value,
    )
    goal = Goal(
        profile_id=profile_id,
        goal_kind=body.kind,
        title=body.title,
        target_value=Decimal(str(body.target_value)),
        unit=GOAL_UNITS[body.kind],
        target_date=body.target_date,
        status="active",
        visibility=body.visibility.value,
    )
    session.add(goal)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile_id,
        action="goal_created",
        entity_type="goals",
        entity_id=goal.id,
        details={"kind": body.kind},
    )
    session.commit()
    session.refresh(goal)
    return _goal_response(session, goal)


@router.get("/profiles/{profile_id}/goals", response_model=list[GoalResponse])
def list_goals(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[GoalResponse]:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    goals = session.scalars(
        select(Goal)
        .where(Goal.profile_id == profile_id, Goal.deleted_at.is_(None))
        .order_by(Goal.created_at, Goal.id)
    )
    visible = [
        goal
        for goal in goals
        if goal.goal_kind in GOAL_UNITS
        and evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=profile_id,
            visibility=goal.visibility,
            action=Action.READ,
            record_id=goal.id,
            resource="goals",
        ).allowed
    ]
    response.headers["Cache-Control"] = "no-store"
    return [_goal_response(session, goal) for goal in visible]


@router.post("/goals/{goal_id}/logs", response_model=GoalLogResponse)
def log_goal(
    goal_id: UUID,
    body: GoalLogCreate,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> GoalLogResponse:
    goal = session.scalar(select(Goal).where(Goal.id == goal_id).with_for_update())
    if goal is None or goal.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Goal not found")
    if goal.goal_kind not in GOAL_UNITS:
        raise HTTPException(
            status_code=409, detail="This legacy goal has no supported wellness kind"
        )
    _profile_access(
        session,
        authenticated.user,
        goal.profile_id,
        Action.WRITE,
        "goals",
        goal.visibility,
        goal.id,
    )
    session.scalar(
        select(MemberProfile).where(MemberProfile.id == goal.profile_id).with_for_update()
    )
    today = datetime.now(UTC).date()
    if body.logged_on > today or body.logged_on < today - timedelta(days=365):
        raise HTTPException(status_code=422, detail="Log date must be within the last 365 days")
    log = session.scalar(
        select(GoalLog).where(GoalLog.goal_id == goal.id, GoalLog.logged_on == body.logged_on)
    )
    if log is None:
        log = GoalLog(goal_id=goal.id, logged_on=body.logged_on, value=Decimal(str(body.value)))
        session.add(log)
    else:
        log.value = Decimal(str(body.value))
    session.flush()
    badges: list[str] = []
    if _award_badge(
        session,
        profile_id=goal.profile_id,
        badge_code="first_goal_log",
        goal_id=goal.id,
        details={"logged_on": body.logged_on.isoformat()},
    ):
        badges.append("first_goal_log")
    target = float(goal.target_value or 0)
    target_met = (
        abs(body.value - target) <= 1.0 if goal.goal_kind == "weight" else body.value >= target
    )
    if target_met and _award_badge(
        session,
        profile_id=goal.profile_id,
        badge_code="goal_target_met",
        goal_id=goal.id,
        details={"kind": goal.goal_kind, "logged_on": body.logged_on.isoformat()},
    ):
        badges.append("goal_target_met")
    streak = _goal_streak(session, goal)
    if streak >= 7 and _award_badge(
        session,
        profile_id=goal.profile_id,
        badge_code="seven_day_streak",
        goal_id=goal.id,
        details={"streak_days": streak},
    ):
        badges.append("seven_day_streak")
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=goal.profile_id,
        action="goal_logged",
        entity_type="goal_logs",
        entity_id=log.id,
        details={"goal_id": str(goal.id), "logged_on": body.logged_on.isoformat()},
    )
    session.commit()
    session.refresh(log)
    return GoalLogResponse(
        goal=_goal_response(session, goal),
        logged_on=log.logged_on,
        value=float(log.value),
        target_met=target_met,
        awarded_badges=badges,
    )


@router.get("/goals/{goal_id}/logs", response_model=list[GoalDailyLogResponse])
def list_goal_logs(
    goal_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    start_date: date | None = None,
    end_date: date | None = None,
    limit: int = Query(default=100, ge=1, le=366),
) -> list[GoalDailyLogResponse]:
    goal = session.get(Goal, goal_id)
    if goal is None or goal.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Goal not found")
    if goal.goal_kind not in GOAL_UNITS:
        raise HTTPException(
            status_code=409, detail="This legacy goal has no supported wellness kind"
        )
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
    _profile_access(
        session,
        authenticated.user,
        goal.profile_id,
        Action.READ,
        "goals",
        goal.visibility,
        goal.id,
    )
    statement = select(GoalLog).where(GoalLog.goal_id == goal.id)
    if start_date is not None:
        statement = statement.where(GoalLog.logged_on >= start_date)
    if end_date is not None:
        statement = statement.where(GoalLog.logged_on <= end_date)
    logs = session.scalars(statement.order_by(GoalLog.logged_on.desc()).limit(limit))
    target = float(goal.target_value or 0)
    response.headers["Cache-Control"] = "no-store"
    return [
        GoalDailyLogResponse(
            logged_on=log.logged_on,
            value=float(log.value),
            target_met=(
                abs(float(log.value) - target) <= 1.0
                if goal.goal_kind == "weight"
                else float(log.value) >= target
            ),
        )
        for log in logs
    ]


@router.get("/profiles/{profile_id}/badges", response_model=list[BadgeResponse])
def list_badges(
    profile_id: UUID,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> list[BadgeResponse]:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    badges = session.scalars(
        select(BadgeAward)
        .where(BadgeAward.profile_id == profile_id, BadgeAward.deleted_at.is_(None))
        .order_by(BadgeAward.awarded_at, BadgeAward.id)
    )
    visible_badges = [
        badge
        for badge in badges
        if evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=profile_id,
            visibility=badge.visibility,
            action=Action.READ,
            record_id=badge.id,
            resource="badge_awards",
        ).allowed
    ]
    response.headers["Cache-Control"] = "no-store"
    return [
        BadgeResponse(
            id=badge.id,
            badge_code=badge.badge_code,
            awarded_at=badge.awarded_at,
            details=badge.details,
        )
        for badge in visible_badges
    ]


@router.delete("/diet/plans/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_diet_plan(
    plan_id: UUID,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> Response:
    plan = _owned_plan(session, authenticated.user, plan_id, Action.DELETE)
    plan.deleted_at = datetime.now(UTC)
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=plan.profile_id,
        action="diet_plan_deleted",
        entity_type="diet_plans",
        entity_id=plan.id,
    )
    session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
