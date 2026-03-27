"""poe-all MCP Server — single server that bundles all PoE MCP tools.

Runs all poe-market, poe-stash, poe-trade, poe-char, poe-pricer, poe-filter,
pob-vault, and pob-brain tools under one MCP connection.

Usage:
    python poe_all.py          # stdio (add to .mcp.json as single entry)
    python poe_all.py sse      # HTTPS SSE on port 8490

Version: 1.0
"""
import sys
from pathlib import Path

# Ensure shared utils and sibling servers are importable
_MCP_DIR = str(Path(__file__).parent)
_POBRAIN_DIR = "c:/src/pobrain"
_BUILDSTUFF_DIR = "c:/src/buildstuff"
for _p in [_MCP_DIR, _POBRAIN_DIR, _BUILDSTUFF_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ── Import all sub-servers (they register tools on their own `app` instances) ─
# We import the modules to get access to their TOOLS lists and call_tool handlers,
# then re-register everything onto a single combined MCP Server.

import importlib
import json

from mcp.server import Server
from mcp.types import TextContent, Tool

combined = Server("poe-all")

# ── Sub-server registry ───────────────────────────────────────────────────────
# (module_path, prefix, port_for_reference)
_SERVERS = [
    ("poe_market",       "poe-market"),
    ("poe_stash",        "poe-stash"),
    ("poe_trade",        "poe-trade"),
    ("poe_char",         "poe-char"),
    ("poe_pricer",       "poe-pricer"),
    ("poe_filter",       "poe-filter"),
    ("pob_vault_mcp",    "pob-vault"),
]

_tool_registry: dict[str, tuple] = {}  # tool_name -> (call_tool_handler, original_name)


def _load_servers():
    """Import each sub-server and collect its tools + handler."""
    for module_name, prefix in _SERVERS:
        try:
            mod = importlib.import_module(module_name)
        except Exception as e:
            print(f"[poe-all] WARNING: could not load {module_name}: {e}", file=sys.stderr)
            continue

        # Each server registers handlers via @app.list_tools() and @app.call_tool()
        # We access the underlying handler directly from the Server's request_handlers
        sub_app = getattr(mod, "app", None)
        tools = getattr(mod, "TOOLS", [])
        if not sub_app or not tools:
            print(f"[poe-all] WARNING: {module_name} has no app or TOOLS", file=sys.stderr)
            continue

        # Get the call_tool handler registered on the sub-server
        from mcp.types import CallToolRequest
        handler = sub_app.request_handlers.get(CallToolRequest)

        for tool in tools:
            _tool_registry[tool.name] = (handler, tool.name, prefix)

        print(f"[poe-all] loaded {len(tools)} tools from {module_name}", file=sys.stderr)

    # pob-brain is special (has LuaProcess), load separately
    try:
        import server as pob_brain_mod
        sub_app = pob_brain_mod.app
        tools = pob_brain_mod.TOOLS
        from mcp.types import CallToolRequest
        handler = sub_app.request_handlers.get(CallToolRequest)
        for tool in tools:
            _tool_registry[tool.name] = (handler, tool.name, "pob-brain")
        print(f"[poe-all] loaded {len(tools)} tools from pob-brain", file=sys.stderr)
    except Exception as e:
        print(f"[poe-all] WARNING: could not load pob-brain: {e}", file=sys.stderr)


_load_servers()

# Build combined TOOLS list
ALL_TOOLS = []
for tool_name, (handler, orig_name, prefix) in _tool_registry.items():
    # Find the original Tool object to preserve schema
    # Re-search across all loaded modules
    ALL_TOOLS_NAMES = tool_name


# Rebuild by pulling Tool objects from each module
def _collect_all_tools() -> list[Tool]:
    tools_out = []
    for module_name, prefix in _SERVERS:
        try:
            mod = importlib.import_module(module_name)
            for t in getattr(mod, "TOOLS", []):
                tools_out.append(t)
        except Exception:
            pass
    try:
        import server as pob_brain_mod
        for t in pob_brain_mod.TOOLS:
            tools_out.append(t)
    except Exception:
        pass
    return tools_out


_ALL_TOOLS = _collect_all_tools()


@combined.list_tools()
async def list_tools():
    return _ALL_TOOLS


@combined.call_tool()
async def call_tool(name: str, arguments: dict):
    entry = _tool_registry.get(name)
    if not entry:
        return [TextContent(type="text", text=f"Unknown tool: {name}")]
    handler, orig_name, prefix = entry
    if handler is None:
        return [TextContent(type="text", text=f"No handler found for {name} (from {prefix})")]
    try:
        # Call the sub-server's handler directly
        from mcp.types import CallToolRequest, CallToolRequestParams
        req = CallToolRequest(
            method="tools/call",
            params=CallToolRequestParams(name=orig_name, arguments=arguments),
        )
        result = await handler(req)
        return result.root.content
    except Exception as e:
        return [TextContent(type="text", text=f"Error in {prefix}/{name}: {type(e).__name__}: {e}")]


# ── Entry point ───────────────────────────────────────────────────────────────
from mcp_server_utils import run_server

if __name__ == "__main__":
    print(f"[poe-all] {len(_ALL_TOOLS)} tools loaded across {len(_SERVERS) + 1} servers", file=sys.stderr)
    run_server(combined, port=8490, name="poe-all")
