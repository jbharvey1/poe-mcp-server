"""poe-char MCP Server — live character data, stash price scan, KF breakpoint check.

Tools:
  get_character     — fetch equipped items + passives from the PoE API
  scan_stash_tabs   — price all _-prefix stash tabs (rares + uniques), return JSON
  kf_check          — KF breakpoint analysis via headless PoB

Version: 1.0
"""
import asyncio
import json
import sqlite3
import sys
from pathlib import Path

POE_MONITOR_DIR = Path(r"c:\src\buildstuff\poe_monitor")
POE_DIR         = Path(r"c:\poe")
sys.path.insert(0, str(POE_MONITOR_DIR))
sys.path.insert(0, str(POE_DIR))

from poe_lib import PoeApi, load_config, build_pob_xml, PobAnalyzer
from stash_cache import StashCache
from rare_scorer import score_item

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

app    = Server("poe-char")
_api   = None
_league = None
_last_sessid = None

PRICE_DB = POE_DIR / "price_history.db"


# ── helpers ──────────────────────────────────────────────────────────────────

def _init():
    global _api, _league, _last_sessid
    config  = load_config()
    sessid  = config["poesessid"]
    if _api is not None and sessid == _last_sessid:
        return
    _last_sessid = sessid
    _api    = PoeApi(sessid, config["account"], config["character"])
    data    = _api.get_items()
    _league = data.get("character", {}).get("league", "Mirage")


def _unique_price(name: str) -> float:
    try:
        con = sqlite3.connect(str(PRICE_DB))
        row = con.execute(
            "SELECT chaos_value FROM price_history WHERE name = ? ORDER BY fetched_at DESC LIMIT 1",
            (name,)
        ).fetchone()
        if not row:
            row = con.execute(
                "SELECT chaos_value FROM price_history WHERE name LIKE ? ORDER BY fetched_at DESC LIMIT 1",
                (f"%{name.split('(')[0].strip()}%",)
            ).fetchone()
        con.close()
        return row[0] if row else 0.0
    except Exception:
        return 0.0


def _slot_name(inv_id: str) -> str:
    return {
        "Weapon": "Weapon 1", "Offhand": "Weapon 2",
        "Helm": "Helmet", "BodyArmour": "Body Armour",
        "Ring": "Ring 1", "Ring2": "Ring 2",
        "Gloves": "Gloves", "Boots": "Boots",
        "Amulet": "Amulet", "Belt": "Belt",
    }.get(inv_id, inv_id)


# ── tool definitions ──────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="get_character",
        description=(
            "Fetch live gear and passive tree for the configured character from the PoE API. "
            "Returns equipped items (slot, name, base, mods, ilvl) and passive summary "
            "(allocated node count, keystones, mastery count)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "character_name": {
                    "type": "string",
                    "description": "Character name to fetch (default: from config.json).",
                },
                "include_mods": {
                    "type": "boolean",
                    "description": "Include explicit/implicit mods on each item (default true).",
                },
            },
        },
    ),
    Tool(
        name="scan_stash_tabs",
        description=(
            "Price all stash tabs whose name starts with '_'. "
            "Rares are scored via rare_scorer; uniques are priced via price_history.db. "
            "Returns items above min_price chaos (default 5)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "min_price": {
                    "type": "integer",
                    "description": "Minimum chaos value to include (default 5).",
                },
                "force": {
                    "type": "boolean",
                    "description": "Bypass stash cache and fetch fresh from API (default true).",
                },
            },
        },
    ),
    Tool(
        name="get_character_pob",
        description=(
            "Fetch live character data from the PoE API and return a full PoB XML build. "
            "Use this to load a character into pob-brain via load_build(xml=...). "
            "Returns the complete Path of Building XML text."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "character_name": {
                    "type": "string",
                    "description": "Character name to fetch (default: from config.json).",
                },
            },
        },
    ),
    Tool(
        name="kf_check",
        description=(
            "Run Kinetic Fusillade breakpoint analysis via headless Path of Building. "
            "Checks attack rate vs max effective APS, duration mod, projectile count, "
            "headroom, and whether Less Duration / Window of Opportunity are present. "
            "Returns structured JSON with status (SAFE/TIGHT/JAMMED) and recommendations."
        ),
        inputSchema={"type": "object", "properties": {}},
    ),
]


@app.list_tools()
async def list_tools():
    return TOOLS


# ── tool implementations ──────────────────────────────────────────────────────

