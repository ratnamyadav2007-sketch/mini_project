import json
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

import reportlab
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer
from sqlalchemy import select

from app.api.v1.routes.auth.dependencies import CurrentAuthenticatedSession
from app.api.v1.routes.records import _record_response
from app.db.models import (
    Appointment,
    Attachment,
    BadgeAward,
    DietPlan,
    EmergencyContact,
    Goal,
    GoalLog,
    HealthRecord,
    MealItem,
    MemberProfile,
    Reminder,
    SOSEvent,
    User,
)
from app.security.audit import append_audit_event
from app.security.dependencies import DbSession
from app.security.permissions import Action, Visibility, evaluate_access
from app.security.rate_limit import enforce_rate_limit

router = APIRouter(prefix="/profiles", tags=["data rights"])


def _profile_export(
    profile_id: UUID,
    actor: User,
    request: Request,
    session: DbSession,
) -> dict[str, Any]:
    enforce_rate_limit(
        session,
        request,
        scope="profile-export",
        limit=5,
        window_seconds=3600,
        actor_id=str(actor.id),
    )
    profile = session.get(MemberProfile, profile_id)
    if profile is None or profile.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Profile not found")
    profile_access = evaluate_access(
        session,
        actor=actor,
        profile_id=profile.id,
        visibility=Visibility.PRIVATE,
        action=Action.READ,
        resource="member_profiles",
    )
    if not profile_access.allowed:
        append_audit_event(
            session,
            actor_user_id=actor.id,
            target_profile_id=profile.id,
            action="data_export_denied",
            entity_type="member_profiles",
            entity_id=profile.id,
            details={"reason": profile_access.reason},
        )
        session.commit()
        raise HTTPException(status_code=403, detail="Access to this profile is denied")

    records: list[dict[str, Any]] = []
    for record in session.scalars(
        select(HealthRecord)
        .where(
            HealthRecord.profile_id == profile.id,
            HealthRecord.deleted_at.is_(None),
        )
        .order_by(HealthRecord.recorded_at, HealthRecord.id)
    ):
        access = evaluate_access(
            session,
            actor=actor,
            profile_id=profile.id,
            visibility=record.visibility,
            action=Action.READ,
            record_id=record.id,
            resource="health_records",
        )
        if access.allowed:
            records.append(_record_response(record, actor, session).model_dump(mode="json"))

    collections: dict[str, list[dict[str, Any]]] = {}
    for name, model, resource in (
        ("appointments", Appointment, "appointments"),
        ("reminders", Reminder, "reminders"),
        ("emergency_contacts", EmergencyContact, "emergency_contacts"),
        ("goals", Goal, "goals"),
        ("diet_plans", DietPlan, "diet_plans"),
        ("badge_awards", BadgeAward, "badge_awards"),
        ("sos_events", SOSEvent, "sos_events"),
    ):
        items: list[dict[str, Any]] = []
        for item in session.scalars(
            select(model)
            .where(model.profile_id == profile.id, model.deleted_at.is_(None))
            .order_by(model.id)
        ):
            access = evaluate_access(
                session,
                actor=actor,
                profile_id=profile.id,
                visibility=getattr(item, "visibility", Visibility.PRIVATE.value),
                action=Action.READ,
                record_id=item.id,
                resource=resource,
            )
            if access.allowed:
                items.append(
                    {
                        column.name: _json_value(getattr(item, column.name))
                        for column in model.__table__.columns
                        if column.name not in {"encrypted_content", "content_nonce"}
                    }
                )
        collections[name] = items

    plan_ids = [UUID(item["id"]) for item in collections["diet_plans"]]
    collections["meal_items"] = (
        [
            {
                column.name: _json_value(getattr(item, column.name))
                for column in MealItem.__table__.columns
            }
            for item in session.scalars(
                select(MealItem)
                .where(
                    MealItem.diet_plan_id.in_(plan_ids),
                    MealItem.deleted_at.is_(None),
                )
                .order_by(MealItem.diet_plan_id, MealItem.sort_order, MealItem.id)
            )
        ]
        if plan_ids
        else []
    )

    collections["attachments"] = [
        {
            "id": str(item.id),
            "profile_id": str(item.profile_id),
            "original_filename": item.original_filename,
            "content_type": item.content_type,
            "file_size_bytes": item.file_size_bytes,
            "visibility": item.visibility,
            "created_at": item.created_at.isoformat(),
        }
        for item in session.scalars(
            select(Attachment)
            .where(
                Attachment.profile_id == profile.id,
                Attachment.deleted_at.is_(None),
            )
            .order_by(Attachment.created_at, Attachment.id)
        )
        if evaluate_access(
            session,
            actor=actor,
            profile_id=profile.id,
            visibility=item.visibility,
            action=Action.READ,
            record_id=item.id,
            resource="attachments",
        ).allowed
    ]

    goal_ids = [UUID(item["id"]) for item in collections["goals"]]
    collections["goal_logs"] = (
        [
            {
                "id": str(log.id),
                "goal_id": str(log.goal_id),
                "logged_on": log.logged_on.isoformat(),
                "value": str(log.value),
            }
            for log in session.scalars(
                select(GoalLog).where(GoalLog.goal_id.in_(goal_ids)).order_by(GoalLog.logged_on)
            )
        ]
        if goal_ids
        else []
    )
    display_name = profile.display_name or actor.display_name
    append_audit_event(
        session,
        actor_user_id=actor.id,
        target_profile_id=profile.id,
        action="data_export_created",
        entity_type="member_profiles",
        entity_id=profile.id,
        details={"format": request.url.path.rsplit(".", 1)[-1]},
    )
    session.commit()
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "profile": {
            "id": str(profile.id),
            "user_id": str(profile.user_id) if profile.user_id else None,
            "display_name": display_name,
            "date_of_birth": profile.date_of_birth.isoformat() if profile.date_of_birth else None,
            "sex": profile.sex,
            "timezone": profile.timezone,
            "preferences": profile.preferences,
        },
        "health_records": records,
        **collections,
    }


