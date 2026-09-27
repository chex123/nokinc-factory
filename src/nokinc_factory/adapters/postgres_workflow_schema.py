"""PostgreSQL schema and least-privilege grants for factory workflow intake."""

import re

from sqlalchemy import (
    Column,
    DateTime,
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
workflow_items = Table(
    "factory_workflow_item",
    metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("work_item_id", String(200), primary_key=True),
    Column("document", Text, nullable=False),
    Column("digest", String(71), nullable=False),
    Column("format_version", Integer, nullable=False),
    UniqueConstraint("tenant_id", "work_item_id"),
)
workflow_events = Table(
    "factory_workflow_event",
    metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("work_item_id", String(200), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column("event_id", String(200), nullable=False),
    Column("document", Text, nullable=False),
    Column("digest", String(71), nullable=False),
    Column("kind", String(32), nullable=False),
    Column("actor_id", String(200), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "work_item_id"],
        [f"{workflow_items.name}.tenant_id", f"{workflow_items.name}.work_item_id"],
    ),
    UniqueConstraint("event_id"),
)
workflow_outbox = Table(
    "factory_workflow_outbox",
    metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("event_id", String(200), primary_key=True),
    Column("work_item_id", String(200), nullable=False),
    Column("topic", String(200), nullable=False),
    Column("payload_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("attempts", Integer, nullable=False),
    Column("worker_id", String(200)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("content_digest", String(71), nullable=False),
    ForeignKeyConstraint(
        ["tenant_id", "work_item_id"],
        [f"{workflow_items.name}.tenant_id", f"{workflow_items.name}.work_item_id"],
    ),
    UniqueConstraint("tenant_id", "event_id"),
)
workflow_inbox = Table(
    "factory_workflow_inbox",
    metadata,
    Column("tenant_id", String(200), primary_key=True),
    Column("event_id", String(200), primary_key=True),
    Column("source", String(200), nullable=False),
    Column("payload_digest", String(71), nullable=False),
    Column("status", String(16), nullable=False),
    Column("received_at", DateTime(timezone=True), nullable=False),
    Column("processed_at", DateTime(timezone=True)),
    Column("content_digest", String(71), nullable=False),
    UniqueConstraint("tenant_id", "event_id"),
)


def require_postgres(engine: Engine) -> None:
    if engine.dialect.name != "postgresql":
        raise ValueError("Workflow persistence requires PostgreSQL, not an emulated dialect")


def create_schema(engine: Engine) -> None:
    require_postgres(engine)
    with engine.begin() as connection:
        connection.execute(text("SELECT pg_advisory_xact_lock(630051005)"))
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
                connection.execute(text(
                    f"CREATE POLICY tenant_scope ON {table.name} "
                    f"USING ({predicate}) WITH CHECK ({predicate})"
                ))


def grant_worker(engine: Engine, role: str) -> None:
    """Grant only tenant-scoped read/insert access to a nonowner worker role."""
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
                raise ValueError("Worker cannot own workflow tables")
            connection.execute(text(f"REVOKE ALL ON {table.name} FROM {quoted}, PUBLIC"))
            connection.execute(text(f"GRANT SELECT, INSERT ON {table.name} TO {quoted}"))
            if table.name == workflow_outbox.name:
                connection.execute(text(
                    f"GRANT UPDATE (status, attempts, worker_id, lease_expires_at, updated_at) "
                    f"ON {table.name} TO {quoted}"
                ))
            elif table.name == workflow_inbox.name:
                connection.execute(text(
                    f"GRANT UPDATE (status, processed_at) ON {table.name} TO {quoted}"
                ))
