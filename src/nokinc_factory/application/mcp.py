"""Authenticated construction of the read-only developer MCP surface."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from nokinc_factory.application.service import WorkflowStore


def create_readonly_mcp(*, store: WorkflowStore, tenant_id: str) -> MCPServer:
    """Create an MCP server with tenant identity bound outside tool arguments.

    The authenticated API must construct this server only after verifying the
    caller's tenant. No tool accepts a tenant selector, mutation, approval,
    merge, deployment, secret, or credential argument.
    """
    if not tenant_id.strip():
        raise ValueError("tenant binding is required")
    server = MCPServer(
        name="nokinc-factory-readonly",
        description="Read-only workflow status and evidence for one authenticated tenant.",
    )

    @server.tool(
        name="workflow.list",
        description="List workflow items belonging to the authenticated tenant.",
        structured_output=True,
    )
    async def list_workflows() -> dict[str, Any]:
        return {"items": [item.model_dump(mode="json") for item in store.list(tenant_id=tenant_id)]}

    @server.tool(
        name="workflow.get",
        description="Read one workflow item belonging to the authenticated tenant.",
        structured_output=True,
    )
    async def get_workflow(work_item_id: str) -> dict[str, Any]:
        item = store.get(tenant_id=tenant_id, work_item_id=work_item_id)
        if item is None:
            return {"error": "work item not found"}
        return item.model_dump(mode="json")

    @server.tool(
        name="workflow.trace",
        description="Read the append-only workflow event trace for one work item.",
        structured_output=True,
    )
    async def trace_workflow(work_item_id: str) -> dict[str, Any]:
        item = store.get(tenant_id=tenant_id, work_item_id=work_item_id)
        if item is None:
            return {"error": "work item not found"}
        return {
            "work_item_id": work_item_id,
            "events": [
                event.model_dump(mode="json")
                for event in store.trace(tenant_id=tenant_id, work_item_id=work_item_id)
            ],
        }

    return server
