"""PoE Filter MCP Server — read and edit .filter files programmatically.

Supports the full PoE item filter syntax (Show/Hide/Continue blocks, all conditions
and actions). Designed for NeverSink-style filters with comment metadata.

Tools:
  get_filter_info     — summary of filter (path, size, block count, section headers)
  find_blocks         — search blocks by text, class, basetype, or comment
  get_block           — get a specific block by line number
  add_block           — insert a new block at a position (top/bottom/after pattern)
  remove_block        — remove a block by line number or anchor comment
  replace_block       — replace a block entirely
  set_basetype_rule   — convenience: show/hide a specific BaseType everywhere
  reload_filter       — reload filter from disk (discards unsaved changes)

Version: 1.0
"""
import json
import re
import sys
from pathlib import Path

from mcp.server import Server
from mcp.types import TextContent, Tool

# Default filter path — the user's active NeverSink filter
DEFAULT_FILTER = Path("C:/Users/jbharvey/OneDrive/Documents/My Games/Path of Exile/Starting.filter")

app = Server("poe-filter")
print("[poe-filter] SERVER START v1.0", file=sys.stderr)


# ── Filter parsing ─────────────────────────────────────────────────────────────

def _load_filter(path: Path) -> list[str]:
    """Load filter as list of lines (preserving line endings stripped)."""
    return path.read_text(encoding="utf-8", errors="replace").splitlines()


def _find_block_bounds(lines: list[str], start: int) -> tuple[int, int]:
    """Return (start, end) line indices (inclusive) for block starting at `start`."""
    end = start
    for i in range(start + 1, len(lines)):
        stripped = lines[i].strip()
        if stripped.startswith(("Show", "Hide", "Continue")):
            # Next block starts here — previous line is our end
            end = i - 1
            break
    else:
        end = len(lines) - 1
    # Trim trailing blank lines from block
    while end > start and not lines[end].strip():
        end -= 1
    return start, end


def _parse_blocks(lines: list[str]) -> list[dict]:
    """Parse all blocks from lines, return list of block dicts."""
    blocks = []
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if stripped.startswith(("Show", "Hide", "Continue")):
            start, end = _find_block_bounds(lines, i)
            block_lines = lines[start:end + 1]
            # Extract comment from header line
            header = stripped
            comment = ""
            if "#" in header:
                comment = header[header.index("#"):].strip()
            btype = header.split()[0]
            blocks.append({
                "line": start,
                "end_line": end,
                "type": btype,
                "comment": comment,
                "header": header,
                "body": "\n".join(block_lines),
                "conditions": _extract_conditions(block_lines[1:]),
            })
            i = end + 1
        else:
            i += 1
    return blocks


def _extract_conditions(lines: list[str]) -> dict:
    """Extract key conditions from block body lines."""
    conds = {}
    for line in lines:
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split(None, 1)
        if not parts:
            continue
        key = parts[0]
        val = parts[1] if len(parts) > 1 else ""
        conds[key] = val
    return conds


def _get_filter_path(arguments: dict) -> Path:
    p = arguments.get("filter_path")
    return Path(p) if p else DEFAULT_FILTER


# ── Tool definitions ────────────────────────────────────────────────────────────

