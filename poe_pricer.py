"""PoE Pricer MCP Server — local item pricing, zero network calls.

For magic/rare items: uses the rare_scorer.py algorithm.
For everything else (uniques, currency, gems, divination cards, etc.):
  queries the local poe.ninja SQLite snapshot DB at C:\\poe\\price_history.db.

Tools:
  price_item   — price a single item (PoE API dict OR clipboard text)
  price_items  — price a batch of items (array of PoE API dicts)

Version: 1.0
"""
import importlib
import json
import sqlite3
import sys
from pathlib import Path

# Add poe_monitor to path for rare_scorer
POE_MONITOR_DIR = Path(r"c:\src\buildstuff\poe_monitor")
POE_DIR = Path(r"c:\poe")
sys.path.insert(0, str(POE_MONITOR_DIR))
sys.path.insert(0, str(POE_DIR))

import importlib.util as _iutil

_SCORER_PATH = POE_MONITOR_DIR / "rare_scorer.py"


def _scorer():
    """Return rare_scorer module, loading fresh from source each call so edits take effect immediately."""
    spec = _iutil.spec_from_file_location("rare_scorer", _SCORER_PATH)
    mod = _iutil.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app = Server("poe-pricer")

DB_PATH = Path(r"C:\poe\price_history.db")


# ── Ninja DB lookup ────────────────────────────────────────────────────────────

def _ninja_price(name: str) -> dict | None:
    """Look up the most recent chaos value for an item by name from local DB.

    Returns {"chaos_value": float, "category": str} or None if not found.
    """
    if not DB_PATH.exists():
        return None
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        row = con.execute(
            "SELECT category, chaos_value FROM price_history"
            " WHERE name=? ORDER BY fetched_at DESC LIMIT 1",
            (name,),
        ).fetchone()
        con.close()
        if row:
            return {"chaos_value": row["chaos_value"], "category": row["category"]}
    except Exception:
        pass
    return None


def _ninja_search(name: str) -> list[dict]:
    """Fuzzy search for item names containing the query string."""
    if not DB_PATH.exists():
        return []
    try:
        con = sqlite3.connect(DB_PATH)
        con.row_factory = sqlite3.Row
        rows = con.execute(
            """
            SELECT name, category, chaos_value
            FROM price_history
            WHERE name LIKE ? COLLATE NOCASE
            GROUP BY name
            HAVING fetched_at = MAX(fetched_at)
            ORDER BY chaos_value DESC
            LIMIT 10
            """,
            (f"%{name}%",),
        ).fetchall()
        con.close()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── Frame type constants ────────────────────────────────────────────────────────
# 0=Normal, 1=Magic, 2=Rare, 3=Unique, 4=Gem, 5=Currency, 6=DivinationCard,
# 7=Quest, 8=Prophecy, 9=Foil (special unique)

FRAME_RARE = 2
FRAME_MAGIC = 1
ALGO_FRAMES = {FRAME_MAGIC, FRAME_RARE}
NINJA_FRAMES = {3, 4, 5, 6, 9}


def _price_single_api_item(item: dict) -> dict:
    """Price one PoE API item dict. Returns a result dict."""
    frame = item.get("frameType", 0)
    name = item.get("name", "").strip()
    type_line = item.get("typeLine", "").strip()
    display_name = f"{name} {type_line}".strip() if name else type_line
    ilvl = item.get("ilvl", 0)

    if frame in ALGO_FRAMES:
        # Use local scoring algorithm for magic/rare items
        rs = _scorer()
        result = rs.score_item(item)
        if result is None:
            return {
                "name": display_name,
                "ilvl": ilvl,
                "rarity": "Magic" if frame == FRAME_MAGIC else "Rare",
                "method": "algo",
                "price_estimate": 0,
                "note": "Could not score item",
            }
        out = {
            "name": result.name or display_name,
            "ilvl": result.ilvl,
            "rarity": "Magic" if frame == FRAME_MAGIC else "Rare",
            "method": "algo",
            "category": result.category,
            "price_estimate": result.price_estimate,
            "total_score": result.total_score,
            "good_mods": result.good_mod_count,
            "junk_mods": result.junk_count,
            "breakdown": result.breakdown,
        }
        if result.is_fractured:
            out["fractured"] = True
            out["should_trade_check"] = result.should_trade_check
        return out

    # For everything else try the ninja DB by display name
    lookup_name = name if name else type_line
    ninja = _ninja_price(lookup_name)

    rarity_map = {0: "Normal", 1: "Magic", 2: "Rare", 3: "Unique",
                  4: "Gem", 5: "Currency", 6: "DivinationCard", 9: "Unique (Foil)"}

    if ninja:
        return {
            "name": display_name,
            "ilvl": ilvl,
            "rarity": rarity_map.get(frame, "Unknown"),
            "method": "ninja_db",
            "category": ninja["category"],
            "price_estimate": round(ninja["chaos_value"], 2),
        }

    # Fallback: return unpriced
    return {
        "name": display_name,
        "ilvl": ilvl,
        "rarity": rarity_map.get(frame, "Unknown"),
        "method": "not_found",
        "price_estimate": None,
        "note": f"'{lookup_name}' not in local ninja DB",
    }


