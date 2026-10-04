from collections.abc import Generator
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings

DATABASE_URL = get_settings().database_url
parsed_database_url = make_url(DATABASE_URL)
if parsed_database_url.get_backend_name() == "sqlite" and parsed_database_url.database not in (
    None,
    ":memory:",
):
    Path(parsed_database_url.database).resolve().parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    database = SessionLocal()
    try:
        yield database
    finally:
        database.close()
