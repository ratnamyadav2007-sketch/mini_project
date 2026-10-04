from datetime import UTC, date, datetime, time, timedelta
from math import isfinite
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Response
from pydantic import BaseModel
from sqlalchemy import select

from app.api.v1.routes.records import _record_response
from app.db.models import (
    Family,
    FamilyMembership,
    HealthRecord,
    MemberProfile,
    RecordType,
    ReferenceRange,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import CurrentAuthenticatedSession, DbSession
from app.security.permissions import Action, Visibility, evaluate_access

router = APIRouter(tags=["health insights"])


class MetricPoint(BaseModel):
    recorded_at: datetime
    value: float
    unit: str | None
    range_flag: Literal["below", "within", "above", "unknown"]
    reference_population: str | None
    reference_note: str | None
    reference_lower: float | None
    reference_upper: float | None


class MetricInsights(BaseModel):
    profile_id: UUID
    metric: str
    start_date: date | None
    end_date: date | None
    points: list[MetricPoint]
    trend: Literal["increasing", "decreasing", "stable", "insufficient_data"]
    improvement_delta: float | None
    improvement: Literal["improved", "worsened", "unchanged", "unknown"]


class FamilyComparisonMember(BaseModel):
    profile_id: UUID
    member_label: str
    baseline_date: date
    latest_date: date
    baseline_normalized_value: float
    latest_normalized_value: float
    improvement_delta: float
    improvement: Literal["improved", "worsened", "unchanged"]
    reference_population: str


class FamilyMetricComparison(BaseModel):
    family_id: UUID
    metric: str
    members: list[FamilyComparisonMember]
    disclaimer: str


class InsightChartSeries(BaseModel):
    profile_id: UUID
    member_label: str
    locked: bool
    points: list[MetricPoint]
    trend: Literal["increasing", "decreasing", "stable", "insufficient_data"]
    improvement_delta: float | None
    improvement: Literal["improved", "worsened", "unchanged", "unknown"]


class InsightChartResponse(BaseModel):
    metric: str
    start_date: date | None
    end_date: date
    series: list[InsightChartSeries]
    reference_ranges: list[dict[str, str | float | None]]
    disclaimer: str


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _age_on(birth_date: date | None, at: date) -> int | None:
    if birth_date is None:
        return None
    return at.year - birth_date.year - ((at.month, at.day) < (birth_date.month, birth_date.day))


def _sex_key(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"female", "f"}:
        return "female"
    if normalized in {"male", "m"}:
        return "male"
    return None


def _record_value(
    record: HealthRecord,
    actor: User,
    session: DbSession,
) -> tuple[float, str | None] | None:
    if record.value is not None:
        numeric = float(record.value)
        return (numeric, record.unit) if isfinite(numeric) else None
    data = _record_response(record, actor, session).data
    value = data.get("value")
    if isinstance(value, bool) or not isinstance(value, (float, int, str)):
        return None
    try:
        numeric = float(value)
    except ValueError:
        return None
    if not isfinite(numeric):
        return None
    unit = data.get("unit")
    return numeric, unit.strip() if isinstance(unit, str) and unit.strip() else None


def _matching_reference(
    session: DbSession,
    *,
    metric: str,
    unit: str | None,
    profile: MemberProfile,
    on_date: date,
    require_demographic_match: bool = False,
) -> ReferenceRange | None:
    if unit is None:
        return None
    age = _age_on(profile.date_of_birth, on_date)
    sex = _sex_key(profile.sex)
    ranges = list(
        session.scalars(
            select(ReferenceRange)
            .join(RecordType, RecordType.id == ReferenceRange.record_type_id)
            .where(
                RecordType.code == metric,
                ReferenceRange.unit == unit,
                ReferenceRange.deleted_at.is_(None),
                (ReferenceRange.age_min.is_(None) | (ReferenceRange.age_min <= age))
                if age is not None
                else ReferenceRange.age_min.is_(None),
                (ReferenceRange.age_max.is_(None) | (ReferenceRange.age_max >= age))
                if age is not None
                else ReferenceRange.age_max.is_(None),
                (ReferenceRange.sex.is_(None) | (ReferenceRange.sex == sex))
                if sex is not None
                else ReferenceRange.sex.is_(None),
            )
        )
    )
    if require_demographic_match:
        ranges = [
            item
            for item in ranges
            if age is not None
            and sex is not None
            and item.sex is not None
            and item.age_min is not None
            and item.age_max is not None
        ]
    ranges.sort(
        key=lambda item: (
            item.sex is None,
            item.age_min is None,
            (item.age_max - item.age_min)
            if item.age_min is not None and item.age_max is not None
            else 10_000,
        )
    )
    return ranges[0] if ranges else None


def _range_flag(
    value: float,
    reference: ReferenceRange | None,
) -> Literal["below", "within", "above", "unknown"]:
    if reference is None or (reference.lower_bound is None and reference.upper_bound is None):
        return "unknown"
    if reference.lower_bound is not None and value < float(reference.lower_bound):
        return "below"
    if reference.upper_bound is not None and value > float(reference.upper_bound):
        return "above"
    return "within"


def _normalized_value(value: float, reference: ReferenceRange) -> float:
    lower = float(reference.lower_bound) if reference.lower_bound is not None else None
    upper = float(reference.upper_bound) if reference.upper_bound is not None else None
    if lower is None or upper is None or upper <= lower:
        raise HTTPException(
            status_code=422,
            detail="A two-sided demographic reference range is required for comparison",
        )
    return round((value - (lower + upper) / 2) / (upper - lower), 4)


def _outside_distance(value: float, reference: ReferenceRange) -> float:
    lower = float(reference.lower_bound) if reference.lower_bound is not None else None
    upper = float(reference.upper_bound) if reference.upper_bound is not None else None
    width = (upper - lower) if lower is not None and upper is not None else None
    if width is None or width <= 0:
        return 0.0
    if lower is not None and value < lower:
        return (lower - value) / width
    if upper is not None and value > upper:
        return (value - upper) / width
    return 0.0


def _readable_records(
    session: DbSession,
    *,
    actor: User,
    profile: MemberProfile,
    metric: str,
    start_date: date | None,
    end_date: date | None,
    latest_first: bool = False,
) -> list[HealthRecord]:
    statement = select(HealthRecord).where(
        HealthRecord.profile_id == profile.id,
        HealthRecord.type == metric,
        HealthRecord.deleted_at.is_(None),
    )
    if start_date is not None:
        statement = statement.where(
            HealthRecord.recorded_at >= datetime.combine(start_date, datetime.min.time(), UTC)
        )
    if end_date is not None:
        statement = statement.where(
            HealthRecord.recorded_at <= datetime.combine(end_date, time.max, UTC)
        )
    result: list[HealthRecord] = []
    ordering = (
        (HealthRecord.recorded_at.desc(), HealthRecord.id.desc())
        if latest_first
        else (HealthRecord.recorded_at, HealthRecord.id)
    )
    for record in session.scalars(statement.order_by(*ordering)):
        decision = evaluate_access(
            session,
            actor=actor,
            profile_id=profile.id,
            visibility=record.visibility,
            action=Action.READ,
            record_id=record.id,
            resource="health_records",
        )
        if decision.allowed:
            result.append(record)
    return result


def _metric_exists(session: DbSession, metric: str) -> None:
    if session.scalar(select(RecordType.id).where(RecordType.code == metric)) is None:
        raise HTTPException(status_code=404, detail="Metric not found")


def _profile(session: DbSession, profile_id: UUID) -> MemberProfile:
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def _insights(
    session: DbSession,
    *,
    actor: User,
    profile: MemberProfile,
    metric: str,
    start_date: date | None,
    end_date: date | None,
    offset: int,
    limit: int,
    latest_first: bool = False,
) -> MetricInsights:
    records = _readable_records(
        session,
        actor=actor,
        profile=profile,
        metric=metric,
        start_date=start_date,
        end_date=end_date,
        latest_first=latest_first,
    )
    points: list[MetricPoint] = []
    for record in records:
        extracted = _record_value(record, actor, session)
        if extracted is None:
            continue
        value, unit = extracted
        recorded_at = _utc(record.recorded_at)
        reference = _matching_reference(
            session,
            metric=metric,
            unit=unit,
            profile=profile,
            on_date=recorded_at.date(),
        )
        points.append(
            MetricPoint(
                recorded_at=recorded_at,
                value=value,
                unit=unit,
                range_flag=_range_flag(value, reference),  # type: ignore[arg-type]
                reference_population=reference.population if reference else None,
                reference_note=reference.source_note if reference else None,
                reference_lower=float(reference.lower_bound)
                if reference is not None and reference.lower_bound is not None
                else None,
                reference_upper=float(reference.upper_bound)
                if reference is not None and reference.upper_bound is not None
                else None,
            )
        )
    selected_points = (
        list(reversed(points[:limit])) if latest_first else points[offset : offset + limit]
    )
    trend: Literal["increasing", "decreasing", "stable", "insufficient_data"] = "insufficient_data"
    improvement: Literal["improved", "worsened", "unchanged", "unknown"] = "unknown"
    improvement_delta: float | None = None
    if len(selected_points) >= 2:
        delta = selected_points[-1].value - selected_points[0].value
        tolerance = max(abs(selected_points[0].value), 1.0) * 0.01
        trend = "stable" if abs(delta) <= tolerance else "increasing" if delta > 0 else "decreasing"
        first_range = _matching_reference(
            session,
            metric=metric,
            unit=selected_points[0].unit,
            profile=profile,
            on_date=selected_points[0].recorded_at.date(),
        )
        last_range = _matching_reference(
            session,
            metric=metric,
            unit=selected_points[-1].unit,
            profile=profile,
            on_date=selected_points[-1].recorded_at.date(),
        )
        if (
            first_range is not None
            and last_range is not None
            and first_range.lower_bound is not None
            and first_range.upper_bound is not None
            and last_range.lower_bound is not None
            and last_range.upper_bound is not None
        ):
            start_distance = _outside_distance(selected_points[0].value, first_range)
            end_distance = _outside_distance(selected_points[-1].value, last_range)
            improvement_delta = round(start_distance - end_distance, 4)
            improvement = (
                "improved"
                if improvement_delta > 0
                else "worsened"
                if improvement_delta < 0
                else "unchanged"
            )
    return MetricInsights(
        profile_id=profile.id,
        metric=metric,
        start_date=start_date,
        end_date=end_date,
        points=selected_points,
        trend=trend,
        improvement_delta=improvement_delta,
        improvement=improvement,
    )


@router.get("/profiles/{profile_id}/insights/{metric}", response_model=MetricInsights)
def get_metric_insights(
    profile_id: UUID,
    metric: str,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    member_id: UUID | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
    offset: int = Query(default=0, ge=0, le=10000),
    limit: int = Query(default=100, ge=1, le=366),
) -> MetricInsights:
    selected_profile_id = member_id or profile_id
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
    _metric_exists(session, metric)
    profile = _profile(session, selected_profile_id)
    response.headers["Cache-Control"] = "no-store"
    insights = _insights(
        session,
        actor=authenticated.user,
        profile=profile,
        metric=metric,
        start_date=start_date,
        end_date=end_date,
        offset=offset,
        limit=limit,
    )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="metric_insights_viewed",
        entity_type="health_records",
        details={"metric": metric, "returned_points": len(insights.points)},
    )
    session.commit()
    return insights