TOOLS = [
    Tool(
        name="get_filter_info",
        description=(
            "Get summary info about the filter: path, total lines, block count, "
            "and all section headers (## comments). Use this to orient yourself "
            "before making changes."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "filter_path": {
                    "type": "string",
                    "description": f"Path to .filter file. Default: {DEFAULT_FILTER}",
                },
            },
        },
    ),
    Tool(
        name="find_blocks",
        description=(
            "Search filter blocks matching a query. Returns matching blocks with "
            "line numbers, type (Show/Hide/Continue), comment, and key conditions. "
            "Query is matched against the full block text (case-insensitive)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Text to search for (e.g. 'Orb of Chance', 'Body Armours', 'Bosch').",
                },
                "filter_path": {"type": "string"},
                "limit": {
                    "type": "integer",
                    "description": "Max results to return (default 20).",
                },
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="get_block",
        description="Get the full text of a block by its starting line number.",
        inputSchema={
            "type": "object",
            "properties": {
                "line": {
                    "type": "integer",
                    "description": "Starting line number of the block (1-based).",
                },
                "filter_path": {"type": "string"},
            },
            "required": ["line"],
        },
    ),
    Tool(
        name="add_block",
        description=(
            "Insert a new filter block. Positions:\n"
            "  'top' — after the [[0100]] override header (highest priority)\n"
            "  'bottom' — at the end of the file\n"
            "  'after_line:N' — after line number N\n"
            "  'after_pattern:TEXT' — after the first line containing TEXT"
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "block_text": {
                    "type": "string",
                    "description": "Full block text to insert, e.g. 'Hide\\n\\tBaseType == \"Orb of Chance\"'",
                },
                "position": {
                    "type": "string",
                    "description": "Where to insert. Default: 'top'",
                },
                "filter_path": {"type": "string"},
            },
            "required": ["block_text"],
        },
    ),
    Tool(
        name="remove_block",
        description=(
            "Remove a block by its starting line number. "
            "Use find_blocks first to locate the line number."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "line": {
                    "type": "integer",
                    "description": "Starting line number of the block to remove (1-based).",
                },
                "filter_path": {"type": "string"},
            },
            "required": ["line"],
        },
    ),
    Tool(
        name="replace_block",
        description=(
            "Replace a block entirely with new text. "
            "Use find_blocks first to locate the line number."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "line": {
                    "type": "integer",
                    "description": "Starting line number of the block to replace (1-based).",
                },
                "new_block_text": {
                    "type": "string",
                    "description": "Replacement block text.",
                },
                "filter_path": {"type": "string"},
            },
            "required": ["line", "new_block_text"],
        },
    ),
    Tool(
        name="set_basetype_rule",
        description=(
            "Convenience tool: add a top-priority Show or Hide rule for one or more "
            "BaseTypes. Inserts into the [[0100]] override section. "
            "If a Bosch override already exists for the same basetype, it is replaced."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "'Show' or 'Hide'",
                },
                "basetypes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of BaseType strings, e.g. ['Orb of Chance', 'Orb of Alteration']",
                },
                "exact_match": {
                    "type": "boolean",
                    "description": "Use == (exact match) instead of substring. Default true.",
                },
                "comment": {
                    "type": "string",
                    "description": "Optional comment to add to the block header.",
                },
                "extra_conditions": {
                    "type": "string",
                    "description": "Optional extra condition lines, e.g. 'StackSize >= 3'",
                },
                "filter_path": {"type": "string"},
            },
            "required": ["action", "basetypes"],
        },
    ),
]


# ── Tool implementation ─────────────────────────────────────────────────────────

