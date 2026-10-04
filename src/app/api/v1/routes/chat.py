import re
from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentCsrfAuthenticatedSession
from app.api.v1.routes.insights import _insights
from app.db.models import MemberProfile, Reminder
from app.security.audit import append_audit_event
from app.security.dependencies import DbSession
from app.security.permissions import Action, evaluate_access
from app.security.rate_limit import enforce_rate_limit
from app.services.chat import (
    EducationTopic,
    OptionalHttpEducationModel,
    education_answer,
    emergency_answer,
)

router = APIRouter(tags=["wellness assistant"])

EMERGENCY_PATTERNS = re.compile(
    r"\b("
    r"emergency|urgent|can't breathe|cannot breathe|difficulty breathing|"
    r"chest pain|heart attack|stroke|overdos(?:e|ed|ing)|poisoning|unconscious|"
    r"suicid(?:e|al)|kill myself|self harm|hurt myself|want to die|"
    r"took too many pills|too many pills|can't stay safe|bleeding heavily|"
    r"severe bleeding|severe allergic reaction|anaphylaxis|choking|seizure"
    r")\b",
    re.IGNORECASE,
)
METRIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("systolic_blood_pressure", ("blood pressure", "systolic", "bp")),
    ("blood_glucose", ("blood glucose", "glucose", "blood sugar")),
    ("body_temperature", ("temperature", "fever")),
)


class ChatRequest(BaseModel):
    profile_id: UUID
    message: str = Field(min_length=1, max_length=1000)
    allow_model: bool = False

    @field_validator("message")
    @classmethod
    def require_non_blank(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Message must not be blank")
        return value


class ChatActionCard(BaseModel):
    type: Literal["reminder", "navigation"]
    label: str
    href: Literal["#/sos", "#/appointments", "#/diet"]
    description: str | None = None


class ChatResponse(BaseModel):
    intent: Literal["data_question", "reminder", "education", "emergency", "clarification"]
    answer: str
    source: Literal["authorized_records", "authorized_reminders", "rule_based", "model"]
    emergency_guidance: bool = False
    actions: list[ChatActionCard] = Field(default_factory=list)


def _education_topic(message: str) -> EducationTopic:
    normalized = message.casefold()
    if "sleep" in normalized:
        return "sleep"
    if any(word in normalized for word in ("water", "hydration", "drink")):
        return "hydration"
    if any(word in normalized for word in ("exercise", "activity", "movement", "steps")):
        return "activity"
    return "general"


def _metric(message: str) -> str | None:
    normalized = message.casefold()
    for metric, aliases in METRIC_ALIASES:
        if any(alias in normalized for alias in aliases):
            return metric
    return None


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@router.post("/chat/messages", response_model=ChatResponse)
@router.post("/chat", response_model=ChatResponse, include_in_schema=False)
def chat(
    body: ChatRequest,
    request: Request,
    response: Response,
    authenticated: CurrentCsrfAuthenticatedSession,
    session: DbSession,
) -> ChatResponse:
    enforce_rate_limit(
        session,
        request,
        scope="wellness-chat",
        limit=30,
        window_seconds=60,
        actor_id=str(authenticated.user.id),
    )
    profile = session.get(MemberProfile, body.profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    message = body.message
    if EMERGENCY_PATTERNS.search(message):
        result = ChatResponse(
            intent="emergency",
            answer=emergency_answer(),
            source="rule_based",
            emergency_guidance=True,
            actions=[
                ChatActionCard(
                    type="navigation",
                    label="Open SOS",
                    href="#/sos",
                    description="You must trigger SOS yourself; chat cannot dispatch an alert.",
                )
            ],
        )
    elif "remind" in message.casefold() or "appointment" in message.casefold():
        reminders = [
            reminder
            for reminder in session.scalars(
                select(Reminder)
                .where(
                    Reminder.profile_id == profile.id,
                    Reminder.status == "pending",
                    Reminder.deleted_at.is_(None),
                )
                .order_by(Reminder.due_at, Reminder.id)
                .limit(5)
            )
            if evaluate_access(
                session,
                actor=authenticated.user,
                profile_id=profile.id,
                visibility=reminder.visibility,
                action=Action.READ,
                record_id=reminder.id,
                resource="reminders",
            ).allowed
        ]
        if reminders:
            lines = [
                f"{item.title}: due {item.due_at.replace(tzinfo=UTC).isoformat()}"
                for item in reminders
            ]
            answer = "Your next authorized reminders are:\n" + "\n".join(lines)
        else:
            answer = "There are no pending reminders available to you for this profile."
        result = ChatResponse(
            intent="reminder",
            answer=answer,
            source="authorized_reminders",
            actions=[
                ChatActionCard(
                    type="reminder",
                    label=item.title,
                    href="#/appointments",
                    description=f"Due {_utc(item.due_at).isoformat()}",
                )
                for item in reminders
            ]
            or [
                ChatActionCard(
                    type="navigation",
                    label="Open appointments",
                    href="#/appointments",
                )
            ],
        )
    elif metric := _metric(message):
        insights = _insights(
            session,
            actor=authenticated.user,
            profile=profile,
            metric=metric,
            start_date=None,
            end_date=None,
            offset=0,
            limit=5,
            latest_first=True,
        )
        if not insights.points:
            answer = f"No {metric.replace('_', ' ')} readings are available to you."
        else:
            latest = insights.points[-1]
            unit = f" {latest.unit}" if latest.unit else ""
            answer = (
                f"Your latest authorized {metric.replace('_', ' ')} reading was "
                f"{latest.value:g}{unit} on {latest.recorded_at.date().isoformat()} "
                f"(reference flag: {latest.range_flag})."
            )
            if insights.improvement != "unknown":
                answer += (
                    f" The change toward the reference interval is {insights.improvement} "
                    f"(normalized delta {insights.improvement_delta:g})."
                )
            answer += " This is a data summary, not a diagnosis."
        result = ChatResponse(intent="data_question", answer=answer, source="authorized_records")
    elif any(
        word in message.casefold()
        for word in ("sleep", "water", "hydration", "exercise", "activity", "wellness")
    ):
        answer, fallback = education_answer(
            _education_topic(message),
            model=OptionalHttpEducationModel(),
            allow_model=body.allow_model,
        )
        result = ChatResponse(
            intent="education",
            answer=answer,
            source="rule_based" if fallback else "model",
        )
    else:
        result = ChatResponse(
            intent="clarification",
            answer=(
                "I can summarize an authorized blood pressure, glucose, or temperature trend; "
                "list authorized pending reminders; or share general sleep, hydration, and "
                "activity education. If this is urgent, contact emergency services now."
            ),
            source="rule_based",
        )
    append_audit_event(
        session,
        actor_user_id=authenticated.user.id,
        target_profile_id=profile.id,
        action="chat_request_processed",
        entity_type="chat",
        details={"intent": result.intent, "source": result.source},
    )
    session.commit()
    response.headers["Cache-Control"] = "no-store"
    return result
