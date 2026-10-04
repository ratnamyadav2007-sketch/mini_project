from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.db.models import (
    Appointment,
    Attachment,
    AuditLog,
    BadgeAward,
    DietPlan,
    EmergencyContact,
    Family,
    FamilyMembership,
    Food,
    Goal,
    HealthRecord,
    MealItem,
    MemberProfile,
    RecordType,
    ReferenceRange,
    Reminder,
    ShareGrant,
    SOSEvent,
    User,
)
from app.db.repository import Repository
from app.db.seed import DEMO_WARNING, FOODS, RECORD_TYPES, seed_reference_data


def _create_domain_rows(session: Session) -> list[object]:
    user = Repository(User).create(
        session, email="member@example.test", password_hash="test-hash", display_name="Member"
    )
    other_user = Repository(User).create(
        session, email="contact@example.test", password_hash="test-hash", display_name="Contact"
    )
    profile = Repository(MemberProfile).create(
        session, user_id=user.id, date_of_birth=date(1990, 1, 1)
    )
    family = Repository(Family).create(session, name="Household", created_by_user_id=user.id)
    membership = Repository(FamilyMembership).create(
        session, family_id=family.id, profile_id=profile.id
    )
    record_type = Repository(RecordType).create(
        session, code="weight", display_name="Weight", default_unit="kg"
    )
    health_record = Repository(HealthRecord).create(
        session,
        profile_id=profile.id,
        type="weight",
        value=Decimal("70.5"),
        unit="kg",
        recorded_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    reference_range = Repository(ReferenceRange).create(
        session,
        record_type_id=record_type.id,
        unit="kg",
        lower_bound=Decimal("1"),
        upper_bound=Decimal("200"),
        population="demo",
        source_note=DEMO_WARNING,
    )
    attachment = Repository(Attachment).create(
        session,
        profile_id=profile.id,
        uploaded_by_user_id=user.id,
        object_key="test/member/report.pdf",
        content_type="application/pdf",
        file_size_bytes=100,
        original_filename="report.pdf",
    )
    share_grant = Repository(ShareGrant).create(
        session, profile_id=profile.id, granted_to_user_id=other_user.id, permissions=["read"]
    )
    contact = Repository(EmergencyContact).create(
        session,
        profile_id=profile.id,
        name="Contact",
        relationship="friend",
        phone_number="+10000000000",
    )
    appointment = Repository(Appointment).create(
        session,
        profile_id=profile.id,
        title="Check-in",
        starts_at=datetime(2026, 2, 1, tzinfo=UTC),
    )
    reminder = Repository(Reminder).create(
        session,
        profile_id=profile.id,
        appointment_id=appointment.id,
        title="Appointment reminder",
        due_at=datetime(2026, 1, 31, tzinfo=UTC),
    )
    diet_plan = Repository(DietPlan).create(session, profile_id=profile.id, name="Example plan")
    food = Repository(Food).create(
        session,
        name="Example food",
        nutrients_per_100g={"protein_g": 2.0},
        source_note=DEMO_WARNING,
    )
    meal_item = Repository(MealItem).create(
        session,
        diet_plan_id=diet_plan.id,
        food_id=food.id,
        name="Example food",
        meal_type="lunch",
        serving_size_grams=Decimal("100"),
    )
    goal = Repository(Goal).create(session, profile_id=profile.id, title="Example goal")
    badge_award = Repository(BadgeAward).create(
        session, profile_id=profile.id, badge_code="first-entry"
    )
    sos_event = Repository(SOSEvent).create(session, profile_id=profile.id)
    audit_log = Repository(AuditLog).create(
        session,
        actor_user_id=user.id,
        action="created",
        entity_type="health_record",
        entity_id=health_record.id,
    )
    session.flush()
    return [
        user,
        profile,
        family,
        membership,
        record_type,
        health_record,
        reference_range,
        attachment,
        share_grant,
        contact,
        appointment,
        reminder,
        diet_plan,
        food,
        meal_item,
        goal,
        badge_award,
        sos_event,
        audit_log,
    ]


def test_every_model_has_uuid_primary_key_and_timestamps() -> None:
    models = (
        User,
        MemberProfile,
        Family,
        FamilyMembership,
        HealthRecord,
        Attachment,
        ShareGrant,
        EmergencyContact,
        Appointment,
        Reminder,
        DietPlan,
        MealItem,
        Goal,
        BadgeAward,
        SOSEvent,
        AuditLog,
        RecordType,
        ReferenceRange,
        Food,
    )

    for model in models:
        mapper = inspect(model)
        assert mapper.primary_key[0].type.python_type is UUID
        assert "created_at" in mapper.columns


def test_health_record_indexes_cover_profile_timestamp_and_type() -> None:
    indexes = {
        index.name: tuple(column.name for column in index.columns)
        for index in HealthRecord.__table__.indexes
    }

    assert indexes["ix_health_records_profile_id_recorded_at"] == ("profile_id", "recorded_at")
    assert ("type",) in indexes.values()


def test_repository_crud_for_every_model(db_session: Session) -> None:
    rows = _create_domain_rows(db_session)
    update_values = {
        User: {"display_name": "Updated member"},
        MemberProfile: {"timezone": "America/New_York"},
        Family: {"name": "Updated household"},
        FamilyMembership: {"role": "admin"},
        RecordType: {"display_name": "Updated weight"},
        HealthRecord: {"notes": "Updated note"},
        ReferenceRange: {"population": "updated demo"},
        Attachment: {"original_filename": "updated.pdf"},
        ShareGrant: {"permissions": ["read", "write"]},
        EmergencyContact: {"name": "Updated contact"},
        Appointment: {"title": "Updated appointment"},
        Reminder: {"title": "Updated reminder"},
        DietPlan: {"name": "Updated plan"},
        Food: {"name": "Updated example food"},
        MealItem: {"name": "Updated meal"},
        Goal: {"title": "Updated goal"},
        BadgeAward: {"badge_code": "updated-badge"},
        SOSEvent: {"status": "resolved"},
        AuditLog: {"action": "updated"},
    }

    for row in rows:
        repository = Repository(type(row))
        assert isinstance(row.id, UUID)
        assert repository.get(db_session, row.id) is row
        assert row in repository.list(db_session)
        if isinstance(row, AuditLog):
            assert repository.get(db_session, row.id) is row
            continue
        updated = repository.update(db_session, row.id, update_values[type(row)])
        assert updated is row
        assert repository.delete(db_session, row.id)
        assert repository.get(db_session, row.id) is None
        assert repository.get(db_session, row.id, include_deleted=True) is row
        assert row not in repository.list(db_session)
        assert row in repository.list(db_session, include_deleted=True)


def test_audit_logs_cannot_be_updated_or_deleted(db_session: Session) -> None:
    audit = Repository(AuditLog).create(
        db_session,
        action="read",
        entity_type="health_record",
    )
    repository = Repository(AuditLog)

    for operation in (
        lambda: repository.update(db_session, audit.id, {"action": "tampered"}),
        lambda: repository.delete(db_session, audit.id),
    ):
        try:
            operation()
        except ValueError as error:
            assert "append-only" in str(error)
        else:
            raise AssertionError("Audit log mutation must be rejected")


def test_repository_rejects_primary_key_updates(db_session: Session) -> None:
    user = Repository(User).create(
        db_session, email="update@example.test", password_hash="test-hash", display_name="User"
    )

    try:
        Repository(User).update(db_session, user.id, {"id": UUID(int=1)})
    except ValueError as error:
        assert "id" in str(error)
    else:
        raise AssertionError("Primary-key updates must be rejected")


def test_seed_data_is_idempotent_and_warns_about_demo_values(db_session: Session) -> None:
    seed_reference_data(db_session)
    seed_reference_data(db_session)

    assert db_session.query(RecordType).count() == len(RECORD_TYPES)
    assert db_session.query(ReferenceRange).count() == 3
    assert db_session.query(Food).count() == len(FOODS)
    assert all(DEMO_WARNING in item.source_note for item in db_session.query(ReferenceRange).all())
    assert all(DEMO_WARNING in item.source_note for item in db_session.query(Food).all())
