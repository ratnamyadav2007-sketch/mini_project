import sys
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from app.api.v1.routes.auth.service import password_hash
from app.core.config import get_settings
from app.db.models import Appointment, Family, FamilyMembership, Goal, MemberProfile, User
from app.db.session import SessionLocal
from app.security.audit import append_audit_event

DEMO_USERS = (
    ("demo.owner@example.com", "Demo Owner", "owner"),
    ("demo.family@example.com", "Demo Family", "adult"),
)


def seed_demo_family() -> None:
    configured_password = get_settings().demo_password
    password = configured_password.get_secret_value() if configured_password else ""
    if len(password) < 12:
        raise RuntimeError("Set DEMO_PASSWORD to a value of at least 12 characters")
    with SessionLocal() as session:
        users: list[User] = []
        for email, display_name, _ in DEMO_USERS:
            user = session.scalar(select(User).where(User.email == email))
            if user is None:
                user = User(
                    email=email,
                    display_name=display_name,
                    password_hash=password_hash.hash(password),
                )
                session.add(user)
                session.flush()
                session.add(
                    MemberProfile(
                        user_id=user.id,
                        display_name=display_name,
                        date_of_birth=date(1980, 1, 1),
                    )
                )
            elif user.deleted_at is not None or not user.is_active:
                raise RuntimeError(f"Demo account {email} is inactive or pending deletion")
            users.append(user)
        session.flush()
        profiles = [
            session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
            for user in users
        ]
        if any(profile is None for profile in profiles):
            raise RuntimeError("Demo account profile is missing")
        for profile in profiles:
            assert profile is not None
            if profile.date_of_birth is None:
                profile.date_of_birth = date(1980, 1, 1)
        family = session.scalar(select(Family).where(Family.name == "Sample Demo Family"))
        if family is None:
            family = Family(name="Sample Demo Family", created_by_user_id=users[0].id)
            session.add(family)
            session.flush()
        for profile, role in zip(profiles, ("owner", "adult"), strict=True):
            assert profile is not None
            membership = session.scalar(
                select(FamilyMembership).where(
                    FamilyMembership.family_id == family.id,
                    FamilyMembership.profile_id == profile.id,
                )
            )
            if membership is None:
                session.add(
                    FamilyMembership(
                        family_id=family.id,
                        profile_id=profile.id,
                        role=role,
                        status="active",
                    )
                )
            appointment = session.scalar(
                select(Appointment).where(
                    Appointment.profile_id == profile.id,
                    Appointment.title == "Demo wellness check-in",
                )
            )
            if appointment is None:
                session.add(
                    Appointment(
                        profile_id=profile.id,
                        title="Demo wellness check-in",
                        starts_at=datetime.now(UTC) + timedelta(days=14),
                        status="scheduled",
                        visibility="private",
                    )
                )
            goal = session.scalar(
                select(Goal).where(
                    Goal.profile_id == profile.id,
                    Goal.title == "Demo hydration goal",
                )
            )
            if goal is None:
                session.add(
                    Goal(
                        profile_id=profile.id,
                        title="Demo hydration goal",
                        goal_kind="water",
                        target_value=2,
                        unit="liters",
                        status="active",
                        visibility="private",
                    )
                )
        append_audit_event(
            session,
            actor_user_id=users[0].id,
            action="demo_family_seeded",
            entity_type="families",
            entity_id=family.id,
            details={"demo_only": True},
        )
        session.commit()


def main() -> None:
    try:
        seed_demo_family()
    except RuntimeError as exception:
        print(str(exception), file=sys.stderr)
        raise SystemExit(1) from exception
    print("Sample Demo Family is ready. Demo users share DEMO_PASSWORD.")


if __name__ == "__main__":
    main()
