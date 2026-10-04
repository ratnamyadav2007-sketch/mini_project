import argparse
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

from sqlalchemy.engine import make_url

from app.core.config import get_settings


def _sqlite_path(database_url: str) -> Path:
    database = make_url(database_url).database
    if not database or database == ":memory:":
        raise ValueError("Backup and restore require a file-backed database")
    return Path(database).resolve()


def _postgres_environment(database_url: str) -> tuple[dict[str, str], str]:
    url = make_url(database_url)
    if not url.database:
        raise ValueError("DATABASE_URL must include a database name")
    environment = os.environ.copy()
    if url.username:
        environment["PGUSER"] = url.username
    if url.password:
        environment["PGPASSWORD"] = url.password
    if url.host:
        environment["PGHOST"] = url.host
    if url.port:
        environment["PGPORT"] = str(url.port)
    for key, value in (url.query or {}).items():
        if key in {"sslmode", "sslrootcert", "sslcert", "sslkey", "connect_timeout"}:
            environment[f"PG{key.upper()}"] = str(value)
    return environment, url.database


def backup(destination: Path, database_url: str) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if database_url.startswith("sqlite"):
        source_path = _sqlite_path(database_url)
        if source_path == destination.resolve():
            raise ValueError("The SQLite backup destination and configured database must differ")
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination) as target:
            source.backup(target)
        return
    if database_url.startswith(("postgresql", "postgres")):
        environment, database = _postgres_environment(database_url)
        subprocess.run(
            [
                "pg_dump",
                "--format=custom",
                "--file",
                str(destination),
                "--dbname",
                database,
            ],
            env=environment,
            check=True,
        )
        return
    raise ValueError("Only SQLite and PostgreSQL databases are supported")


def restore(source_path: Path, database_url: str) -> None:
    if not source_path.is_file():
        raise FileNotFoundError(f"Backup file not found: {source_path}")
    if database_url.startswith("sqlite"):
        destination_path = _sqlite_path(database_url)
        if destination_path == source_path.resolve():
            raise ValueError("The SQLite backup source and configured database must differ")
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(source_path) as source, sqlite3.connect(destination_path) as target:
            source.backup(target)
        return
    if database_url.startswith(("postgresql", "postgres")):
        environment, database = _postgres_environment(database_url)
        subprocess.run(
            [
                "pg_restore",
                "--clean",
                "--if-exists",
                "--no-owner",
                "--dbname",
                database,
                str(source_path),
            ],
            env=environment,
            check=True,
        )
        return
    raise ValueError("Only SQLite and PostgreSQL databases are supported")


def main() -> None:
    parser = argparse.ArgumentParser(description="Back up or restore the configured database.")
    subparsers = parser.add_subparsers(dest="operation", required=True)
    backup_parser = subparsers.add_parser("backup")
    backup_parser.add_argument("destination", type=Path)
    restore_parser = subparsers.add_parser("restore")
    restore_parser.add_argument("source", type=Path)
    restore_parser.add_argument("--yes", action="store_true", help="Confirm destructive restore")
    arguments = parser.parse_args()
    database_url = get_settings().database_url
    try:
        if arguments.operation == "backup":
            backup(arguments.destination, database_url)
            print(f"Database backup written to {arguments.destination.resolve()}")
        elif not arguments.yes:
            parser.error("Restore replaces existing database contents; pass --yes to continue")
        else:
            restore(arguments.source, database_url)
            print("Database restore completed")
    except (FileNotFoundError, ValueError, subprocess.CalledProcessError) as exception:
        print(f"Database {arguments.operation} failed: {exception}", file=sys.stderr)
        raise SystemExit(1) from exception


if __name__ == "__main__":
    main()
