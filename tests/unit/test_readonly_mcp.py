import asyncio
from datetime import UTC, datetime

from nokinc_factory.application.mcp import create_readonly_mcp
from nokinc_factory.application.service import InMemoryWorkflowStore


def test_mcp_exposes_only_tenant_bound_read_tools() -> None:
    store = InMemoryWorkflowStore()
    item = store.create_intake(
        tenant_id="tenant-a", actor_id="user-a", now=datetime.now(UTC),
        message="build refunds",
    )
    server = create_readonly_mcp(store=store, tenant_id="tenant-a")
    tools = asyncio.run(server.list_tools())

    assert {tool.name for tool in tools} == {
        "workflow.list", "workflow.get", "workflow.trace",
    }
    result = asyncio.run(server.call_tool(
        "workflow.get", {"work_item_id": item.work_item_id},
    ))
    assert "tenant-a" in str(result)


def test_mcp_tenant_binding_cannot_read_another_tenant() -> None:
    store = InMemoryWorkflowStore()
    item = store.create_intake(
        tenant_id="tenant-a", actor_id="user-a", now=datetime.now(UTC),
        message="build refunds",
    )
    server = create_readonly_mcp(store=store, tenant_id="tenant-b")

    result = asyncio.run(server.call_tool(
        "workflow.get", {"work_item_id": item.work_item_id},
    ))
    assert "not found" in str(result).lower()
