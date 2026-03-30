# poe-mcp-server

[![Follow on X](https://img.shields.io/badge/follow-%40boschzilla-black.svg?logo=x)](https://x.com/boschzilla)

A collection of MCP (Model Context Protocol) servers that expose Path of Exile game data, pricing, trade, stash management, character inspection, item filtering, and market analytics as tool-callable endpoints. Designed to be used by AI assistants (Claude, etc.) for PoE build planning, economy tracking, and in-game automation.

---

## Architecture

The repo contains 7 Python modules. Six are standalone MCP servers, each with their own `Server` instance, `TOOLS` list, and `call_tool` handler. The seventh, `poe_all.py`, acts as a **unified gateway** that dynamically imports all six sibling servers (plus the external `pob-vault` and `pob-brain` servers), collects their tool definitions, and re-registers everything onto a single combined MCP `Server`. This means you can either run individual servers on their own ports, or run `poe_all.py` once to get every tool under one MCP connection.

### How poe_all.py bundles sub-servers

1. Adds sibling directories (`c:/src/pobrain`, `c:/src/buildstuff`) to `sys.path`.
2. Iterates a registry of `(module_name, prefix)` tuples and uses `importlib.import_module()` to load each.
3. From each loaded module, reads the `TOOLS` list and extracts the `call_tool` request handler from the sub-server's `app.request_handlers`.
4. Stores a mapping of `tool_name -> (handler, original_name, prefix)` in `_tool_registry`.
5. The combined `@combined.call_tool()` handler dispatches incoming calls by constructing a synthetic `CallToolRequest` and forwarding it to the appropriate sub-server handler.
6. `pob-brain` (from `c:/src/pobrain/server.py`) is loaded separately because it manages a LuaProcess for headless Path of Building.

**Sub-server registry:**

| Module | Prefix | Standalone Port |
|--------|--------|-----------------|
| `poe_market` | poe-market | 8481 |
| `poe_stash` | poe-stash | 8482 |
| `poe_trade` | poe-trade | 8483 |
| `poe_char` | poe-char | 8485 |
| `poe_pricer` | poe-pricer | 8486 |
| `poe_filter` | poe-filter | 8487 |
| `pob_vault_mcp` | pob-vault | (external) |
| `server` (pob-brain) | pob-brain | (external) |
| **poe_all** | **poe-all** | **8490** |

---

## Modules

### poe_all.py

**What it does:** Unified MCP server that bundles all PoE and PoB tools into a single endpoint. No tools of its own -- it re-exports everything from the sub-servers.

**Entry point:**
```
python poe_all.py          # stdio mode (for .mcp.json)
python poe_all.py sse      # HTTPS SSE on port 8490
```

---

### poe_char.py

**What it does:** Live character data from the PoE API -- equipped gear, passive tree, PoB XML export, and Kinetic Fusillade breakpoint analysis.

#### Tools

| Tool | Description |
|------|-------------|
| **`get_character`** | Fetch equipped items and passive tree for a character. |
| **`get_character_pob`** | Fetch character data and return a complete Path of Building XML. |
| **`scan_stash_tabs`** | Price all stash tabs whose name starts with `_`. Rares scored via algorithm, uniques via poe.ninja DB. |
| **`kf_check`** | Kinetic Fusillade breakpoint analysis via headless PoB. |

#### get_character

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `character_name` | string | config default | Character name to fetch |
| `include_mods` | boolean | true | Include implicit/explicit/crafted mods on each item |

**Returns:** JSON with `character` (name, class, level, league), `gear` (keyed by slot with name, base, ilvl, rarity, mods), and `passives` (node_count, mastery_count, node hashes, mastery effects).

#### get_character_pob

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `character_name` | string | config default | Character name to fetch |

**Returns:** Complete PoB XML string. Use with `load_build(xml=...)` on pob-brain.

#### scan_stash_tabs

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `min_price` | integer | 5 | Minimum chaos value to include |
| `force` | boolean | true | Bypass stash cache and fetch fresh from API |

**Returns:** JSON with `scanned_tabs`, `total_rares`, `total_uniques`, `rare_hits` (with name, slot, price, score, breakdown), `unique_hits` (with name, base, price), and `errors`.

#### kf_check

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| (none) | | | Uses configured character |

**Returns:** JSON with `status` (SAFE/TIGHT/JAMMED), `attack_rate_aps`, `max_effective_aps`, `headroom_aps`, `headroom_pct`, `skill_duration_s`, `reduced_duration_pct`, `projectile_count`, `full_dps`, `has_less_duration`, `has_window_of_opportunity`, and `recommendations` array.

**Notable:** Runs a Lua script inside headless PoB to extract KF-specific stats from `build.calcsTab.mainOutput`. Checks gem links for Less Duration Support and passive tree for Window of Opportunity.

---

### poe_filter.py

**What it does:** Read and edit PoE `.filter` files programmatically. Supports full PoE item filter syntax (Show/Hide/Continue blocks). Designed for NeverSink-style filters with comment metadata and `[[xxxx]]` section headers.

**Default filter path:** `C:/Users/jbharvey/OneDrive/Documents/My Games/Path of Exile/Starting.filter`

#### Tools

| Tool | Description |
|------|-------------|
| **`get_filter_info`** | Summary of filter: path, line count, block counts by type, section headers. |
| **`find_blocks`** | Search blocks by text (case-insensitive full-text match). |
| **`get_block`** | Get the full text of a block by its starting line number. |
| **`add_block`** | Insert a new block at a position (top/bottom/after_line:N/after_pattern:TEXT). |
| **`remove_block`** | Remove a block by its starting line number. |
| **`replace_block`** | Replace a block entirely with new text. |
| **`set_basetype_rule`** | Convenience: add a top-priority Show/Hide rule for specific BaseTypes. |

#### get_filter_info

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** JSON with `path`, `total_lines`, `blocks` (counts by Show/Hide/Continue/total), `sections` (first 40 `[[xxxx]]` section headers with line numbers).

#### find_blocks

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query` | string | *required* | Text to search for |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |
| `limit` | integer | 20 | Max results |

**Returns:** JSON with `total_matches` and `blocks` array (line, end_line, type, comment, conditions, preview).

#### get_block

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `line` | integer | *required* | Starting line number (1-based) |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** JSON with `line`, `end_line`, `text`.

#### add_block

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `block_text` | string | *required* | Full block text to insert |
| `position` | string | "top" | Where to insert: `top`, `bottom`, `after_line:N`, `after_pattern:TEXT` |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** JSON with `ok`, `inserted_after_line`, `new_block`.

**Notable:** Validates blocks before insertion -- rejects empty blocks, blocks without conditions (would match ALL items), and blocks with literal `\n`/`\t` escape sequences instead of real whitespace.

#### remove_block

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `line` | integer | *required* | Starting line number (1-based) |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** JSON with `ok`, `removed_lines`, `removed_text`.

#### replace_block

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `line` | integer | *required* | Starting line number (1-based) |
| `new_block_text` | string | *required* | Replacement block text |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** JSON with `ok`, `replaced_lines`, `old`, `new`.

#### set_basetype_rule

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `action` | string | *required* | "Show" or "Hide" |
| `basetypes` | array[string] | *required* | List of BaseType strings |
| `exact_match` | boolean | true | Use `==` exact match operator |
| `comment` | string | auto-generated | Comment on block header |
| `extra_conditions` | string | "" | Additional condition lines |
| `filter_path` | string | DEFAULT_FILTER | Path to .filter file |

**Returns:** Same as `add_block` or `replace_block` (replaces existing Bosch overrides for the same basetypes).

**Notable:** Inserts into the `[[0100]]` override section for highest priority. If a "Bosch" tagged override already exists for the same basetype, it replaces it instead of duplicating.

---

### poe_market.py

**What it does:** Exposes the local `price_history.db` SQLite database (populated by an external `trend_watcher.py` process that scrapes poe.ninja) via MCP. Provides price lookups, history, trend analysis (risers/fallers/movers), and database status.

#### Tools

| Tool | Description |
|------|-------------|
| **`get_price`** | Get the latest price for a specific item (exact match, case-insensitive). |
| **`get_price_history`** | Get all price snapshots for an item over time. |
| **`search_items`** | Search items by name substring. |
| **`get_risers`** | Items with the biggest positive price increase (% change). |
| **`get_fallers`** | Items with the biggest negative price drop (% change). |
| **`get_movers`** | Items with the biggest absolute price movement (volatile items). |
| **`snapshot_status`** | Database info: total snapshots, latest/oldest fetch time, items tracked. |

#### get_price

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `name` | string | *required* | Item name (exact match, case-insensitive) |

**Returns:** JSON with `name`, `chaos_value`, `category`, and other fields from `price_db.search_items()`.

#### get_price_history

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `name` | string | *required* | Item name (exact match, falls back to fuzzy) |

**Returns:** Array of price snapshot records over time.

#### search_items

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query` | string | *required* | Search term (substring match) |
| `limit` | integer | 20 | Max results |

**Returns:** Array of items with latest prices.

#### get_risers

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `min_snapshots` | integer | 3 | Minimum price snapshots required for reliable trends |
| `limit` | integer | 25 | Max results |
| `min_price` | number | 0 | Minimum current chaos price (filters out junk) |

**Returns:** Array of items sorted by % price increase.

#### get_fallers

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `min_snapshots` | integer | 3 | Minimum snapshots required |
| `limit` | integer | 25 | Max results |

**Returns:** Array of items sorted by % price decrease.

#### get_movers

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `min_snapshots` | integer | 3 | Minimum snapshots required |
| `limit` | integer | 25 | Max results |

**Returns:** Array of items sorted by absolute price movement.

#### snapshot_status

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| (none) | | | |

**Returns:** JSON with `total_snapshots`, `latest_fetch`, `oldest_fetch`, `items_tracked`.

---

### poe_pricer.py

**What it does:** Local item pricing engine with zero network calls. Magic/rare items are scored using the `rare_scorer` algorithm. Uniques, gems, currency, and divination cards are looked up from the local poe.ninja snapshot database. Reloads the scorer module from source on every call so edits take effect immediately.

#### Tools

| Tool | Description |
|------|-------------|
| **`price_item`** | Price a single item from a PoE API dict or clipboard text. |
| **`price_items`** | Batch-price an array of PoE API item dicts. |
| **`ninja_lookup`** | Look up poe.ninja price by name (exact or fuzzy). |

#### price_item

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `item_dict` | object | -- | PoE API item dict (frameType, typeLine, explicitMods, etc.) |
| `item_text` | string | -- | Raw clipboard text from PoE (Ctrl+C format) |

Provide one of `item_dict` or `item_text`.

**Returns (algo method):** JSON with `name`, `ilvl`, `rarity`, `method` ("algo"), `category`, `price_estimate`, `total_score`, `good_mods`, `junk_mods`, `breakdown`. If fractured: also `fractured` and `should_trade_check`.

**Returns (ninja_db method):** JSON with `name`, `ilvl`, `rarity`, `method` ("ninja_db"), `category`, `price_estimate`.

**Returns (not found):** JSON with `method` ("not_found"), `price_estimate` (null), `note`.

#### price_items

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `items` | array[object] | *required* | Array of PoE API item dicts |
| `min_price` | number | 0 | Only include items at or above this price |
| `include_unpriced` | boolean | false | Include items that couldn't be priced |

**Returns:** JSON with `total_items`, `priced_count`, `total_value_chaos`, `items` (sorted by price descending), and optionally `should_trade_check` (names of fractured items worth verifying on trade).

#### ninja_lookup

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `name` | string | *required* | Item name (partial match supported) |

**Returns (exact):** JSON with `name`, `price_chaos`, `category`, `match` ("exact").

**Returns (fuzzy):** JSON with `query`, `match` ("fuzzy"), `results` array of `{name, price_chaos, category}`.

---

### poe_stash.py

**What it does:** Stash tab access with caching, item search, rare scoring, and tab pricing. Uses a 5-minute cache via `StashCache` to avoid excessive API calls.

#### Tools

| Tool | Description |
|------|-------------|
| **`get_tab`** | Get all items from a stash tab by name or index. |
| **`list_tabs`** | List all stash tab names and indices. |
| **`score_rare`** | Score a rare item from PoE clipboard text. |
| **`price_tab`** | Score and price all rare items in a stash tab. |
| **`find_items`** | Search stash tabs for items by name, base type, or mod text. |
| **`cache_status`** | Show cache freshness for stash tabs. |

#### get_tab

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `tab_name` | string | -- | Tab name (case-insensitive) |
| `tab_index` | integer | -- | Tab index (0-based) |
| `force` | boolean | false | Force refresh, bypassing cache |

Provide one of `tab_name` or `tab_index`.

**Returns:** JSON with `count` and `items` array. Each item includes `name`, `baseType`, `rarity`, `ilvl`, `category`, `mods`, `implicitMods`, `craftedMods`, `enchantMods`, `sockets`, `requirements`, and grid position (`x`, `y`, `w`, `h`) if present.

#### list_tabs

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `force` | boolean | false | Force refresh tab list |

**Returns:** Array of `{index, name, type}` for each tab.

#### score_rare

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `item_text` | string | *required* | Raw item text from PoE (Ctrl+C) |

**Returns:** JSON with `name`, `category`, `ilvl`, `price_estimate`, `total_score`, `affix_count`, `good_mods`, `junk_mods`, `breakdown`.

#### price_tab

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `tab_name` | string | -- | Tab name (case-insensitive) |
| `tab_index` | integer | -- | Tab index (0-based) |
| `min_price` | integer | 1 | Minimum chaos value to include |
| `force` | boolean | false | Force refresh stash data |

**Returns:** JSON with `total_rares`, `priced_items`, `total_value`, `items` (sorted by score descending, each with name, category, price, score, good_mods, junk_mods, breakdown).

**Notable:** Reads directly from disk cache rather than hitting the API. Requires a prior `get_tab` or `list_tabs` call to populate the cache.

#### find_items

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query` | string | *required* | Search term (matches name, base type, or mod text) |
| `tab_name` | string | -- | Search only this tab (searches first 10 tabs if omitted) |
| `force` | boolean | false | Force refresh |

**Returns:** JSON with `matches` count and `items` array. Rare items include `price_estimate` and `score`.

#### cache_status

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| (none) | | | |

**Returns:** JSON with `league` and `tabs` array (index, name, cached, age_seconds, fresh). Shows first 15 tabs.

---

### poe_trade.py

**What it does:** Search and fetch from the official `pathofexile.com/api/trade`. General-purpose trade API wrapper supporting any item type. Always enforces priced/instant-buyout listings (`sale_type: priced`). Includes automatic retry with backoff on HTTP 429 rate limits.

#### Tools

| Tool | Description |
|------|-------------|
| **`search_trade`** | Search the PoE trade site with stat/category/price filters. |
| **`get_stat_ids`** | Search for trade stat filter IDs by keyword. |
| **`search_by_item_mods`** | Search trade by human-readable mod text (no stat IDs needed). |
| **`fetch_listing`** | Fetch detailed info for specific listing IDs from a previous search. |

#### search_trade

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `league` | string | "Mirage" | League name |
| `category` | string | -- | Item category (e.g., "weapon.wand", "armour.body", "accessory.ring") |
| `rarity` | string | "any" | "nonunique", "unique", or "any" |
| `name` | string | -- | Item name (for uniques) |
| `base_type` | string | -- | Base type filter (e.g., "Opal Wand") |
| `stats` | array | -- | Stat filters, each `{id, min, max}` |
| `min_price` | number | -- | Minimum price in chaos |
| `max_price` | number | -- | Maximum price in chaos |
| `max_level` | integer | -- | Maximum level requirement |
| `instant_buyout` | boolean | true (when max_price set) | Only show priced listings |
| `online_only` | boolean | true | Only show online sellers |
| `account` | string | -- | Filter by seller account name |
| `min_links` | number | -- | Minimum linked sockets |
| `limit` | integer | 10 | Max results (capped at 20) |

**Returns:** JSON with `total`, `showing`, `trade_url` (clickable link to results on trade site), `query_id`, and `results` array. Each result includes `id`, `name`, `base_type`, `ilvl`, `price_amount`, `price_currency`, `account`, `implicit_mods`, `explicit_mods`, `crafted_mods`, `corrupted`, `level_req`, `sockets`.

#### get_stat_ids

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query` | string | *required* | Keyword to search (e.g., "attack speed", "fire resistance") |
| `limit` | integer | 10 | Max results |

**Returns:** Array of `{id, text, type}` for matching stat filter IDs.

#### search_by_item_mods

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `mods` | array | -- | List of `{text, is_local?, min_pct?}` objects. `min_pct` defaults to 0.7 (70% of rolled value). |
| `item_category` | string | -- | Trade category (e.g., "weapon.wand") |
| `unique_name` | string | -- | For uniques: search by name instead of mods |
| `league` | string | "Mirage" | League name |
| `limit` | integer | 10 | Max results |

**Returns:** Same format as `search_trade`.

**Notable:** Converts human-readable mod text to stat IDs automatically using a pattern-matching index built from the trade stats API. Normalizes numbers to `#` placeholders for fuzzy matching. Handles local weapon mods (e.g., "increased Physical Damage (local)") separately.

#### fetch_listing

| Argument | Type | Default | Description |
|----------|------|---------|-------------|
| `query_id` | string | *required* | Query ID from a previous search result |
| `listing_ids` | string | *required* | Comma-separated listing IDs (max 10) |

**Returns:** Array of parsed listing objects.

---

## Dependencies

### Shared Utilities

| Module | Location | Purpose |
|--------|----------|---------|
| `mcp_server_utils.py` | `c:/poe/mcp/` | Shared `run_server()` function that handles stdio vs SSE mode selection based on CLI args |

### poe_monitor Libraries

All located at `c:/src/buildstuff/poe_monitor/`:

| Module | Purpose |
|--------|---------|
| `poe_lib.py` | `PoeApi` class (PoE API client), `load_config()` (reads `config.json` with `poesessid`, `account`, `character`), `build_pob_xml()` (converts API data to PoB XML), `PobAnalyzer` (headless PoB interface) |
| `stash_cache.py` | `StashCache` class with 5-minute disk caching for stash tab data. Provides `get_tab()`, `get_tab_by_name()`, `get_tab_list()`, `get_tabs()`, `cache_age()` |
| `rare_scorer.py` | `score_item()` (scores a PoE API item dict), `score_item_text()` (scores from clipboard text), `classify_item()` (categorizes items). Returns result objects with `name`, `category`, `ilvl`, `price_estimate`, `total_score`, `good_mod_count`, `junk_count`, `breakdown`, `is_fractured`, `should_trade_check` |

### Price Database

| Module | Location | Purpose |
|--------|----------|---------|
| `price_db.py` | `c:/poe/` | SQLite interface for `price_history.db`. Functions: `search_items()`, `get_history()`, `get_risers()`, `get_fallers()`, `get_movers()`, `snapshot_count()`, `get_snapshot_times()`, `get_all_latest()` |

The database (`c:/poe/price_history.db`) is populated by an external `trend_watcher.py` process that scrapes poe.ninja periodically.

### Path of Building (PoB) Brain

| Module | Location | Purpose |
|--------|----------|---------|
| `server.py` | `c:/src/pobrain/` | pob-brain MCP server -- headless Path of Building with Lua eval, build loading, stat extraction, gem swapping, tree analysis |

---

## Setup

### Prerequisites

- Python 3.11+
- `mcp` Python package (Model Context Protocol SDK)
- PoE session ID configured in `c:/src/buildstuff/poe_monitor/config.json`:

```json
{
    "poesessid": "your-poe-session-id",
    "account": "your-account-name",
    "character": "your-character-name",
    "bandit": "None",
    "res_penalty": "Act 10 (-60%)"
}
```

### Running Individual Servers

Each module can run standalone:

```bash
python poe_market.py       # stdio mode
python poe_market.py sse   # SSE mode on its assigned port
```

### Running the Combined Server

```bash
python poe_all.py           # stdio mode — all tools under one connection
python poe_all.py sse       # SSE mode on port 8490
```

---

## MCP Registration

Add to your `.mcp.json` (or Claude Desktop config) as a single entry using the combined server:

```json
{
    "mcpServers": {
        "poe-all": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_all.py"],
            "env": {}
        }
    }
}
```

Or register individual servers separately:

```json
{
    "mcpServers": {
        "poe-market": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_market.py"]
        },
        "poe-stash": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_stash.py"]
        },
        "poe-trade": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_trade.py"]
        },
        "poe-char": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_char.py"]
        },
        "poe-pricer": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_pricer.py"]
        },
        "poe-filter": {
            "command": "python",
            "args": ["C:/tmp/poe-mcp-server/poe_filter.py"]
        }
    }
}
```
