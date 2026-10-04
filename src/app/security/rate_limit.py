import hashlib
from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, Request
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.db.models import ApiRateLimit


def enforce_rate_limit(
    session: Session,
    request: Request,
    *,
    scope: str,
    limit: int,
    window_seconds: int,
    actor_id: str | None = None,
) -> None:
    if limit < 1 or window_seconds < 1:
        raise ValueError("Rate limit and window must be positive")
    client_ip = request.client.host if request.client is not None else "unknown"
    subject = f"user:{actor_id}" if actor_id is not None else f"ip:{client_ip}"
    bucket_hash = hashlib.sha256(f"{scope}:{subject}".encode()).hexdigest()
    now = datetime.now(UTC)
    expired_before = now - timedelta(seconds=window_seconds * 2)
    session.execute(delete(ApiRateLimit).where(ApiRateLimit.window_started_at < expired_before))
    dialect = session.get_bind().dialect.name
    insert = (
        postgresql_insert(ApiRateLimit)
        if dialect == "postgresql"
        else sqlite_insert(ApiRateLimit)
        if dialect == "sqlite"
        else None
    )
    if insert is None:
        raise RuntimeError(f"Unsupported database dialect for API rate limiting: {dialect}")
    session.execute(
        insert.on_conflict_do_nothing(index_elements=["bucket_hash"]).values(
            bucket_hash=bucket_hash,
            window_started_at=now,
            request_count=0,
        )
    )
    bucket = session.scalar(
        select(ApiRateLimit).where(ApiRateLimit.bucket_hash == bucket_hash).with_for_update()
    )
    if bucket is None:
        raise RuntimeError("Failed to initialize API rate limit state")
    window_started = (
        bucket.window_started_at.replace(tzinfo=UTC)
        if bucket.window_started_at.tzinfo is None
        else bucket.window_started_at.astimezone(UTC)
    )
    if now - window_started >= timedelta(seconds=window_seconds):
        bucket.window_started_at = now
        bucket.request_count = 1
    elif bucket.request_count >= limit:
        retry_after = max(
            1,
            int((timedelta(seconds=window_seconds) - (now - window_started)).total_seconds()),
        )
        session.commit()
        raise HTTPException(
            status_code=429,
            detail="Request rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    else:
        bucket.request_count += 1
    session.commit()
