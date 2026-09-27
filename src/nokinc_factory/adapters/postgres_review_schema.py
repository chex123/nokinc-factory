"""PostgreSQL-only, versioned A04 review persistence schema and least-privilege grants.

Call schema/provisioning functions with an administrator connection, never the
agent worker. FORCE RLS scopes application queries; it is not authentication of
a raw SQL-capable caller. The trusted service supplies the tenant per transaction.
"""

import re

from sqlalchemy import (
    BigInteger,
    Column,
    ForeignKeyConstraint,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.engine import Engine

metadata = MetaData()
COUNTERS = ("invocations", "repairs", "tokens_spent", "cost_microusd_spent",
            "tokens_reserved", "cost_microusd_reserved")
budgets = Table(
    "factory_review_budget", metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("budget_id", String(200), primary_key=True),
    Column("work_item_id", String(200), nullable=False),
    Column("limits_json", Text, nullable=False),
    *(Column(name, BigInteger, nullable=False, server_default="0") for name in COUNTERS),
    UniqueConstraint("tenant_id", "work_item_id"),
    UniqueConstraint("tenant_id", "work_item_id", "budget_id"),
)
registrations = Table(
    "factory_review_registration", metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("work_item_id", String(200), primary_key=True),
    Column("session_id", String(200), primary_key=True),
    Column("budget_id", String(200), nullable=False),
    Column("seed_digest", String(71), nullable=False),
    ForeignKeyConstraint(["tenant_id", "work_item_id", "budget_id"],
                         [f"{budgets.name}.{name}" for name in
                          ("tenant_id", "work_item_id", "budget_id")]),
)
sessions = Table(
    "factory_review_session", metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("work_item_id", String(200), primary_key=True),
    Column("session_id", String(200), primary_key=True),
    Column("document", Text, nullable=False),
    Column("digest", String(71), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("format_version", Integer, nullable=False),
    ForeignKeyConstraint(["tenant_id", "work_item_id", "session_id"],
                         [f"{registrations.name}.{name}" for name in
                          ("tenant_id", "work_item_id", "session_id")]),
)
events = Table(
    "factory_review_event", metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("work_item_id", String(200), primary_key=True),
    Column("session_id", String(200), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("kind", String(16), nullable=False),
    Column("subject_id", String(200), nullable=False),
    Column("before_digest", String(71)),
    Column("after_digest", String(71), nullable=False),
    Column("document", Text, nullable=False),
    Column("invocation_id", String(200)),
    Column("provider", String(200)),
    Column("execution_id", String(200)),
    ForeignKeyConstraint(["tenant_id", "work_item_id", "session_id"],
                         [f"{sessions.name}.{name}" for name in
                          ("tenant_id", "work_item_id", "session_id")]),
    UniqueConstraint("invocation_id"),
    UniqueConstraint("provider", "execution_id"),
)


def require_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("Review persistence requires PostgreSQL, not an emulated dialect")


def create_schema(engine: Engine) -> None:
    """Version-1 install, idempotent; future incompatible changes need migrations."""
    require_postgres(engine)
    with engine.begin() as connection:
        # Serialize concurrent first-time schema creation; no per-tenant agent grants.
        connection.execute(text("SELECT pg_advisory_xact_lock(630051004)"))
        metadata.create_all(connection)
        for table in metadata.sorted_tables:
            connection.execute(text(f"ALTER TABLE {table.name} ENABLE ROW LEVEL SECURITY"))
            connection.execute(text(f"ALTER TABLE {table.name} FORCE ROW LEVEL SECURITY"))
            found = connection.scalar(text(
                "SELECT 1 FROM pg_policies WHERE schemaname=current_schema() "
                "AND tablename=:table AND policyname='tenant_scope'"
            ), {"table": table.name})
            if not found:
                predicate = "tenant_id = nullif(current_setting('factory.tenant_id', true), '')"
                connection.execute(text(f"CREATE POLICY tenant_scope ON {table.name} "
                                        f"USING ({predicate}) WITH CHECK ({predicate})"))


def grant_worker(engine: Engine, role: str) -> None:
    """Administrator-only. Fail rather than run with an owner/bypass worker role."""
    require_postgres(engine)
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise ValueError("Invalid worker role identifier")
    quoted = engine.dialect.identifier_preparer.quote(role)
    with engine.begin() as connection:
        record = connection.execute(text(
            "SELECT rolsuper, rolbypassrls, rolcreaterole, rolcreatedb "
            "FROM pg_roles WHERE rolname=:role"
        ), {"role": role}).one()
        if record.rolsuper or record.rolbypassrls or record.rolcreaterole or record.rolcreatedb:
            raise ValueError("Worker must not bypass row-level security")
        for table in metadata.sorted_tables:
            owner = connection.scalar(text(
                "SELECT pg_get_userbyid(relowner) FROM pg_class WHERE oid=to_regclass(:table)"
            ), {"table": table.name})
            if owner == role:
                raise ValueError("Worker cannot own review tables")
            connection.execute(text(f"REVOKE ALL ON {table.name} FROM {quoted}, PUBLIC"))
            connection.execute(text(f"GRANT SELECT ON {table.name} TO {quoted}"))
        connection.execute(text(f"GRANT UPDATE ({', '.join(COUNTERS)}) "
                                f"ON {budgets.name} TO {quoted}"))
        session_columns = "document, digest, sequence, format_version"
        connection.execute(text(f"GRANT INSERT, UPDATE ({session_columns}) "
                    f"ON {sessions.name} TO {quoted}"))
        connection.execute(text(f"GRANT INSERT ON {events.name} TO {quoted}"))