@router.get("/insights", response_model=InsightChartResponse)
def get_insight_chart(
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    metric: str,
    range_name: Annotated[Literal["7d", "30d", "90d", "1y", "all"], Query(alias="range")] = "30d",
    members: str | None = None,
) -> InsightChartResponse:
    _metric_exists(session, metric)
    owned_profiles = list(
        session.scalars(
            select(MemberProfile).where(
                MemberProfile.user_id == authenticated.user.id,
                MemberProfile.deleted_at.is_(None),
            )
        )
    )
    owned_ids = {profile.id for profile in owned_profiles}
    if not owned_ids:
        raise HTTPException(status_code=403, detail="An active profile is required")

    memberships = list(
        session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.profile_id.in_(owned_ids),
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
            )
        )
    )
    family_ids = {membership.family_id for membership in memberships}
    accessible_ids = set(owned_ids)
    if family_ids:
        accessible_ids.update(
            session.scalars(
                select(FamilyMembership.profile_id).where(
                    FamilyMembership.family_id.in_(family_ids),
                    FamilyMembership.status == "active",
                    FamilyMembership.deleted_at.is_(None),
                )
            )
        )
    if members is None or not members.strip():
        selected_ids = accessible_ids
    else:
        try:
            selected_ids = {UUID(value.strip()) for value in members.split(",") if value.strip()}
        except ValueError as error:
            raise HTTPException(
                status_code=422,
                detail="members must be comma-separated profile UUIDs",
            ) from error
        if not selected_ids or not selected_ids <= accessible_ids:
            raise HTTPException(status_code=403, detail="Requested profile is not available")
    if len(selected_ids) > 20:
        raise HTTPException(status_code=422, detail="At most 20 member series may be requested")

    end_date = datetime.now(UTC).date()
    range_days = {"7d": 7, "30d": 30, "90d": 90, "1y": 365}
    start_date = (
        end_date - timedelta(days=range_days[range_name] - 1) if range_name in range_days else None
    )
    profiles = [
        profile
        for profile_id in sorted(selected_ids, key=str)
        if (profile := session.get(MemberProfile, profile_id)) is not None
        and profile.deleted_at is None
    ]
    series: list[InsightChartSeries] = []
    reference_ranges: dict[
        tuple[str | None, str | None, float | None, float | None],
        dict[str, str | float | None],
    ] = {}
    for index, profile in enumerate(profiles, start=1):
        access = evaluate_access(
            session,
            actor=authenticated.user,
            profile_id=profile.id,
            visibility=Visibility.FAMILY,
            action=Action.READ,
            resource="health_records",
        )
        label = "You" if profile.id in owned_ids else f"Family member {index}"
        if not access.allowed:
            append_audit_event(
                session,
                actor_user_id=authenticated.user.id,
                target_profile_id=profile.id,
                action="insights_access_denied",
                entity_type="health_records",
                details={"metric": metric, "reason": access.reason},
            )
            series.append(
                InsightChartSeries(
                    profile_id=profile.id,
                    member_label=label,
                    locked=True,
                    points=[],
                    trend="insufficient_data",
                    improvement_delta=None,
                    improvement="unknown",
                )
            )
            continue
        result = _insights(
            session,
            actor=authenticated.user,
            profile=profile,
            metric=metric,
            start_date=start_date,
            end_date=end_date,
            offset=0,
            limit=366,
        )
        append_audit_event(
            session,
            actor_user_id=authenticated.user.id,
            target_profile_id=profile.id,
            action="metric_insights_viewed",
            entity_type="health_records",
            details={"metric": metric, "returned_points": len(result.points)},
        )
        series.append(
            InsightChartSeries(
                profile_id=profile.id,
                member_label=label,
                locked=False,
                points=result.points,
                trend=result.trend,
                improvement_delta=result.improvement_delta,
                improvement=result.improvement,
            )
        )
        for point in result.points:
            if point.reference_lower is None and point.reference_upper is None:
                continue
            key = (
                point.unit,
                point.reference_population,
                point.reference_lower,
                point.reference_upper,
            )
            reference_ranges[key] = {
                "unit": point.unit,
                "population": point.reference_population,
                "lower": point.reference_lower,
                "upper": point.reference_upper,
            }
    response.headers["Cache-Control"] = "no-store"
    session.commit()
    return InsightChartResponse(
        metric=metric,
        start_date=start_date,
        end_date=end_date,
        series=series,
        reference_ranges=list(reference_ranges.values()),
        disclaimer=(
            "Reference ranges are context, not diagnoses. Family series are consent-filtered; "
            "locked series remain visible only to show that sharing is restricted."
        ),
    )