def _tool_get_filter_info(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    lines = _load_filter(path)
    blocks = _parse_blocks(lines)

    # Find section headers
    sections = []
    for i, line in enumerate(lines):
        s = line.strip()
        if re.match(r"#\s*\[\[", s):
            sections.append(f"L{i+1}: {s}")

    show_count = sum(1 for b in blocks if b["type"] == "Show")
    hide_count = sum(1 for b in blocks if b["type"] == "Hide")
    cont_count = sum(1 for b in blocks if b["type"] == "Continue")

    result = {
        "path": str(path),
        "total_lines": len(lines),
        "blocks": {"Show": show_count, "Hide": hide_count, "Continue": cont_count, "total": len(blocks)},
        "sections": sections[:40],  # first 40 section headers
    }
    return json.dumps(result, indent=2)


def _tool_find_blocks(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    query = arguments["query"].lower()
    limit = arguments.get("limit", 20)
    lines = _load_filter(path)
    blocks = _parse_blocks(lines)

    matches = []
    for b in blocks:
        if query in b["body"].lower():
            matches.append({
                "line": b["line"] + 1,  # 1-based for user
                "end_line": b["end_line"] + 1,
                "type": b["type"],
                "comment": b["comment"],
                "conditions": b["conditions"],
                "preview": b["body"][:300],
            })
        if len(matches) >= limit:
            break

    return json.dumps({"total_matches": len(matches), "blocks": matches}, indent=2)


def _tool_get_block(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    line_1based = arguments["line"]
    lines = _load_filter(path)
    idx = line_1based - 1
    if idx < 0 or idx >= len(lines):
        return json.dumps({"error": f"Line {line_1based} out of range (file has {len(lines)} lines)"})
    stripped = lines[idx].strip()
    if not stripped.startswith(("Show", "Hide", "Continue")):
        return json.dumps({"error": f"Line {line_1based} is not a block header: {lines[idx]!r}"})
    start, end = _find_block_bounds(lines, idx)
    return json.dumps({
        "line": start + 1,
        "end_line": end + 1,
        "text": "\n".join(lines[start:end + 1]),
    }, indent=2)


def _validate_block(block_text: str) -> str | None:
    """Validate a filter block has proper structure. Returns error string or None."""
    block_lines = block_text.strip().splitlines()
    if not block_lines:
        return "Block text is empty"
    header = block_lines[0].strip()
    if not header.startswith(("Show", "Hide", "Continue")):
        return f"Block must start with Show/Hide/Continue, got: {header!r}"
    # Check for escaped newlines/tabs that should be real whitespace
    if r"\n" in block_text or r"\t" in block_text:
        return (
            r"Block contains literal \n or \t sequences — these must be actual "
            "newline/tab characters, not escaped strings. The block would be written "
            "as a single line and match ALL items with no conditions."
        )
    # Must have at least one condition line (indented) after the header
    condition_lines = [l for l in block_lines[1:] if l.strip() and not l.strip().startswith("#")]
    if not condition_lines:
        return "Block has no conditions — would match ALL items. Add at least one condition."
    return None


def _tool_add_block(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    block_text = arguments["block_text"].rstrip()
    position = arguments.get("position", "top")

    err = _validate_block(block_text)
    if err:
        return json.dumps({"error": f"Invalid block: {err}", "block_text": block_text})

    lines = _load_filter(path)

    insert_after = None  # 0-based index of line AFTER which to insert

    if position == "top":
        # Find end of [[0100]] header comment + any existing Bosch overrides
        for i, line in enumerate(lines):
            if re.search(r"#.*\[\[0100\]\]", line) or re.search(r"Waypoint c0\.alpha", line):
                insert_after = i
        if insert_after is None:
            insert_after = 0
        # Advance past any existing content in the section (up to next [[xxxx]] section)
        for i in range(insert_after, len(lines)):
            if i > insert_after and re.match(r"#\s*={10}", lines[i]):
                insert_after = i - 1
                break
            insert_after = i
            if re.match(r"#\s*={10}", lines[i]) and i > 0 and re.search(r"\[\[0[2-9]", lines[i]):
                insert_after = i - 1
                break

    elif position == "bottom":
        insert_after = len(lines) - 1

    elif position.startswith("after_line:"):
        insert_after = int(position.split(":")[1]) - 1  # convert to 0-based

    elif position.startswith("after_pattern:"):
        pattern = position[len("after_pattern:"):].lower()
        for i, line in enumerate(lines):
            if pattern in line.lower():
                insert_after = i
                break
        if insert_after is None:
            return json.dumps({"error": f"Pattern not found: {pattern}"})

    else:
        return json.dumps({"error": f"Unknown position: {position}"})

    new_lines = ["", block_text, ""]
    lines = lines[:insert_after + 1] + new_lines + lines[insert_after + 1:]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[poe-filter] add_block at line {insert_after+1}, position={position}", file=sys.stderr)
    return json.dumps({"ok": True, "inserted_after_line": insert_after + 1, "new_block": block_text})


def _tool_remove_block(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    line_1based = arguments["line"]
    lines = _load_filter(path)
    idx = line_1based - 1
    if idx < 0 or idx >= len(lines):
        return json.dumps({"error": f"Line {line_1based} out of range"})
    stripped = lines[idx].strip()
    if not stripped.startswith(("Show", "Hide", "Continue")):
        return json.dumps({"error": f"Line {line_1based} is not a block header: {lines[idx]!r}"})
    start, end = _find_block_bounds(lines, idx)
    # Also remove surrounding blank lines
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    removed = "\n".join(lines[start:end + 1])
    lines = lines[:start] + lines[end + 1:]
    path.write_text("\n".join(lines), encoding="utf-8")
    print(f"[poe-filter] remove_block lines {start+1}-{end+1}", file=sys.stderr)
    return json.dumps({"ok": True, "removed_lines": f"{start+1}-{end+1}", "removed_text": removed})


def _tool_replace_block(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    line_1based = arguments["line"]
    new_text = arguments["new_block_text"].rstrip()

    err = _validate_block(new_text)
    if err:
        return json.dumps({"error": f"Invalid block: {err}", "block_text": new_text})

    lines = _load_filter(path)
    idx = line_1based - 1
    if idx < 0 or idx >= len(lines):
        return json.dumps({"error": f"Line {line_1based} out of range"})
    stripped = lines[idx].strip()
    if not stripped.startswith(("Show", "Hide", "Continue")):
        return json.dumps({"error": f"Line {line_1based} is not a block header"})
    start, end = _find_block_bounds(lines, idx)
    old_text = "\n".join(lines[start:end + 1])
    new_block_lines = new_text.splitlines()
    lines = lines[:start] + new_block_lines + lines[end + 1:]
    path.write_text("\n".join(lines), encoding="utf-8")
    return json.dumps({"ok": True, "replaced_lines": f"{start+1}-{end+1}", "old": old_text, "new": new_text})


def _tool_set_basetype_rule(arguments: dict) -> str:
    path = _get_filter_path(arguments)
    action = arguments["action"].capitalize()
    if action not in ("Show", "Hide"):
        return json.dumps({"error": "action must be 'Show' or 'Hide'"})
    basetypes = arguments["basetypes"]
    exact = arguments.get("exact_match", True)
    comment = arguments.get("comment", f"Bosch: {action} {', '.join(basetypes)}")
    extra = arguments.get("extra_conditions", "").strip()

    op = "==" if exact else ""
    bt_str = " ".join(f'"{b}"' for b in basetypes)
    lines_out = [f"{action} # {comment}"]
    if extra:
        for line in extra.splitlines():
            lines_out.append(f"\t{line.strip()}")
    lines_out.append(f"\tBaseType {op} {bt_str}" if op else f"\tBaseType {bt_str}")
    block_text = "\n".join(lines_out)

    # Check if a Bosch override for the same basetypes already exists — if so, replace it
    filter_lines = _load_filter(path)
    blocks = _parse_blocks(filter_lines)
    for b in blocks:
        if comment.lower() in b["body"].lower() or any(bt.lower() in b["body"].lower() for bt in basetypes):
            if "Bosch" in b["comment"]:
                # Replace it
                return _tool_replace_block({"line": b["line"] + 1, "new_block_text": block_text, "filter_path": str(path)})

    # Otherwise insert at top
    return _tool_add_block({"block_text": block_text, "position": "top", "filter_path": str(path)})


# ── MCP server ──────────────────────────────────────────────────────────────────

@app.list_tools()
async def list_tools():
    return TOOLS


@app.call_tool()
async def call_tool(name: str, arguments: dict):
    print(f"[poe-filter] {name} {json.dumps(arguments)[:120]}", file=sys.stderr)
    try:
        if name == "get_filter_info":
            result = _tool_get_filter_info(arguments)
        elif name == "find_blocks":
            result = _tool_find_blocks(arguments)
        elif name == "get_block":
            result = _tool_get_block(arguments)
        elif name == "add_block":
            result = _tool_add_block(arguments)
        elif name == "remove_block":
            result = _tool_remove_block(arguments)
        elif name == "replace_block":
            result = _tool_replace_block(arguments)
        elif name == "set_basetype_rule":
            result = _tool_set_basetype_rule(arguments)
        else:
            result = json.dumps({"error": f"Unknown tool: {name}"})
        return [TextContent(type="text", text=result)]
    except Exception as e:
        import traceback
        msg = f"Error in {name}: {e}\n{traceback.format_exc()}"
        print(f"[poe-filter] ERROR: {msg}", file=sys.stderr)
        return [TextContent(type="text", text=json.dumps({"error": msg}))]


from mcp_server_utils import run_server

if __name__ == "__main__":
    run_server(app, port=8487, name="poe-filter")
