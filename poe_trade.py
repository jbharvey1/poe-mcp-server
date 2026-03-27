"""PoE Trade MCP Server — search and fetch from pathofexile.com/api/trade.

General-purpose trade API wrapper for any item type, not just wands.

Version: 1.0
"""
import asyncio
import json
import re
import sys
import time
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

TRADE_BASE = "https://www.pathofexile.com/api/trade"
HEADERS = {
    "Content-Type": "application/json",
    "User-Agent": "OAuth BoschAIMaster/1.0 (contact: buildtool@localhost)",
    "Accept": "application/json",
}

# Default league — overridden by tool argument
DEFAULT_LEAGUE = "Mirage"


def _load_headers():
    """Return request headers, adding POESESSID cookie from config if available."""
    h = dict(HEADERS)
    config_paths = [
        Path("C:/src/buildstuff/poe_monitor/config.json"),
        Path(__file__).parent.parent / "buildstuff" / "poe_monitor" / "config.json",
    ]
    for p in config_paths:
        try:
            cfg = json.loads(p.read_text())
            sessid = cfg.get("poesessid", "")
            if sessid:
                h["Cookie"] = "POESESSID=" + sessid
                break
        except Exception:
            pass
    return h


app = Server("poe-trade")
print("[poe-trade] SERVER START — instant buyout ALWAYS enforced (sale_type: priced)", file=sys.stderr)

MAX_RETRIES = 4
RETRY_WAITS = [10, 20, 30]  # seconds between attempts 1-2, 2-3, 3-4


def _post_json(url, payload):
    """POST JSON and return response dict. Retries up to MAX_RETRIES times on 429/400."""
    headers = _load_headers()
    data = json.dumps(payload).encode("utf-8")
    print("[poe-trade] POST " + url, file=sys.stderr)
    print("[poe-trade] payload: " + json.dumps(payload)[:400], file=sys.stderr)
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                wait = RETRY_WAITS[attempt]
                print(f"[poe-trade] HTTP 429 attempt {attempt+1}, retrying in {wait}s", file=sys.stderr)
                time.sleep(wait)
                continue
            raise


def _get_json(url):
    """GET and return response dict. Retries up to MAX_RETRIES times on 429."""
    headers = _load_headers()
    for attempt in range(MAX_RETRIES):
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_WAITS[attempt])
                continue
            raise


