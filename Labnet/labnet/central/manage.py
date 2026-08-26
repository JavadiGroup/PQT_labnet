from __future__ import annotations

import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from .config import Settings
from .services.auth import UserService
from .storage.database import Database, ensure_database_parent
from .storage.json_import import import_legacy_users


_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage the LabNet Central Server.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Run database schema operations.")
    db_parser.add_argument("action", choices=["upgrade", "current"])

    user_parser = subparsers.add_parser("user", help="Manage API users.")
    user_subparsers = user_parser.add_subparsers(dest="user_action", required=True)
    create_parser = user_subparsers.add_parser("create")
    create_parser.add_argument("username")
    create_parser.add_argument(
        "--role", choices=["admin", "user", "device_manager"], default="user"
    )
    rotate_parser = user_subparsers.add_parser("rotate-key")
    rotate_parser.add_argument("username")

    import_parser = subparsers.add_parser(
        "import-json-users", help="Import hashed users from the legacy JSON store."
    )
    import_parser.add_argument("--data-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    settings = Settings.from_env()

    if args.command == "db":
        config = _alembic_config(settings.database_url)
        if args.action == "upgrade":
            command.upgrade(config, "head")
        else:
            command.current(config)
        return

    database = Database(settings.database_url)
    if not database.ready():
        parser.error(
            "Database schema is not ready; run `labnet-central-admin db upgrade`."
        )
    try:
        if args.command == "user":
            service = UserService(database)
            if args.user_action == "create":
                result = service.create_user(args.username, args.role)
            else:
                result = service.rotate_api_key(args.username)
            print(f"User: {result['user']}")
            if "role" in result:
                print(f"Role: {result['role']}")
            print(f"API key: {result['api_key']}")
            print("Save this key now. Central stores only its SHA-256 hash.")
        elif args.command == "import-json-users":
            imported, skipped = import_legacy_users(database, args.data_dir.resolve())
            print(f"Imported users: {imported}; skipped records: {skipped}")
            print("Legacy WebSocket devices and leases were not imported.")
    finally:
        database.dispose()


def _alembic_config(database_url: str) -> Config:
    ensure_database_parent(database_url)
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


if __name__ == "__main__":
    main()
