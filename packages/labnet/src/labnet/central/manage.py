from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

from alembic import command
from alembic.config import Config

from labnet.device_manager.errors import InvalidConfigurationError
from labnet.device_manager.security import validate_central_server_url

from .config import Settings
from .services.auth import UserService
from .storage.database import Database, ensure_database_parent
from .storage.json_import import import_legacy_users


_ROOT = Path(__file__).resolve().parent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="labnet-central-admin",
        description="Manage the LabNet Central Server.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        help="Settings file such as central.env, shared with labnet-central.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    db_parser = subparsers.add_parser("db", help="Run database schema operations.")
    db_parser.add_argument("action", choices=["upgrade", "current"])

    setup_parser = subparsers.add_parser(
        "setup",
        help="Migrate the database and create any missing users in one step.",
    )
    setup_parser.add_argument("--admin", action="append", default=[], metavar="NAME")
    setup_parser.add_argument("--user", action="append", default=[], metavar="NAME")
    setup_parser.add_argument(
        "--lab-pc", action="append", default=[], metavar="ID",
        help="Lab PC ID; created as a device_manager user.",
    )
    setup_parser.add_argument(
        "--write-env", type=Path, metavar="DIR",
        help="Write each new key into DIR/<name>.env instead of printing it.",
    )
    setup_parser.add_argument(
        "--server-url",
        help="Central URL the written files point at, e.g. https://192.0.2.10.",
    )
    setup_parser.add_argument(
        "--ca-file",
        help="CA certificate path to put in the written files (as seen on that computer).",
    )

    user_parser = subparsers.add_parser("user", help="Manage API users.")
    user_subparsers = user_parser.add_subparsers(dest="user_action", required=True)
    create_parser = user_subparsers.add_parser("create")
    create_parser.add_argument("username")
    create_parser.add_argument(
        "--role", choices=["admin", "user", "device_manager"], default="user"
    )
    rotate_parser = user_subparsers.add_parser("rotate-key")
    rotate_parser.add_argument("username")
    user_subparsers.add_parser("list")

    import_parser = subparsers.add_parser(
        "import-json-users", help="Import hashed users from the legacy JSON store."
    )
    import_parser.add_argument("--data-dir", type=Path, required=True)

    args = parser.parse_args(argv)
    try:
        settings = Settings.from_env(args.env_file)
    except (ValueError, FileNotFoundError) as exc:
        parser.error(str(exc))

    if args.command == "db":
        config = _alembic_config(settings.database_url)
        if args.action == "upgrade":
            command.upgrade(config, "head")
        else:
            command.current(config)
        return

    if args.command == "setup":
        if args.write_env is not None and not args.server_url:
            parser.error("--write-env requires --server-url.")
        if args.server_url:
            try:
                args.server_url = validate_central_server_url(args.server_url)
            except InvalidConfigurationError as exc:
                parser.error(f"--server-url: {exc}")
        command.upgrade(_alembic_config(settings.database_url), "head")

    database = Database(settings.database_url)
    if not database.ready():
        parser.error(
            "Database schema is not ready; run `labnet-central-admin db upgrade`."
        )
    try:
        service = UserService(database)
        if args.command == "setup":
            _setup(service, args, parser)
        elif args.command == "user" and args.user_action == "list":
            for user in service.list_users():
                created = user["created_at"]
                state = "" if user["enabled"] else "  (disabled)"
                print(f"{user['user']:<32} {user['role']:<15} {created:%Y-%m-%d}{state}")
        elif args.command == "user":
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


def _setup(
    service: UserService,
    args: argparse.Namespace,
    parser: argparse.ArgumentParser,
) -> None:
    """Create missing users; existing users and their keys are left alone."""

    wanted = (
        [(name, "admin") for name in args.admin]
        + [(name, "user") for name in args.user]
        + [(name, "device_manager") for name in args.lab_pc]
    )
    existing = {user["user"]: user["role"] for user in service.list_users()}
    to_create: list[tuple[str, str]] = []
    for name, role in wanted:
        if name not in existing:
            to_create.append((name, role))
        elif existing[name] != role:
            parser.error(f"User {name} already exists with role {existing[name]}, not {role}.")
        else:
            print(f"exists   {name} ({role}); key unchanged (use `user rotate-key` to reissue)")

    # Check every destination before creating anyone, so a failure changes nothing.
    destinations: dict[str, Path] = {}
    if args.write_env is not None:
        for name, _role in to_create:
            path = args.write_env / f"{name}.env"
            if path.exists():
                parser.error(f"{path} already exists; move it away or choose another --write-env.")
            destinations[name] = path
        args.write_env.mkdir(parents=True, exist_ok=True)

    for name, role in to_create:
        api_key = service.create_user(name, role)["api_key"]
        if name in destinations:
            destinations[name].write_text(
                _env_file_text(name, role, api_key, args.server_url, args.ca_file),
                encoding="utf-8",
            )
            print(f"created  {name} ({role}) -> {destinations[name]}")
        else:
            print(f"created  {name} ({role})  API key: {api_key}")

    if to_create and args.write_env is None:
        print("Save these keys now. Central stores only their SHA-256 hashes.")
    elif to_create:
        print("The written files contain secret API keys; copy each only to its own computer.")


def _env_file_text(
    name: str,
    role: str,
    api_key: str,
    server_url: str,
    ca_file: str | None,
) -> str:
    written = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        f"# Written by labnet-central-admin setup ({written}).",
        "# Contains a secret API key: keep it only on the computer that uses it.",
    ]
    if role == "device_manager":
        lines += [
            f"LABNET_LAB_PC_ID={name}",
            f"LABNET_SERVER_URL={server_url}",
            f"LABNET_DEVICE_MANAGER_API_KEY={api_key}",
        ]
    else:
        lines += [
            f"LABNET_SERVER_URL={server_url}",
            f"LABNET_API_KEY={api_key}",
        ]
    if ca_file:
        lines.append(f"LABNET_CA_FILE={ca_file}")
    return "\n".join(lines) + "\n"


def _alembic_config(database_url: str) -> Config:
    ensure_database_parent(database_url)
    config = Config(str(_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))
    return config


if __name__ == "__main__":
    main()
