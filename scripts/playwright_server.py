from __future__ import annotations

import base64
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

_data_dir = tempfile.TemporaryDirectory(prefix="fieldnote-playwright-")
data_path = Path(_data_dir.name)
database_path = (data_path / "playwright.sqlite3").resolve().as_posix()

os.environ["DATABASE_URL"] = f"sqlite:///{database_path}"
os.environ["MASTER_ENCRYPTION_KEY"] = base64.b64encode(bytes(range(32))).decode("ascii")
os.environ["SOS_SIGNING_KEY"] = "playwright-only-signing-key-with-sufficient-length"
os.environ["ATTACHMENT_STORAGE_PATH"] = str(data_path / "attachments")
os.environ["NOTIFICATION_LOG_PATH"] = str(data_path / "notifications.log")

import app.db.models  # noqa: E402,F401
from app.db.base import Base  # noqa: E402
from app.db.seed import seed_reference_data  # noqa: E402
from app.db.session import SessionLocal, engine  # noqa: E402

Base.metadata.create_all(bind=engine)
with SessionLocal() as session:
    seed_reference_data(session)
    session.commit()

import uvicorn  # noqa: E402

from app.main import app  # noqa: E402

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="127.0.0.1",
        port=int(os.environ.get("PLAYWRIGHT_PORT", "8123")),
        log_level="warning",
    )
