"""PoE Market MCP Server — exposes price_history.db via MCP.

Wraps price_db.py functions for querying market data collected by trend_watcher.py.

Version: 1.0
"""
import asyncio
import json
import sys
from pathlib import Path

# price_db lives in c:\poe
sys.path.insert(0, str(Path(__file__).parent.parent))
import price_db

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app = Server("poe-market")

TOOLS = [
    Tool(
        name="get_price",
        description="Get the latest price for a specific item. Returns chaos value and category.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Item name to look up (exact match, case-insensitive).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="get_price_history",
        description="Get the full price history for an item (all snapshots). Useful for seeing price trajectory over time.",
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Item name (exact match).",
                },
            },
            "required": ["name"],
        },
    ),
    Tool(
        name="search_items",
        description="Search for items by name substring. Returns latest price for each match.",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search term (substring match).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 20).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_risers",
        description="Get items with the biggest positive price increase (% change) across all snapshots.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_snapshots": {
                    "type": "integer",
                    "description": "Minimum number of price snapshots required (default 3). Higher = more reliable trends.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum current price in chaos to include (default 0). Filters out junk.",
                },
            },
        },
    ),
    Tool(
        name="get_fallers",
        description="Get items with the biggest negative price drop (% change) across all snapshots.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_snapshots": {
                    "type": "integer",
                    "description": "Minimum snapshots (default 3).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
        },
    ),
    Tool(
        name="get_movers",
        description="Get items with the biggest absolute price movement (up or down). Good for finding volatile items.",
        inputSchema={
            "type": "object",
            "properties": {
                "min_snapshots": {
                    "type": "integer",
                    "description": "Minimum snapshots (default 3).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 25).",
                },
            },
        },
    ),
    Tool(
        name="snapshot_status",
        description="Get info about the price database: total snapshots, latest fetch time, total items tracked.",
        inputSchema={"type": "object", "properties": {}},
    ),
]


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "get_price":
            item_name = arguments["name"]
            results = price_db.search_items(item_name, limit=10)
            # Try exact match first
            exact = [r for r in results if r["name"].lower() == item_name.lower()]
            if exact:
                return [TextContent(type="text", text=json.dumps(exact[0], indent=2))]
            if results:
                return [TextContent(type="text", text=json.dumps(results[0], indent=2))]
            return [TextContent(type="text", text=f"No item found matching '{item_name}'")]

        elif name == "get_price_history":
            history = price_db.get_history(arguments["name"])
            if not history:
                # Try case-insensitive search
                matches = price_db.search_items(arguments["name"], limit=1)
                if matches:
                    history = price_db.get_history(matches[0]["name"])
            if not history:
                return [TextContent(type="text", text=f"No history for '{arguments['name']}'")]
            return [TextContent(type="text", text=json.dumps(history, indent=2))]

        elif name == "search_items":
            limit = arguments.get("limit", 20)
            results = price_db.search_items(arguments["query"], limit=limit)
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_risers":
            min_snaps = arguments.get("min_snapshots", 3)
            limit = arguments.get("limit", 25)
            min_price = arguments.get("min_price", 0)
            results = price_db.get_risers(min_snaps=min_snaps, limit=limit * 2)
            if min_price > 0:
                results = [r for r in results if r["last_price"] >= min_price][:limit]
            else:
                results = results[:limit]
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_fallers":
            min_snaps = arguments.get("min_snapshots", 3)
            limit = arguments.get("limit", 25)
            results = price_db.get_fallers(min_snaps=min_snaps, limit=limit)
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "get_movers":
            min_snaps = arguments.get("min_snapshots", 3)
            limit = arguments.get("limit", 25)
            results = price_db.get_movers(min_snaps=min_snaps, limit=limit)
            return [TextContent(type="text", text=json.dumps(results, indent=2))]

        elif name == "snapshot_status":
            snap_count = price_db.snapshot_count()
            times = price_db.get_snapshot_times()
            latest = times[0] if times else "never"
            oldest = times[-1] if times else "never"
            all_latest = price_db.get_all_latest()
            result = {
                "total_snapshots": snap_count,
                "latest_fetch": latest,
                "oldest_fetch": oldest,
                "items_tracked": len(all_latest),
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8481, name="poe-market")