KF_LUA = """
local output = build.calcsTab.mainOutput
local result = {
    attackRate            = output.Speed or 0,
    maxEffectiveAPS       = output.KineticFusilladeMaxEffectiveAPS or 0,
    avgMoreMult           = output.KineticFusilladeAvgMoreMult or 0,
    duration              = output.Duration or 0,
    durationMod           = output.DurationMod or 1,
    projectileCount       = output.ProjectileCount or 1,
    fullDPS               = output.FullDPS or 0,
    totemPlacementSpeed   = output.TotemPlacementSpeed or 0,
    totemDuration         = output.TotemDuration or 0,
}
local hasWoO = false
for id, node in pairs(build.spec.allocNodes) do
    if node.dn == "Window of Opportunity" then hasWoO = true; break end
end
result.hasWindowOfOpportunity = hasWoO
local hasLessDur, lessDurLevel, lessDurQuality = false, 0, 0
local mainGroup = build.skillsTab.socketGroupList[build.mainSocketGroup]
if mainGroup then
    for _, gem in ipairs(mainGroup.gemList) do
        local n = (gem.nameSpec or ""):lower()
        if n:find("less duration") then
            hasLessDur = true
            lessDurLevel   = gem.level or 1
            lessDurQuality = gem.quality or 0
            break
        end
    end
end
result.hasLessDuration = hasLessDur
result.lessDurLevel    = lessDurLevel
result.lessDurQuality  = lessDurQuality
return result
"""


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    try:
        _init()

        # ── get_character ────────────────────────────────────────────────────
        if name == "get_character":
            include_mods   = arguments.get("include_mods", True)
            character_name = arguments.get("character_name")
            if character_name:
                config = load_config()
                api = PoeApi(config["poesessid"], config["account"], character_name)
            else:
                api = _api
            items_data   = api.get_items()
            passives     = api.get_passives()
            char         = items_data.get("character", {})

            gear = {}
            for item in items_data.get("items", []):
                inv = item.get("inventoryId", "")
                if inv in ("MainInventory", "Flask"):
                    continue
                slot = _slot_name(inv)
                entry = {
                    "name":  item.get("name", "").strip('"') or item.get("typeLine", ""),
                    "base":  item.get("typeLine", ""),
                    "ilvl":  item.get("ilvl", 0),
                    "rarity": {0:"Normal",1:"Magic",2:"Rare",3:"Unique"}.get(
                        item.get("frameType", 0), "?"),
                }
                if include_mods:
                    entry["implicit_mods"] = item.get("implicitMods", [])
                    entry["explicit_mods"] = item.get("explicitMods", [])
                    entry["crafted_mods"]  = item.get("craftedMods", [])
                gear[slot] = entry

            hashes   = passives.get("hashes", [])
            masteries = passives.get("mastery_effects", {})
            # Identify keystones (type == "Keystone" not easily available here,
            # so we pull names from items_data passives endpoint)
            result = {
                "character": {
                    "name":  char.get("name"),
                    "class": char.get("class"),
                    "level": char.get("level"),
                    "league": _league,
                },
                "gear": gear,
                "passives": {
                    "node_count":   len(hashes),
                    "mastery_count": len(masteries),
                    "nodes": hashes,
                    "mastery_effects": masteries,
                },
            }
            return [TextContent(type="text", text=json.dumps(result, indent=2))]

        # ── get_character_pob ──────────────────────────────────────────────
        elif name == "get_character_pob":
            character_name = arguments.get("character_name")
            if character_name:
                config = load_config()
                api = PoeApi(config["poesessid"], config["account"], character_name)
            else:
                api = _api
            items_data    = api.get_items()
            passives_data = api.get_passives()
            char          = items_data.get("character", {})

            config  = load_config()
            bandit  = config.get("bandit", "None")
            pen_str = config.get("res_penalty", "Act 10 (-60%)")
            res_pen = -60 if "(-60%)" in pen_str else (-30 if "(-30%)" in pen_str else 0)

            xml = build_pob_xml(items_data, passives_data, bandit=bandit, res_penalty=res_pen)
            return [TextContent(type="text", text=xml)]

        # ── scan_stash_tabs ──────────────────────────────────────────────────
        elif name == "scan_stash_tabs":
            min_price = arguments.get("min_price", 5)
            force     = arguments.get("force", True)
            cache     = StashCache(_api, _league)

            all_tabs       = cache.get_tab_list()
            underscore_tabs = [t for t in all_tabs if t["n"].startswith("_")]
            scanned_names  = [t["n"] for t in underscore_tabs]

            all_items = []
            errors    = []
            for tab in underscore_tabs:
                try:
                    all_items.extend(cache.get_tab(tab["i"], force=force))
                except Exception as e:
                    errors.append(f"{tab['n']}: {e}")

            rare_hits, unique_hits = [], []
            for item in all_items:
                rarity = item.get("rarity", "").lower()
                if rarity == "rare":
                    r = score_item(item)
                    if r and r.price_estimate >= min_price:
                        rare_hits.append({
                            "name":     r.name,
                            "slot":     r.category,
                            "price":    r.price_estimate,
                            "score":    r.total_score,
                            "breakdown": r.breakdown,
                        })
                elif rarity == "unique":
                    uname    = item.get("name", "").strip('"')
                    typeline = item.get("typeLine", "")
                    lookup   = f"{uname} ({typeline})" if typeline else uname
                    price    = _unique_price(lookup)
                    if price >= min_price:
                        unique_hits.append({
                            "name":  uname,
                            "base":  typeline,
                            "price": price,
                        })

            rare_hits.sort(key=lambda x: x["price"], reverse=True)
            unique_hits.sort(key=lambda x: x["price"], reverse=True)

            total_rares   = sum(1 for i in all_items if i.get("rarity","").lower()=="rare")
            total_uniques = sum(1 for i in all_items if i.get("rarity","").lower()=="unique")

            out = {
                "scanned_tabs":  scanned_names,
                "total_rares":   total_rares,
                "total_uniques": total_uniques,
                "min_price":     min_price,
                "rare_hits":     rare_hits,
                "unique_hits":   unique_hits,
                "errors":        errors,
            }
            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        # ── kf_check ─────────────────────────────────────────────────────────
        elif name == "kf_check":
            config       = load_config()
            items_data   = _api.get_items()
            passives_data = _api.get_passives()
            char         = items_data.get("character", {})

            bandit   = config.get("bandit", "Alira")
            pen_str  = config.get("res_penalty", "Act 5 (-30%)")
            res_pen  = -60 if "(-60%)" in pen_str else (-30 if "(-30%)" in pen_str else 0)
            xml      = build_pob_xml(items_data, passives_data, bandit=bandit, res_penalty=res_pen)

            analyzer = PobAnalyzer()
            analyzer.start()
            load_res = analyzer.load_build(xml, char.get("name", "Build"))
            if not load_res.get("ok"):
                analyzer.stop()
                return [TextContent(type="text", text=f"PoB load failed: {load_res.get('error')}")]

            kf_res = analyzer.eval_lua(KF_LUA)
            analyzer.stop()

            d = kf_res.get("result", {})
            if not d:
                return [TextContent(type="text", text="Failed to extract KF stats from PoB")]

            attack_rate  = float(d.get("attackRate", 0))
            max_aps      = float(d.get("maxEffectiveAPS", 0))
            duration     = float(d.get("duration", 0))
            duration_mod = float(d.get("durationMod", 1))
            proj_count   = int(d.get("projectileCount", 1))
            full_dps     = float(d.get("fullDPS", 0))
            has_woo      = d.get("hasWindowOfOpportunity", False)
            has_less_dur = d.get("hasLessDuration", False)
            less_dur_lvl = int(d.get("lessDurLevel", 0))
            less_dur_q   = int(d.get("lessDurQuality", 0))

            if max_aps > 0:
                headroom_pct = (max_aps - attack_rate) / max_aps * 100
                headroom_abs = max_aps - attack_rate
            else:
                headroom_pct = headroom_abs = 0

            if attack_rate < max_aps:
                status = "SAFE"
            elif attack_rate < max_aps * 1.05:
                status = "TIGHT"
            else:
                status = "JAMMED"

            reduced_dur_pct = (1 - duration_mod) * 100

            recommendations = []
            if status == "JAMMED":
                recommendations.append("Attack rate exceeds max effective APS — totems will never auto-release!")
                if not has_less_dur:
                    recommendations.append("Add Less Duration Support (mandatory)")
                if not has_woo:
                    recommendations.append("Allocate Window of Opportunity cluster (15% + 10% small + 10% less mastery)")
                recommendations.append("Options: Warped Timepiece, Time Clasp ring, Dusk Ring, or remove attack speed")
            elif status == "TIGHT":
                recommendations.append(f"Close to breakpoint — {headroom_abs:.2f} APS headroom. Be careful adding attack speed.")
            else:
                pct_more = (max_aps / attack_rate - 1) * 100 if attack_rate > 0 else 0
                recommendations.append(f"{headroom_pct:.1f}% headroom — can add ~{pct_more:.0f}% more attack speed safely.")

            if not has_less_dur:
                recommendations.append("CRITICAL: Less Duration Support not in main skill group!")
            if not has_woo:
                recommendations.append("Window of Opportunity not allocated — critical for KF!")
            if has_less_dur and less_dur_q < 20:
                recommendations.append(f"Less Duration quality {less_dur_q}/20 — quality it up for more reduced duration.")

            out = {
                "character": char.get("name"),
                "status":    status,
                "attack_rate_aps":    round(attack_rate, 3),
                "max_effective_aps":  round(max_aps, 3),
                "headroom_aps":       round(headroom_abs, 3),
                "headroom_pct":       round(headroom_pct, 1),
                "skill_duration_s":   round(duration, 3),
                "reduced_duration_pct": round(reduced_dur_pct, 1),
                "projectile_count":   proj_count,
                "full_dps":           round(full_dps),
                "has_less_duration":  has_less_dur,
                "less_duration_level": less_dur_lvl,
                "less_duration_quality": less_dur_q,
                "has_window_of_opportunity": has_woo,
                "recommendations":    recommendations,
            }
            return [TextContent(type="text", text=json.dumps(out, indent=2))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        return [TextContent(type="text", text=f"Error: {type(e).__name__}: {e}")]


from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8485, name="poe-char")