@router.get(
    "/families/{family_id}/insights/compare",
    response_model=FamilyMetricComparison,
)
def compare_family_metric(
    family_id: UUID,
    metric: str,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
    member_ids: Annotated[list[UUID] | None, Query()] = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> FamilyMetricComparison:
    if start_date is not None and end_date is not None and start_date > end_date:
        raise HTTPException(status_code=422, detail="start_date must not exceed end_date")
    _metric_exists(session, metric)
    family = session.get(Family, family_id)
    if family is None or family.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Family not found")
    actor_profile = session.scalar(
        select(MemberProfile).where(
            MemberProfile.user_id == authenticated.user.id,
            MemberProfile.deleted_at.is_(None),
        )
    )
    if actor_profile is None:
        raise HTTPException(status_code=403, detail="An active family profile is required")
    actor_membership = session.scalar(
        select(FamilyMembership).where(
            FamilyMembership.family_id == family_id,
            FamilyMembership.profile_id == actor_profile.id,
            FamilyMembership.status == "active",
            FamilyMembership.deleted_at.is_(None),
        )
    )
    if actor_membership is None:
        raise HTTPException(status_code=403, detail="Active family membership is required")
    memberships = list(
        session.scalars(
            select(FamilyMembership).where(
                FamilyMembership.family_id == family_id,
                FamilyMembership.status == "active",
                FamilyMembership.deleted_at.is_(None),
            )
        )
    )
    allowed_ids = {membership.profile_id for membership in memberships}
    if member_ids is not None and (not member_ids or not set(member_ids) <= allowed_ids):
        raise HTTPException(
            status_code=403,
            detail="Requested profile is not an active family member",
        )
    selected_ids = set(member_ids) if member_ids is not None else allowed_ids
    selected_ids.add(actor_profile.id)
    profiles = [
        profile
        for profile_id in sorted(selected_ids, key=str)
        if (profile := session.get(MemberProfile, profile_id)) is not None
        and profile.deleted_at is None
    ]
    comparisons: list[FamilyComparisonMember] = []
    for profile in profiles:
        records = _readable_records(
            session,
            actor=authenticated.user,
            profile=profile,
            metric=metric,
            start_date=start_date,
            end_date=end_date,
        )
        points: list[tuple[datetime, float, ReferenceRange]] = []
        for record in records:
            extracted = _record_value(record, authenticated.user, session)
            if extracted is None:
                continue
            value, unit = extracted
            reference = _matching_reference(
                session,
                metric=metric,
                unit=unit,
                profile=profile,
                on_date=_utc(record.recorded_at).date(),
                require_demographic_match=True,
            )
            if reference is not None:
                points.append((_utc(record.recorded_at), value, reference))
        if len(points) < 2:
            continue
        first_at, first_value, first_reference = points[0]
        last_at, last_value, last_reference = points[-1]
        if first_reference.unit != last_reference.unit:
            continue
        baseline_normalized = _normalized_value(first_value, first_reference)
        latest_normalized = _normalized_value(last_value, last_reference)
        delta = round(
            _outside_distance(first_value, first_reference)
            - _outside_distance(last_value, last_reference),
            4,
        )
        comparisons.append(
            FamilyComparisonMember(
                profile_id=profile.id,
                member_label=(
                    "You" if profile.id == actor_profile.id else f"Member {len(comparisons) + 1}"
                ),
                baseline_date=first_at.date(),
                latest_date=last_at.date(),
                baseline_normalized_value=baseline_normalized,
                latest_normalized_value=latest_normalized,
                improvement_delta=delta,
                improvement="improved" if delta > 0 else "worsened" if delta < 0 else "unchanged",
                reference_population=last_reference.population,
            )
        )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=actor_profile.id,
        action="family_metric_comparison_viewed",
        entity_type="health_records",
        entity_id=family_id,
        details={"metric": metric, "included_profiles": len(comparisons)},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return FamilyMetricComparison(
        family_id=family_id,
        metric=metric,
        members=comparisons,
        disclaimer=(
            "Demo comparison normalized against matched age/sex-specific reference intervals. "
            "This is not a ranking or clinical interpretation; use with clinician guidance."
        ),
    )