# ── Tool definitions ────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="price_item",
        description=(
            "Price a single item. Accepts either:\n"
            "  • item_dict: a PoE API item object (as returned by the stash or character API)\n"
            "  • item_text: raw clipboard text from PoE (Ctrl+C)\n"
            "Magic/rare items are scored by the local algorithm. "
            "Uniques, gems, currency, and divination cards are looked up from the local "
            "poe.ninja snapshot database. No network calls are made."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "item_dict": {
                    "type": "object",
                    "description": "PoE API item dict (has frameType, typeLine, explicitMods, etc.)",
                },
                "item_text": {
                    "type": "string",
                    "description": "Raw item text copied from PoE (Ctrl+C format).",
                },
            },
        },
    ),
    Tool(
        name="price_items",
        description=(
            "Price a batch of items in one call. Accepts an array of PoE API item dicts "
            "(e.g., all items from a stash tab). Returns results sorted by price (highest first). "
            "Magic/rare items use the local algorithm; everything else uses the local poe.ninja DB. "
            "No network calls."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": {"type": "object"},
                    "description": "Array of PoE API item dicts.",
                },
                "min_price": {
                    "type": "number",
                    "description": "Only include items with price_estimate >= this value (default 0 = all).",
                },
                "include_unpriced": {
                    "type": "boolean",
                    "description": "Include items that couldn't be priced (default false).",
                },
            },
            "required": ["items"],
        },
    ),
    Tool(
        name="ninja_lookup",
        description=(
            "Look up the current poe.ninja price for a named item (exact or fuzzy match). "
            "Useful for uniques, currency, gems, divination cards, etc. No network calls — "
            "reads from the local snapshot DB."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Item name to look up (partial match supported).",
                },
            },
            "required": ["name"],
        },
    ),
]


# ── Tool handler ────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "price_item":
            item_dict = arguments.get("item_dict")
            item_text = arguments.get("item_text")

            if item_dict:
                _scorer()  # reload before pricing
                result = _price_single_api_item(item_dict)
                return [TextContent(type="text", text=json.dumps(result, indent=2))]

            elif item_text:
                # Try algo for magic/rare text
                rs = _scorer()
                algo_result = rs.score_item_text(item_text)
                if algo_result is not None:
                    out = {
                        "name": algo_result.name,
                        "ilvl": algo_result.ilvl,
                        "method": "algo",
                        "category": algo_result.category,
                        "price_estimate": algo_result.price_estimate,
                        "total_score": algo_result.total_score,
                        "good_mods": algo_result.good_mod_count,
                        "junk_mods": algo_result.junk_count,
                        "breakdown": algo_result.breakdown,
                    }
                    if algo_result.is_fractured:
                        out["fractured"] = True
                        out["should_trade_check"] = algo_result.should_trade_check
                    return [TextContent(type="text", text=json.dumps(out, indent=2))]

                # Try ninja lookup by parsing item name from text
                # First line after "Rarity: X" is the name
                lines = [l.strip() for l in item_text.strip().splitlines() if l.strip()]
                lookup_name = None
                for i, line in enumerate(lines):
                    if line.lower().startswith("rarity:"):
                        if i + 1 < len(lines):
                            lookup_name = lines[i + 1]
                        break

                if lookup_name:
                    ninja = _ninja_price(lookup_name)
                    if ninja:
                        out = {
                            "name": lookup_name,
                            "method": "ninja_db",
                            "category": ninja["category"],
                            "price_estimate": round(ninja["chaos_value"], 2),
                        }
                        return [TextContent(type="text", text=json.dumps(out, indent=2))]

                return [TextContent(type="text", text=json.dumps({
                    "note": "Could not price item. Not a magic/rare and not found in ninja DB.",
                    "name": lookup_name,
                }, indent=2))]

            else:
                return [TextContent(type="text", text="Error: provide item_dict or item_text")]

        elif name == "price_items":
            items = arguments.get("items", [])
            min_price = arguments.get("min_price", 0)
            include_unpriced = arguments.get("include_unpriced", False)

            _scorer()  # reload once before batch
            results = []
            for item in items:
                r = _price_single_api_item(item)
                price = r.get("price_estimate")
                if price is None:
                    if include_unpriced:
                        results.append(r)
                elif price >= min_price:
                    results.append(r)

            results.sort(key=lambda x: x.get("price_estimate") or 0, reverse=True)

            total_priced = sum(1 for r in results if r.get("price_estimate") is not None)
            total_value = sum(r.get("price_estimate") or 0 for r in results)
            trade_check = [r["name"] for r in results if r.get("should_trade_check")]

            out = {
                "total_items": len(items),
                "priced_count": total_priced,
                "total_value_chaos": round(total_value, 2),
                "items": results,
            }
            if trade_check:
                out["should_trade_check"] = trade_check

            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        elif name == "ninja_lookup":
            query = arguments["name"]
            # Try exact match first
            exact = _ninja_price(query)
            if exact:
                return [TextContent(type="text", text=json.dumps({
                    "name": query,
                    "price_chaos": round(exact["chaos_value"], 2),
                    "category": exact["category"],
                    "match": "exact",
                }, indent=2))]

            # Fuzzy search
            matches = _ninja_search(query)
            if not matches:
                return [TextContent(type="text", text=json.dumps({
                    "name": query,
                    "note": "Not found in local ninja DB",
                }, indent=2))]

            return [TextContent(type="text", text=json.dumps({
                "query": query,
                "match": "fuzzy",
                "results": [
                    {"name": m["name"], "price_chaos": round(m["chaos_value"], 2), "category": m["category"]}
                    for m in matches
                ],
            }, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        import traceback
        return [TextContent(type="text", text=f"Error: {e}\n{traceback.format_exc()}")]


# ── Entry point ─────────────────────────────────────────────────────────────────

from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8486, name="poe-pricer")
