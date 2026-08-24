from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, create_engine, event, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from .orm import Base


SCHEMA_REVISION = "20260824_0001"


class Database:
    """SQLAlchemy engine and short-lived transactional session factory."""

    def __init__(self, url: str, *, create_schema: bool = False) -> None:
        engine_kwargs: dict[str, object] = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {
                "check_same_thread": False,
                "timeout": 30,
            }
        self.engine: Engine = create_engine(url, **engine_kwargs)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _configure_sqlite_connection)
        self.session_factory = sessionmaker(
            bind=self.engine,
            autoflush=False,
            expire_on_commit=False,
            class_=Session,
        )
        if create_schema:
            ensure_database_parent(url)
            self._create_empty_test_schema()

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self.session_factory() as session:
            yield session

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[Session]:
        """Open a transaction, optionally reserving SQLite's write lock now.

        SQLite ignores ``SELECT FOR UPDATE``. ``BEGIN IMMEDIATE`` serializes
        lease mutations before they inspect availability; the unique database
        constraint remains the final invariant for every SQL dialect.
        """
        with self.session_factory() as session:
            try:
                if immediate and self.engine.dialect.name == "sqlite":
                    session.connection().exec_driver_sql("BEGIN IMMEDIATE")
                else:
                    session.begin()
                yield session
                session.commit()
            except BaseException:
                session.rollback()
                raise

    def ready(self) -> bool:
        try:
            with self.engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            inspector = inspect(self.engine)
            required = {
                "alembic_version",
                "users",
                "api_keys",
                "device_managers",
                "devices",
                "leases",
                "lease_requests",
                "audit_events",
            }
            if not required.issubset(set(inspector.get_table_names())):
                return False
            with self.engine.connect() as connection:
                revision = connection.scalar(
                    text("SELECT version_num FROM alembic_version")
                )
            return revision == SCHEMA_REVISION
        except Exception:
            return False

    def dispose(self) -> None:
        self.engine.dispose()

    def _create_empty_test_schema(self) -> None:
        existing = set(inspect(self.engine).get_table_names())
        if existing:
            if self.ready():
                return
            self.engine.dispose()
            raise RuntimeError(
                "Refusing to stamp a non-empty or partial database as current. "
                "Use Alembic migrations or an empty test database."
            )
        Base.metadata.create_all(self.engine)
        # Test/development convenience only. Normal deployments let Alembic
        # own this table through `central_server.manage db upgrade`.
        with self.engine.begin() as connection:
            connection.execute(
                text(
                    "CREATE TABLE alembic_version "
                    "(version_num VARCHAR(32) NOT NULL PRIMARY KEY)"
                )
            )
            connection.execute(
                text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
                {"revision": SCHEMA_REVISION},
            )


def _configure_sqlite_connection(dbapi_connection, _connection_record) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.close()


def ensure_database_parent(url: str) -> None:
    """Create a SQLite parent only for an explicit setup/schema operation."""
    parsed = make_url(url)
    if not parsed.drivername.startswith("sqlite"):
        return
    database = parsed.database
    if not database or database == ":memory:":
        return
    Path(database).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
