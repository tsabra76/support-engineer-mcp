#!/usr/bin/env python3
"""
Technical Support Engineer MCP Server
Provides tools to query knowledge base, analyze tickets, and suggest solutions.
Designed to be vendor-agnostic.
"""

import asyncio
import csv
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from mcp.server import Server, NotificationOptions
from mcp.server.models import InitializationOptions
import mcp.types as types

# Knowledge base root (can be overridden by env var)
KNOWLEDGE_ROOT = os.environ.get("KNOWLEDGE_ROOT", r"\\192.168.68.2\Backups\Workspace\work_knowledge")

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
    ]

@server.call_tool()
async def handle_call_tool(
    name: str, arguments: Dict[str, Any] | None
) -> List[types.TextContent | types.ImageContent | types.EmbeddedResource]:
    root_path = Path(KNOWLEDGE_ROOT)
    if not root_path.exists():
        return [types.TextContent(type="text", text=f"Knowledge root not found: {KNOWLEDGE_ROOT}")]

    if name == "search_knowledge":
        keyword = arguments.get("keyword", "")
        if not keyword:
            return [types.TextContent(type="text", text="Keyword is required")]
        results = search_files(keyword, root_path)
        if not results:
            return [types.TextContent(type="text", text=f"No results found for '{keyword}'")]
        # Return as formatted text
        output = f"Found {len(results)} file(s) containing '{keyword}':\n\n"
        for r in results[:10]:  # limit
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
        # Placeholder: could be improved by analyzing historical tickets
        suggestion = f"For issue: '{issue}'. I recommend checking the knowledge base for similar cases. Use search_knowledge to find relevant articles."
        return [types.TextContent(type="text", text=suggestion)]

    else:
        return [types.TextContent(type="text", text=f"Unknown tool: {name}")]

async def main():
    async with server.run_stdio():
        await server.wait_for_shutdown()

if __name__ == "__main__":
    asyncio.run(main())