TOOLS = [
    Tool(
        name="search_trade",
        description=(
            "Search the PoE trade site for items matching filters. "
            "Returns up to 10 results with prices and mods. "
            "Also returns a clickable trade URL."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "league": {
                    "type": "string",
                    "description": "League name (default: " + DEFAULT_LEAGUE + ").",
                },
                "category": {
                    "type": "string",
                    "description": (
                        "Item category filter. Examples: 'weapon.wand', 'weapon.staff', "
                        "'armour.body', 'armour.helmet', 'armour.boots', 'armour.gloves', "
                        "'armour.shield', 'accessory.ring', 'accessory.amulet', 'accessory.belt', "
                        "'jewel', 'gem'. Optional."
                    ),
                },
                "rarity": {
                    "type": "string",
                    "description": "Rarity filter: 'nonunique', 'unique', 'any' (default: 'any').",
                },
                "name": {
                    "type": "string",
                    "description": "Item name to search for (for uniques). Optional.",
                },
                "base_type": {
                    "type": "string",
                    "description": "Base type filter (e.g., 'Opal Wand', 'Astral Plate'). Optional.",
                },
                "stats": {
                    "type": "array",
                    "description": (
                        "Stat filters. Each entry: {id, min, max}. "
                        "Common stat IDs: "
                        "'pseudo.pseudo_total_life' (total life), "
                        "'pseudo.pseudo_total_elemental_resistance' (total ele res), "
                        "'explicit.stat_210067635' (local attack speed), "
                        "'explicit.stat_2974417149' (spell damage), "
                        "'explicit.stat_3336890334' (local lightning dmg), "
                        "'explicit.stat_1940865751' (local phys dmg). "
                        "Use get_stat_ids tool to find specific stat IDs."
                    ),
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string"},
                            "min": {"type": "number"},
                            "max": {"type": "number"},
                        },
                        "required": ["id"],
                    },
                },
                "min_price": {
                    "type": "number",
                    "description": "Minimum price in chaos orbs. Optional. Set this when searching for non-trivial items to prevent cheap listings from selling before the fetch completes.",
                },
                "max_price": {
                    "type": "number",
                    "description": "Maximum price in chaos orbs. Optional.",
                },
                "max_level": {
                    "type": "integer",
                    "description": "Maximum level requirement. Optional.",
                },
                "instant_buyout": {
                    "type": "boolean",
                    "description": "Only show priced (instant buyout) listings (default true when max_price is set).",
                },
                "online_only": {
                    "type": "boolean",
                    "description": "Only show online sellers (default true).",
                },
                "account": {
                    "type": "string",
                    "description": "Filter by seller account name (e.g., 'buddies1296#3898'). Optional.",
                },
                "min_links": {
                    "type": "number",
                    "description": "Minimum number of linked sockets (e.g. 6 for six-linked). Optional.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results to fetch (default 10, max 20).",
                },
            },
        },
    ),
    Tool(
        name="get_stat_ids",
        description=(
            "Search for trade stat filter IDs by keyword. "
            "Use this to find the correct stat ID for search_trade filters. "
            "Returns matching stat IDs with their display text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Keyword to search for (e.g., 'attack speed', 'fire resistance', 'spell damage').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max results (default 10).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="search_by_item_mods",
        description=(
            "Search trade for an item by its mod texts — no stat IDs needed. "
            "Pass mod lines as human-readable text (e.g. '+92 to maximum Life', "
            "'17% increased Attack Speed'). Handles local weapon mods automatically. "
            "For uniques pass unique_name instead of mods."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "mods": {
                    "type": "array",
                    "description": "List of mod objects. Each: {text: str, is_local: bool (default false), min_pct: float (default 0.7)}.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string"},
                            "is_local": {"type": "boolean"},
                            "min_pct": {"type": "number"},
                        },
                        "required": ["text"],
                    },
                },
                "item_category": {
                    "type": "string",
                    "description": "Trade category, e.g. 'weapon.wand', 'armour.chest', 'accessory.ring'.",
                },
                "unique_name": {
                    "type": "string",
                    "description": "For unique items: search by name instead of mods.",
                },
                "league": {"type": "string"},
                "limit": {"type": "integer"},
            },
        },
    ),
    Tool(
        name="fetch_listing",
        description="Fetch detailed info for specific trade listing IDs (from a previous search).",
        inputSchema={
            "type": "object",
            "properties": {
                "query_id": {
                    "type": "string",
                    "description": "The query ID from a previous search_trade result.",
                },
                "listing_ids": {
                    "type": "string",
                    "description": "Comma-separated listing IDs to fetch (max 10), e.g. 'id1,id2'.",
                },
            },
            "required": ["query_id", "listing_ids"],
        },
    ),
]


# Cache stats data (fetched once)
_stats_cache = None


def _get_stats():
    global _stats_cache
    if _stats_cache is not None:
        return _stats_cache
    data = _get_json(TRADE_BASE + "/data/stats")
    all_stats = []
    for group in data.get("result", []):
        for entry in group.get("entries", []):
            all_stats.append({
                "id": entry.get("id", ""),
                "text": entry.get("text", ""),
                "type": entry.get("type", group.get("label", "")),
            })
    _stats_cache = all_stats
    return all_stats


# ─── Mod-text → stat-ID lookup (shared with item roller) ────────────────────

_stats_index_by_pattern = None


