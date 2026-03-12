"""
Nexus ↔ n8n Integration
Connects to n8n-MCP server (HTTP) to provide workflow automation tools.
Also provides direct n8n REST API access for simple operations.
"""
import asyncio, json, uuid
from pathlib import Path

import httpx

# n8n-MCP HTTP endpoint
N8N_MCP_URL = "http://localhost:3100/mcp"
N8N_MCP_TOKEN = "nexus-mcp-token"

_session_id = None
_initialized = False


async def _ensure_session():
    """Initialize MCP session if needed."""
    global _session_id, _initialized
    if _initialized and _session_id:
        return
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(N8N_MCP_URL, headers=_headers(), json={
            "jsonrpc": "2.0", "method": "initialize", "id": str(uuid.uuid4()),
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nexus", "version": "1.0"}
            }
        })
        _session_id = r.headers.get("mcp-session-id", "")
        # Send initialized notification
        await client.post(N8N_MCP_URL, headers=_headers(), json={
            "jsonrpc": "2.0", "method": "notifications/initialized"
        })
        _initialized = True


def _headers():
    h = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {N8N_MCP_TOKEN}",
    }
    if _session_id:
        h["mcp-session-id"] = _session_id
    return h


async def call_mcp_tool(tool_name: str, arguments: dict) -> str:
    """Call an n8n-MCP tool and return the result as string."""
    try:
        await _ensure_session()
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(N8N_MCP_URL, headers=_headers(), json={
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
                "id": str(uuid.uuid4())
            })
            # Parse SSE response
            for line in r.text.split("\n"):
                if line.startswith("data:"):
                    data = json.loads(line[5:].strip())
                    if "result" in data:
                        content = data["result"].get("content", [])
                        texts = [c.get("text", "") for c in content if c.get("type") == "text"]
                        return "\n".join(texts) if texts else json.dumps(data["result"])
                    if "error" in data:
                        return f"Error: {data['error'].get('message', str(data['error']))}"
            return r.text[:4000]
    except Exception as e:
        # Reset session on failure
        global _initialized
        _initialized = False
        return f"n8n-MCP error: {e}"


async def n8n_api(method: str, path: str, data: dict = None) -> dict:
    """Direct n8n REST API call."""
    cfg = _load_config()
    base = cfg.get("n8n_api_url", "http://localhost:5678")
    key = cfg.get("n8n_api_key", "")
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.request(method, f"{base}/api/v1{path}",
            headers={"X-N8N-API-KEY": key, "Content-Type": "application/json"},
            json=data)
        return r.json()


def _load_config():
    try:
        return json.loads((Path.home() / ".nexus" / "config.json").read_text())
    except:
        return {}


# ── Tools exposed to Nexus orchestrator ──────────────────────────────────

N8N_TOOLS = [
    {"name": "n8n_search_nodes", "description": "Search n8n automation nodes by keyword. Use to find integrations (e.g. 'slack', 'google sheets', 'webhook').",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search keyword"}}, "required": ["query"]}},

    {"name": "n8n_get_node", "description": "Get detailed info about an n8n node type. Returns properties, operations, and examples.",
     "input_schema": {"type": "object", "properties": {"nodeType": {"type": "string", "description": "Node type (e.g. 'n8n-nodes-base.slack')"}}, "required": ["nodeType"]}},

    {"name": "n8n_search_templates", "description": "Search n8n workflow templates. Find pre-built automations for common tasks.",
     "input_schema": {"type": "object", "properties": {"query": {"type": "string", "description": "Search query"}}, "required": ["query"]}},

    {"name": "n8n_create_workflow", "description": "Create a new n8n workflow. Provide name, nodes array, and connections object.",
     "input_schema": {"type": "object", "properties": {
         "name": {"type": "string"}, "nodes": {"type": "string", "description": "JSON string of nodes array"},
         "connections": {"type": "string", "description": "JSON string of connections object"}
     }, "required": ["name", "nodes", "connections"]}},

    {"name": "n8n_list_workflows", "description": "List all n8n workflows on the instance.",
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "n8n_activate_workflow", "description": "Activate or deactivate an n8n workflow by ID.",
     "input_schema": {"type": "object", "properties": {
         "workflowId": {"type": "string"}, "active": {"type": "boolean", "description": "true to activate, false to deactivate"}
     }, "required": ["workflowId", "active"]}},

    {"name": "n8n_test_workflow", "description": "Test/trigger an n8n workflow execution.",
     "input_schema": {"type": "object", "properties": {
         "workflowId": {"type": "string"}, "testData": {"type": "string", "description": "Optional JSON test data"}
     }, "required": ["workflowId"]}},

    {"name": "n8n_deploy_template", "description": "Deploy a workflow template from n8n.io to your local instance.",
     "input_schema": {"type": "object", "properties": {
         "templateId": {"type": "string", "description": "Template ID from n8n.io"}
     }, "required": ["templateId"]}},

    {"name": "n8n_health", "description": "Check n8n instance health and connectivity.",
     "input_schema": {"type": "object", "properties": {}}},

    {"name": "n8n_validate_workflow", "description": "Validate a workflow's structure, connections, and expressions.",
     "input_schema": {"type": "object", "properties": {
         "workflowId": {"type": "string"}
     }, "required": ["workflowId"]}},
]


async def execute_n8n_tool(name: str, inp: dict) -> str:
    """Route Nexus tool calls to n8n-MCP."""
    tool_map = {
        "n8n_search_nodes": ("search_nodes", lambda i: {"query": i["query"]}),
        "n8n_get_node": ("get_node", lambda i: {"nodeType": i["nodeType"]}),
        "n8n_search_templates": ("search_templates", lambda i: {"query": i["query"]}),
        "n8n_create_workflow": ("n8n_create_workflow", lambda i: {
            "name": i["name"],
            "nodes": json.loads(i["nodes"]) if isinstance(i.get("nodes"), str) else i.get("nodes", []),
            "connections": json.loads(i["connections"]) if isinstance(i.get("connections"), str) else i.get("connections", {})
        }),
        "n8n_list_workflows": ("n8n_list_workflows", lambda i: {}),
        "n8n_activate_workflow": ("n8n_update_full_workflow", lambda i: {
            "workflowId": i["workflowId"], "active": i["active"]
        }),
        "n8n_test_workflow": ("n8n_test_workflow", lambda i: {
            "workflowId": i["workflowId"],
            **({"testData": json.loads(i["testData"])} if i.get("testData") else {})
        }),
        "n8n_deploy_template": ("n8n_deploy_template", lambda i: {"templateId": i["templateId"]}),
        "n8n_health": ("n8n_health_check", lambda i: {}),
        "n8n_validate_workflow": ("n8n_validate_workflow", lambda i: {"workflowId": i["workflowId"]}),
    }

    if name not in tool_map:
        return f"Unknown n8n tool: {name}"

    mcp_name, arg_fn = tool_map[name]
    try:
        args = arg_fn(inp)
    except Exception as e:
        return f"Argument error: {e}"

    return await call_mcp_tool(mcp_name, args)
