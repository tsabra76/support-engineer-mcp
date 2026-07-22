#!/usr/bin/env python3
"""
Technical Support Engineer MCP Server
Provides tools to query knowledge base, analyze tickets, suggest solutions,
and inspect debug logs.
Designed to be vendor-agnostic.
"""

import asyncio
import csv
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.types as types

# Default knowledge root: ./knowledge relative to this script
DEFAULT_KNOWLEDGE_ROOT = os.path.join(os.path.dirname(__file__), "knowledge")
KNOWLEDGE_ROOT = os.environ.get("KNOWLEDGE_ROOT", DEFAULT_KNOWLEDGE_ROOT)

# Log directory: defaults to ./logs inside the knowledge root, configurable via LOG_ROOT env var
DEFAULT_LOG_ROOT = os.path.join(KNOWLEDGE_ROOT, "logs")
LOG_ROOT = os.environ.get("LOG_ROOT", DEFAULT_LOG_ROOT)

server = Server("support-engineer")

# ---------- Helper functions ----------
def load_csv(file_path: Path) -> List[Dict[str, str]]:
    """Load CSV into list of dicts."""
    rows = []
    with open(file_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
    return rows

def search_files(keyword: str, root: Path) -> List[Dict[str, Any]]:
    """Search for keyword in CSV and text files under root."""
    results = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        # Skip binary or large files
        if path.suffix in ['.pyc', '.json', '.docx']:
            continue
        try:
            content = path.read_text(encoding='utf-8', errors='ignore')
            if keyword.lower() in content.lower():
                # Get first few lines for snippet
                lines = content.splitlines()
                snippet = "\n".join(lines[:5])  # first 5 lines
                results.append({
                    "file": str(path.relative_to(root)),
                    "snippet": snippet,
                    "size": len(content)
                })
        except Exception:
            continue
    return results

def get_ticket_summary(file_path: Path) -> Dict[str, Any]:
    """Extract summary from a ticket CSV."""
    try:
        rows = load_csv(file_path)
        summary = {
            "total": len(rows),
            "fields": list(rows[0].keys()) if rows else [],
            "sample": rows[:3] if rows else []
        }
        return summary
    except Exception as e:
        return {"error": str(e)}

# ---------- Log helpers ----------
def get_log_files(log_root: Path) -> List[Dict[str, Any]]:
    """Retrieve list of log files with basic metadata."""
    log_files = []
    # Supported log file extensions
    log_extensions = {'.log', '.txt', '.out', '.err'}
    for path in log_root.rglob("*"):
        if path.is_file() and path.suffix.lower() in log_extensions:
            stat = path.stat()
            log_files.append({
                "file": str(path.relative_to(log_root)),
                "size_bytes": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
            })
    return log_files

def search_logs(log_root: Path, keyword: str, max_matches: int = 50) -> List[Dict[str, Any]]:
    """Search log files for a keyword, returning matching lines."""
    results = []
    log_files = get_log_files(log_root)
    for lf in log_files:
        file_path = log_root / lf["file"]
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                for line_num, line in enumerate(f, 1):
                    if keyword.lower() in line.lower():
                        results.append({
                            "file": lf["file"],
                            "line": line_num,
                            "content": line.strip()
                        })
                        if len(results) >= max_matches:
                            return results
        except Exception:
            continue
    return results

def tail_logs(log_root: Path, tail_lines: int = 20) -> Dict[str, List[str]]:
    """Return the last N lines of each log file."""
    tails = {}
    log_files = get_log_files(log_root)
    for lf in log_files:
        file_path = log_root / lf["file"]
        try:
            with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                lines = f.readlines()
                tails[lf["file"]] = [line.rstrip() for line in lines[-tail_lines:]]
        except Exception:
            tails[lf["file"]] = ["[Error reading file]"]
    return tails

# ---------- Tool definitions ----------
@server.list_tools()
async def handle_list_tools() -> List[types.Tool]:
    return [
        types.Tool(
            name="search_knowledge",
            description="Search the knowledge base for a keyword or phrase. Returns matching files with snippets.",
            inputSchema={
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "Search term (case-insensitive)"},
                },
                "required": ["keyword"],
            },
        ),
        types.Tool(
            name="analyze_tickets",
            description="Get summary statistics and sample data from a support ticket CSV file.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_name": {"type": "string", "description": "Name of the CSV file (relative to knowledge root)"},
                },
                "required": ["file_name"],
            },
        ),
        types.Tool(
            name="list_knowledge_files",
            description="List all files in the knowledge base, optionally filtered by extension.",
            inputSchema={
                "type": "object",
                "properties": {
                    "extension": {"type": "string", "description": "Filter by file extension (e.g., '.csv', '.md')"},
                },
            },
        ),
        types.Tool(
            name="suggest_resolution",
            description="Suggest a solution based on historical ticket patterns. (Placeholder - improves with data).",
            inputSchema={
                "type": "object",
                "properties": {
                    "issue_description": {"type": "string", "description": "Description of the issue"},
                },
                "required": ["issue_description"],
            },
        ),
        types.Tool(
            name="debug_logs",
            description="Inspect debug logs: list log files, search for keywords, or view recent entries.",
            inputSchema={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "What to do: 'list' (list log files), 'search' (search by keyword), or 'tail' (show recent lines)",
                        "enum": ["list", "search", "tail"]
                    },
                    "keyword": {"type": "string", "description": "Keyword to search for (required if action='search')"},
                    "tail_lines": {"type": "integer", "description": "Number of lines to show from the end (optional, default 20 if action='tail')"},
                },
                "required": ["action"],
            },
        ),
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: Dict[str, Any] | None
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    root_path = Path(KNOWLEDGE_ROOT)
    log_path = Path(LOG_ROOT)

    # Ensure knowledge root exists at least; log root may not exist
    if not root_path.exists():
        return [types.TextContent(type="text", text=f"Knowledge root not found: {KNOWLEDGE_ROOT}")]

    if name == "search_knowledge":
        keyword = arguments.get("keyword", "")
        if not keyword:
            return [types.TextContent(type="text", text="Keyword is required")]
        results = search_files(keyword, root_path)
        if not results:
            return [types.TextContent(type="text", text=f"No results found for '{keyword}'")]
        output = f"Found {len(results)} file(s) containing '{keyword}':\n\n"
        for r in results[:10]:
            output += f"File: {r['file']}\nSnippet: {r['snippet']}\n\n"
        return [types.TextContent(type="text", text=output)]

    elif name == "analyze_tickets":
        file_name = arguments.get("file_name", "")
        if not file_name:
            return [types.TextContent(type="text", text="file_name is required")]
        file_path = root_path / file_name
        if not file_path.exists():
            return [types.TextContent(type="text", text=f"File not found: {file_name}")]
        summary = get_ticket_summary(file_path)
        return [types.TextContent(type="text", text=json.dumps(summary, indent=2))]

    elif name == "list_knowledge_files":
        ext = arguments.get("extension", "")
        files = []
        for path in root_path.rglob("*"):
            if path.is_file():
                if ext and not path.suffix.lower() == ext.lower():
                    continue
                files.append(str(path.relative_to(root_path)))
        output = "\n".join(sorted(files)) if files else "No files found."
        return [types.TextContent(type="text", text=output)]

    elif name == "suggest_resolution":
        issue = arguments.get("issue_description", "")
        suggestion = f"For issue: '{issue}'. I recommend checking the knowledge base for similar cases. Use search_knowledge to find relevant articles."
        return [types.TextContent(type="text", text=suggestion)]

    elif name == "debug_logs":
        action = arguments.get("action", "list")
        if not log_path.exists():
            return [types.TextContent(type="text", text=f"Log directory not found: {LOG_ROOT}. Please ensure logs exist or set LOG_ROOT environment variable.")]

        if action == "list":
            log_files = get_log_files(log_path)
            if not log_files:
                return [types.TextContent(type="text", text=f"No log files found in {LOG_ROOT}")]
            output = f"Log files in {LOG_ROOT}:\n\n"
            for lf in log_files:
                output += f"- {lf['file']} ({lf['size_bytes']} bytes, modified {lf['last_modified']})\n"
            return [types.TextContent(type="text", text=output)]

        elif action == "search":
            keyword = arguments.get("keyword", "")
            if not keyword:
                return [types.TextContent(type="text", text="Keyword is required for search action")]
            matches = search_logs(log_path, keyword)
            if not matches:
                return [types.TextContent(type="text", text=f"No matches found for '{keyword}' in log files")]
            output = f"Found {len(matches)} match(es) for '{keyword}':\n\n"
            for m in matches:
                output += f"{m['file']}:{m['line']}: {m['content']}\n"
            return [types.TextContent(type="text", text=output)]

        elif action == "tail":
            tail_lines = int(arguments.get("tail_lines", 20))
            tails = tail_logs(log_path, tail_lines)
            output = ""
            for fname, lines in tails.items():
                output += f"--- {fname} (last {len(lines)} lines) ---\n"
                for line in lines:
                    output += line + "\n"
                output += "\n"
            return [types.TextContent(type="text", text=output)]

        else:
            return [types.TextContent(type="text", text=f"Unknown action: {action}. Use 'list', 'search', or 'tail'.")]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    async with server.run_stdio():
        await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