def _json_value(value: Any) -> Any:
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


@router.get("/{profile_id}/export.json")
def export_json(
    profile_id: UUID,
    request: Request,
    response: Response,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> dict[str, Any]:
    response.headers["Cache-Control"] = "no-store"
    return _profile_export(profile_id, authenticated.user, request, session)


@router.get("/{profile_id}/export.fhir")
def export_fhir(
    profile_id: UUID,
    request: Request,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> JSONResponse:
    export = _profile_export(profile_id, authenticated.user, request, session)
    profile = export["profile"]
    resources: list[dict[str, Any]] = [
        {
            "resourceType": "Patient",
            "id": profile["id"],
            "name": [{"text": profile["display_name"]}],
            "birthDate": profile["date_of_birth"],
            "gender": profile["sex"],
        }
    ]
    type_map = {
        "condition": "Condition",
        "allergy": "AllergyIntolerance",
        "medication": "MedicationStatement",
        "vaccination": "Immunization",
        "lab": "Observation",
        "vital": "Observation",
        "systolic_blood_pressure": "Observation",
        "body_temperature": "Observation",
        "blood_glucose": "Observation",
        "blood_group": "Observation",
        "surgery": "Procedure",
        "visit_note": "DocumentReference",
    }
    for record in export["health_records"]:
        resource_type = type_map.get(record["type"], "Observation")
        code = {"text": str(record["data"].get("name", record["type"]))}
        resource: dict[str, Any] = {"resourceType": resource_type, "id": record["id"]}
        if resource_type == "Observation":
            resource.update(
                {
                    "subject": {"reference": f"Patient/{profile['id']}"},
                    "effectiveDateTime": record["recorded_at"],
                    "code": code,
                    "valueString": json.dumps(
                        record["data"],
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                }
            )
        elif resource_type == "Condition":
            resource.update(
                {
                    "subject": {"reference": f"Patient/{profile['id']}"},
                    "onsetDateTime": record["recorded_at"],
                    "code": code,
                }
            )
        elif resource_type in {"AllergyIntolerance", "Immunization"}:
            resource.update(
                {
                    "patient": {"reference": f"Patient/{profile['id']}"},
                    "code" if resource_type == "AllergyIntolerance" else "vaccineCode": code,
                    "recordedDate"
                    if resource_type == "AllergyIntolerance"
                    else "occurrenceDateTime": record["recorded_at"],
                }
            )
            if resource_type == "Immunization":
                resource["status"] = "completed"
        elif resource_type == "MedicationStatement":
            resource.update(
                {
                    "subject": {"reference": f"Patient/{profile['id']}"},
                    "medicationCodeableConcept": code,
                    "effectiveDateTime": record["recorded_at"],
                    "status": "unknown",
                }
            )
        elif resource_type == "Procedure":
            resource.update(
                {
                    "subject": {"reference": f"Patient/{profile['id']}"},
                    "code": code,
                    "performedDateTime": record["recorded_at"],
                    "status": "unknown",
                }
            )
        else:
            resource.update(
                {
                    "subject": {"reference": f"Patient/{profile['id']}"},
                    "description": json.dumps(record["data"], ensure_ascii=False),
                    "date": record["recorded_at"],
                    "status": "current",
                }
            )
        resources.append(resource)
    resources.extend(
        {
            "resourceType": "Appointment",
            "id": item["id"],
            "status": {
                "scheduled": "booked",
                "canceled": "cancelled",
                "completed": "fulfilled",
            }.get(item["status"], "booked"),
            "description": item.get("title"),
            "start": item.get("starts_at"),
            "end": item.get("ends_at"),
            "participant": [{"actor": {"reference": f"Patient/{profile['id']}"}}],
        }
        for item in export["appointments"]
    )
    resources.extend(
        {
            "resourceType": "Task",
            "id": item["id"],
            "status": {
                "pending": "requested",
                "snoozed": "on-hold",
                "sent": "completed",
                "completed": "completed",
            }.get(item["status"], "cancelled"),
            "description": item["title"],
            "for": {"reference": f"Patient/{profile['id']}"},
            "executionPeriod": {"end": item["due_at"]},
        }
        for item in export["reminders"]
    )
    resources.extend(
        {
            "resourceType": "Goal",
            "id": item["id"],
            "lifecycleStatus": {
                "active": "active",
                "completed": "completed",
            }.get(item["status"], "cancelled"),
            "description": {"text": item["title"]},
            "subject": {"reference": f"Patient/{profile['id']}"},
            "target": (
                [{"measure": {"text": item["unit"]}, "detailString": item["target_value"]}]
                if item["target_value"] is not None
                else []
            ),
        }
        for item in export["goals"]
    )
    resources.extend(
        {
            "resourceType": "Observation",
            "id": item["id"],
            "subject": {"reference": f"Patient/{profile['id']}"},
            "effectiveDateTime": f"{item['logged_on']}T00:00:00Z",
            "code": {"text": f"Goal log {item['goal_id']}"},
            "valueString": item["value"],
        }
        for item in export["goal_logs"]
    )
    resources.extend(
        {
            "resourceType": "RelatedPerson",
            "id": item["id"],
            "patient": {"reference": f"Patient/{profile['id']}"},
            "relationship": [{"text": item["relationship"]}],
            "name": [{"text": item["name"]}],
            "telecom": [
                {"system": "phone", "value": item["phone_number"]},
                *([{"system": "email", "value": item["email"]}] if item["email"] else []),
            ],
        }
        for item in export["emergency_contacts"]
    )
    bundle = {
        "resourceType": "Bundle",
        "type": "collection",
        "timestamp": export["exported_at"],
        "entry": [{"resource": resource} for resource in resources],
    }
    return JSONResponse(bundle, headers={"Cache-Control": "no-store"})


@router.get("/{profile_id}/export.pdf")
def export_pdf(
    profile_id: UUID,
    request: Request,
    authenticated: CurrentAuthenticatedSession,
    session: DbSession,
) -> StreamingResponse:
    export = _profile_export(profile_id, authenticated.user, request, session)
    buffer = BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter, title="Health record export")
    styles = getSampleStyleSheet()
    font_name = "BackendVera"
    if font_name not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        if not font_path.is_file():
            raise RuntimeError("Bundled PDF font is unavailable")
        pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    for style_name in ("Title", "Heading2", "Normal", "BodyText"):
        styles[style_name].fontName = font_name
    story = [
        Paragraph("Health Record Export", styles["Title"]),
        Paragraph(f"Profile: {_escape(export['profile']['display_name'])}", styles["Heading2"]),
        Paragraph(f"Exported: {export['exported_at']}", styles["Normal"]),
        Spacer(1, 12),
        Paragraph("Health records", styles["Heading2"]),
    ]
    if export["health_records"]:
        for record in export["health_records"]:
            story.append(
                Paragraph(
                    f"{_escape(record['type'])} — {record['recorded_at']}: "
                    f"{_escape(str(record['data']))}",
                    styles["BodyText"],
                )
            )
            story.append(Spacer(1, 6))
    else:
        story.append(Paragraph("No accessible health records.", styles["BodyText"]))
    for section_name, collection in export.items():
        if section_name in {"exported_at", "profile", "health_records"} or not collection:
            continue
        story.extend(
            [
                Spacer(1, 10),
                Paragraph(_escape(section_name.replace("_", " ").title()), styles["Heading2"]),
            ]
        )
        if isinstance(collection, list):
            for item in collection:
                story.append(
                    Paragraph(
                        _escape(json.dumps(item, ensure_ascii=False, default=str)),
                        styles["BodyText"],
                    )
                )
                story.append(Spacer(1, 4))
    document.build(story)
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="application/pdf",
        headers={
            "Cache-Control": "no-store",
            "Content-Disposition": f'attachment; filename="health-record-{profile_id}.pdf"',
        },
    )


def _escape(value: str) -> str:
    return value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
