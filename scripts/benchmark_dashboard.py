import math
import os
import time

from fastapi.testclient import TestClient
from sqlalchemy import select

from app.db.models import Family, MemberProfile, User
from app.db.session import SessionLocal
from app.main import app


def main() -> None:
    email = os.environ.get("DEMO_EMAIL", "demo.owner@example.com")
    password = os.environ.get("DEMO_PASSWORD", "")
    if len(password) < 12:
        raise SystemExit("Set DEMO_PASSWORD to the password used by backend-seed-demo")
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.email == email, User.deleted_at.is_(None)))
        if user is None:
            raise SystemExit(f"Demo account {email} was not found; seed the demo family first")
        profile = session.scalar(select(MemberProfile).where(MemberProfile.user_id == user.id))
        if profile is None:
            raise SystemExit("The demo account has no member profile")
        family = session.scalar(
            select(Family).where(
                Family.created_by_user_id == user.id,
                Family.deleted_at.is_(None),
            )
        )
        if family is None:
            raise SystemExit("The demo account has no active family")
        family_id = family.id

    with TestClient(app) as client:
        challenge = client.get("/api/v1/auth/csrf")
        challenge.raise_for_status()
        login = client.post(
            "/api/v1/auth/login",
            headers={"X-CSRF-Token": challenge.json()["csrf_token"]},
            json={"email": email, "password": password},
        )
        login.raise_for_status()
        samples: list[float] = []
        for _ in range(30):
            started = time.perf_counter()
            response = client.get(f"/api/v1/families/{family_id}/dashboard")
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.raise_for_status()
            samples.append(elapsed_ms)
        p95_ms = sorted(samples)[math.ceil(len(samples) * 0.95) - 1]

    target_ms = float(os.environ.get("BENCHMARK_P95_TARGET_MS", "500"))
    print(f"Family dashboard p95: {p95_ms:.1f} ms (target: < {target_ms:.0f} ms)")
    if p95_ms >= target_ms:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