def _normalize_stat(text):
    """Normalize mod/stat text for fuzzy matching: numbers→#, lowercase, strip +."""
    text = text.lower()
    text = re.sub(r'\d+(?:\.\d+)?', '#', text)
    text = re.sub(r'#(?:\s*to\s*)#', '#', text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.lstrip('+')
    return text


def _build_stats_index():
    """Build and cache a pattern→[(stat_id, label)] index from the trade stats API."""
    global _stats_index_by_pattern
    if _stats_index_by_pattern is not None:
        return _stats_index_by_pattern
    try:
        data = _get_json(TRADE_BASE + "/data/stats")
        idx = {}
        for group in data.get("result", []):
            label = group.get("label", "")
            if label not in ("Explicit", "Pseudo", "Delve", "Fractured", "Implicit"):
                continue
            for entry in group.get("entries", []):
                sid = entry.get("id", "")
                text = entry.get("text", "")
                if not sid or not text:
                    continue
                pat = _normalize_stat(text)
                idx.setdefault(pat, []).append((sid, label))
        _stats_index_by_pattern = idx
    except Exception:
        _stats_index_by_pattern = {}
    return _stats_index_by_pattern


def mod_text_to_stat_id(mod_text, is_local=False):
    """Return (stat_id, numeric_value) for a rolled mod text, or None.
    is_local: if True, try '... (local)' pattern first (weapon local mods)."""
    idx = _build_stats_index()
    nums = re.findall(r'\d+(?:\.\d+)?', mod_text)
    value = float(nums[-1]) if nums else 0
    pat = _normalize_stat(mod_text)

    if is_local:
        candidates = idx.get(pat + " (local)", [])
        for sid, label in candidates:
            if label == "Explicit":
                return sid, value
        if candidates:
            return candidates[0][0], value

    candidates = idx.get(pat, [])
    for sid, label in candidates:
        if label == "Explicit":
            return sid, value
    if candidates:
        return candidates[0][0], value
    return None


def _build_search_payload(arguments):
    """Build trade API search payload from tool arguments."""
    query = {}

    # Status — always "securable" = Travel to Hideout / Instant Buyout.
    # This is what drives the "Travel to Hideout" button on the trade site.
    query["status"] = {"option": "securable"}

    # Name / type
    if arguments.get("name"):
        query["name"] = arguments["name"]
    if arguments.get("base_type"):
        query["type"] = arguments["base_type"]

    # Stats — parse defensively in case MCP delivers as JSON string
    stats_arg = arguments.get("stats", [])
    if isinstance(stats_arg, str):
        try:
            stats_arg = json.loads(stats_arg)
        except Exception:
            stats_arg = []
    print("[poe-trade] stats_arg type=" + type(stats_arg).__name__ + " len=" + str(len(stats_arg)), file=sys.stderr)
    if stats_arg:
        filters = []
        for s in stats_arg:
            f = {"id": s["id"], "disabled": False}
            value = {}
            if "min" in s:
                value["min"] = float(s["min"])
            if "max" in s:
                value["max"] = float(s["max"])
            if value:
                f["value"] = value
            filters.append(f)
        query["stats"] = [{"type": "and", "filters": filters}]

    # Type/category filters
    type_filters = {}
    if arguments.get("category"):
        type_filters["category"] = {"option": arguments["category"]}
    if arguments.get("rarity") and arguments["rarity"] != "any":
        type_filters["rarity"] = {"option": arguments["rarity"]}
    if type_filters:
        query.setdefault("filters", {})["type_filters"] = {"filters": type_filters}

    # Req filters
    if arguments.get("max_level"):
        query.setdefault("filters", {}).setdefault("req_filters", {})["filters"] = {
            "lvl": {"max": arguments["max_level"]}
        }

    # Price + sale_type filters (both go in trade_filters.filters together)
    # ALWAYS enforce priced listings — filters out unpriced/negotiate listings.
    # Note: PoE1 trade API only supports "priced" — the Instant Buyout vs In Person
    # distinction shown in the trade site UI is not exposed in the API.
    trade_f = {"sale_type": {"option": "priced"}}
    price_f = {}
    if arguments.get("min_price"):
        price_f["min"] = float(arguments["min_price"])
    if arguments.get("max_price"):
        price_f["max"] = float(arguments["max_price"])
    if price_f:
        price_f["option"] = "chaos"
        trade_f["price"] = price_f
    query.setdefault("filters", {}).setdefault("trade_filters", {})["filters"] = trade_f
    print("[poe-trade] ENFORCING instant_buyout — trade_filters=" + str(trade_f), file=sys.stderr)

    # Socket link filter
    if arguments.get("min_links"):
        query.setdefault("filters", {}).setdefault("socket_filters", {})["filters"] = {
            "links": {"min": int(arguments["min_links"])}
        }

    # Account filter
    if arguments.get("account"):
        query.setdefault("filters", {}).setdefault("trade_filters", {}).setdefault("filters", {})["account"] = {
            "input": arguments["account"]
        }

    return {"query": query, "sort": {"price": "asc"}}


def _parse_listing(item_data):
    """Parse a trade fetch result into a clean dict."""
    listing = item_data.get("listing", {})
    price = listing.get("price", {})
    it = item_data.get("item", {})

    result = {
        "id": item_data.get("id", ""),
        "name": (it.get("name", "") + " " + it.get("typeLine", "")).strip(),
        "base_type": it.get("typeLine", ""),
        "ilvl": it.get("ilvl", 0),
        "price_amount": price.get("amount", 0),
        "price_currency": price.get("currency", "?"),
        "account": listing.get("account", {}).get("name", ""),
        "implicit_mods": it.get("implicitMods", []),
        "explicit_mods": it.get("explicitMods", []),
        "crafted_mods": it.get("craftedMods", []),
        "corrupted": it.get("corrupted", False),
    }

    for req in it.get("requirements", []):
        if req.get("name") == "Level":
            try:
                result["level_req"] = int(req["values"][0][0])
            except (IndexError, ValueError):
                pass

    sockets = it.get("sockets", [])
    if sockets:
        groups = {}
        for s in sockets:
            g = s.get("group", 0)
            groups.setdefault(g, []).append(s.get("sColour", "?"))
        result["sockets"] = "-".join(["".join(v) for v in groups.values()])

    return result


@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        if name == "search_trade":
            league = arguments.get("league", DEFAULT_LEAGUE)
            limit = min(arguments.get("limit", 10), 20)

            payload = _build_search_payload(arguments)
            url = TRADE_BASE + "/search/" + urllib.parse.quote(league)
            data = _post_json(url, payload)

            query_id = data.get("id", "")
            result_ids = data.get("result", [])[:limit]
            total = data.get("total", 0)
            trade_url = "https://www.pathofexile.com/trade/search/" + urllib.parse.quote(league) + "/" + query_id

            if not result_ids or not query_id:
                return [TextContent(type="text", text=json.dumps({
                    "total": total,
                    "results": [],
                    "trade_url": trade_url,
                    "query_id": query_id,
                }, indent=2))]

            # Fetch endpoint rate limit: 1 req/4s, 1 req/12s on account tier.
            # Brief pause avoids burning the 4s window immediately after the search POST.
            time.sleep(2)
            ids_str = ",".join(result_ids)
            fetch_data = _get_json(TRADE_BASE + "/fetch/" + ids_str + "?query=" + query_id)
            listings = [_parse_listing(r) for r in fetch_data.get("result", [])]

            result = {
                "total": total,
                "showing": len(listings),
                "trade_url": trade_url,
                "query_id": query_id,
                "results": listings,
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        elif name == "get_stat_ids":
            q = arguments["query"].lower()
            limit = arguments.get("limit", 10)
            stats = _get_stats()
            matches = [s for s in stats if q in s["text"].lower() or q in s["id"].lower()][:limit]
            return [TextContent(type="text", text=json.dumps(matches, indent=2))]

        elif name == "search_by_item_mods":
            league = arguments.get("league", DEFAULT_LEAGUE)
            limit = min(arguments.get("limit", 10), 20)
            trade_f = {"sale_type": {"option": "priced"}}

            if arguments.get("unique_name"):
                query = {
                    "status": {"option": "securable"},
                    "name": arguments["unique_name"],
                    "filters": {"trade_filters": {"filters": trade_f}},
                }
            else:
                stat_filters = []
                seen_sids = set()
                for m in arguments.get("mods", []):
                    text = m.get("text", "")
                    is_local = m.get("is_local", False)
                    min_pct = m.get("min_pct", 0.7)
                    res = mod_text_to_stat_id(text, is_local=is_local)
                    if res:
                        sid, val = res
                        if sid not in seen_sids:
                            seen_sids.add(sid)
                            min_val = round(val * min_pct)
                            if min_val > 0:
                                stat_filters.append({"id": sid, "disabled": False, "value": {"min": min_val}})

                type_f = {"rarity": {"option": "rare"}}
                if arguments.get("item_category"):
                    type_f["category"] = {"option": arguments["item_category"]}

                query = {
                    "status": {"option": "securable"},
                    "filters": {
                        "type_filters": {"filters": type_f},
                        "trade_filters": {"filters": trade_f},
                    },
                }
                if stat_filters:
                    query["stats"] = [{"type": "and", "filters": stat_filters}]

            payload = {"query": query, "sort": {"price": "asc"}}
            url = TRADE_BASE + "/search/" + urllib.parse.quote(league)
            data = _post_json(url, payload)
            query_id = data.get("id", "")
            result_ids = data.get("result", [])[:limit]
            total = data.get("total", 0)
            trade_url = "https://www.pathofexile.com/trade/search/" + urllib.parse.quote(league) + "/" + query_id

            if not result_ids or not query_id:
                return [TextContent(type="text", text=json.dumps({
                    "total": total, "results": [], "trade_url": trade_url,
                }, indent=2))]

            ids_str = ",".join(result_ids)
            fetch_data = _get_json(TRADE_BASE + "/fetch/" + ids_str + "?query=" + query_id)
            listings = [_parse_listing(r) for r in fetch_data.get("result", [])]
            return [TextContent(type="text", text=json.dumps({
                "total": total, "showing": len(listings),
                "trade_url": trade_url, "results": listings,
            }, indent=2))]

        elif name == "fetch_listing":
            query_id = arguments["query_id"]
            listing_ids_raw = arguments["listing_ids"]
            if isinstance(listing_ids_raw, list):
                listing_ids = listing_ids_raw
            else:
                # Try JSON parse first, then comma-split
                import json as _json
                try:
                    listing_ids = _json.loads(listing_ids_raw)
                    if not isinstance(listing_ids, list):
                        listing_ids = [str(listing_ids)]
                except Exception:
                    listing_ids = [s.strip() for s in listing_ids_raw.split(",") if s.strip()]
            listing_ids = listing_ids[:10]
            ids_str = ",".join(listing_ids)
            data = _get_json(TRADE_BASE + "/fetch/" + ids_str + "?query=" + query_id)
            listings = [_parse_listing(r) for r in data.get("result", [])]
            return [TextContent(type="text", text=json.dumps(listings, indent=2))]

        else:
            return [TextContent(type="text", text="Unknown tool: " + name)]

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")[:500] if hasattr(e, "read") else ""
        return [TextContent(type="text", text="HTTP " + str(e.code) + ": " + body)]
    except Exception as e:
        return [TextContent(type="text", text="Error: " + type(e).__name__ + ": " + str(e))]


from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8483, name="poe-trade")
