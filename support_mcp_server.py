#!/usr/bin/env python3
"""
Support Engineer MCP Server – vendor‑agnostic knowledge retrieval and triage assistant.

Powered by DuckDB with hybrid BM25 + hashed‑vector ranking.

Usage:
  python support_mcp_server.py          # start the MCP server (stdio transport)

Environment variables:
  KNOWLEDGE_ROOT   path to the knowledge base directory (default: ./knowledge)
  LOG_ROOT         path to debug logs (default: KNOWLEDGE_ROOT/logs)
"""
from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

# ---------------------------------------------------------------------------
# Optional DuckDB import – gracefully degrade if not installed
# ---------------------------------------------------------------------------
try:
    import duckdb
    _HAS_DUCKDB = True
except ImportError:  # pragma: no cover
    duckdb = None  # type: ignore[assignment]
    _HAS_DUCKDB = False

# ---------------------------------------------------------------------------
# MCP SDK imports
# ---------------------------------------------------------------------------
try:
    from mcp.server import Server, NotificationOptions
    from mcp.server.models import InitializationOptions
    import mcp.types as types
    _HAS_MCP_SDK = True
except ImportError:  # pragma: no cover
    # Provide stub types so the module can be inspected without the SDK.
    class Server:  # type: ignore[no-redef]
        def __init__(self, name: str):
            self.name = name
        def list_tools(self):
            return lambda fn: fn
        def call_tool(self):
            return lambda fn: fn
    class types:
        class Tool:
            def __init__(self, **kwargs):
                pass
        class TextContent:
            def __init__(self, text: str, **kwargs):
                self.text = text
    NotificationOptions = None  # type: ignore[assignment]
    InitializationOptions = None  # type: ignore[assignment]
    _HAS_MCP_SDK = False

# ---------------------------------------------------------------------------
# Paths & configuration
# ---------------------------------------------------------------------------

DEFAULT_KNOWLEDGE_ROOT = os.path.join(os.path.dirname(__file__), "knowledge")
KNOWLEDGE_ROOT = Path(os.environ.get("KNOWLEDGE_ROOT", DEFAULT_KNOWLEDGE_ROOT))

DEFAULT_LOG_ROOT = KNOWLEDGE_ROOT / "logs"
LOG_ROOT = Path(os.environ.get("LOG_ROOT", str(DEFAULT_LOG_ROOT)))

# Hybrid ranking weights
BM25_WEIGHT = float(os.environ.get("BM25_WEIGHT", "0.5"))
VECTOR_WEIGHT = float(os.environ.get("VECTOR_WEIGHT", "0.5"))

# Hashed‑vector dimensions
VECTOR_DIM = int(os.environ.get("VECTOR_DIM", "256"))

# Knowledge refresh interval (seconds)
REFRESH_SECONDS = float(os.environ.get("KNOWLEDGE_REFRESH_SECONDS", "300"))

# ---------------------------------------------------------------------------
# Server instance
# ---------------------------------------------------------------------------

server = Server("support-engineer")

# ---------------------------------------------------------------------------
# Global knowledge base state (lazy init)
# ---------------------------------------------------------------------------

_kb: Optional["KnowledgeBase"] = None
_kb_last_refresh: float = 0.0

# ---------------------------------------------------------------------------
# Plain‑text helpers
# ---------------------------------------------------------------------------

def _tokenize(text: str) -> List[str]:
    """Lowercase alphanumeric tokenisation."""
    return re.findall(r"[a-z0-9]+", text.lower())


def _text_to_vector(text: str, dimensions: int = VECTOR_DIM) -> List[float]:
    """Deterministic hashed sparse‑dense vector."""
    tokens = _tokenize(text)
    vec = [0.0] * dimensions
    for token in tokens:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        idx = h % dimensions
        vec[idx] += 1.0
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _bm25(
    query_tokens: Sequence[str],
    doc_tokens: Sequence[str],
    avg_doc_len: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    freq: Dict[str, int] = {}
    for t in doc_tokens:
        freq[t] = freq.get(t, 0) + 1
    dl = len(doc_tokens)
    score = 0.0
    for qt in query_tokens:
        f = freq.get(qt, 0)
        if f:
            score += (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / (avg_doc_len or 1)))
    return score


# ---------------------------------------------------------------------------
# Knowledge base (DuckDB CSV + plain‑text files)
# ---------------------------------------------------------------------------

@dataclass
class Doc:
    """A single searchable document."""
    source: str          # relative path or table name
    row: int = 0
    excerpt: str = ""
    text: str = ""
    tokens: Tuple[str, ...] = ()
    vector: Tuple[float, ...] = ()


@dataclass
class SourceIndex:
    """Pre‑computed index for one CSV or one text chunk collection."""
    path: Path
    docs: List[Doc] = field(default_factory=list)
    avg_doc_len: float = 1.0


class KnowledgeBase:
    """DuckDB‑backed knowledge base with BM25 + vector hybrid ranking."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.indexes: List[SourceIndex] = []
        self._conn: Any = None
        if _HAS_DUCKDB:
            self._conn = duckdb.connect(":memory:")
        self._last_load = 0.0

    def needs_refresh(self) -> bool:
        return (time.time() - self._last_load) >= REFRESH_SECONDS

    def load(self) -> int:
        """(Re)build all indexes.  Returns total document count."""
        self.indexes = []
        if not self.root.exists():
            self._last_load = time.time()
            return 0

        # ---- CSV files ----
        for csv_path in sorted(self.root.rglob("*.csv")):
            idx = self._index_csv(csv_path)
            if idx is not None:
                self.indexes.append(idx)

        # ---- Plain text files ----
        for text_path in sorted(self.root.rglob("*")):
            if not text_path.is_file() or text_path.suffix.lower() in {
                ".csv", ".pyc", ".zip", ".gz", ".png", ".jpg", ".jpeg", ".gif", ".ico",
                ".exe", ".dll", ".so", ".dylib",
            }:
                continue
            idx = self._index_text_file(text_path)
            if idx is not None:
                self.indexes.append(idx)

        self._last_load = time.time()
        return sum(len(idx.docs) for idx in self.indexes)

    def _index_csv(self, path: Path) -> Optional[SourceIndex]:
        if not _HAS_DUCKDB:
            return self._index_csv_fallback(path)
        try:
            table_name = _safe_table_name(path.name)
            self._conn.execute(
                f"CREATE OR REPLACE TABLE {table_name} AS "
                f"SELECT * FROM read_csv_auto('{path}', ignore_errors=true)"
            )
            cols_result = self._conn.execute(f"DESCRIBE {table_name}").fetchall()
            columns = [row[0] for row in cols_result]
            rows = self._conn.execute(
                f"SELECT * FROM {table_name} LIMIT 5000"
            ).fetchall()
            if not rows:
                return None

            docs: List[Doc] = []
            total_len = 0
            for i, row in enumerate(rows):
                text = _row_to_text(row, columns)
                tokens = tuple(_tokenize(text))
                total_len += len(tokens)
                docs.append(Doc(
                    source=f"{path.name}#{i}",
                    row=i,
                    excerpt=_row_excerpt(row, columns, 200),
                    text=text,
                    tokens=tokens,
                    vector=tuple(_text_to_vector(text)),
                ))
            avgdl = total_len / len(docs) if docs else 1.0
            return SourceIndex(path=path, docs=docs, avg_doc_len=avgdl)
        except Exception:
            return self._index_csv_fallback(path)

    def _index_csv_fallback(self, path: Path) -> Optional[SourceIndex]:
        """Plain CSV reader (no DuckDB)."""
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
            if not rows:
                return None
            docs: List[Doc] = []
            total_len = 0
            for i, row in enumerate(rows):
                text = " ".join(str(v) for v in row.values())
                tokens = tuple(_tokenize(text))
                total_len += len(tokens)
                docs.append(Doc(
                    source=f"{path.name}#{i}",
                    row=i,
                    excerpt=_row_excerpt(row, list(row.keys()), 200),
                    text=text,
                    tokens=tokens,
                    vector=tuple(_text_to_vector(text)),
                ))
            avgdl = total_len / len(docs) if docs else 1.0
            return SourceIndex(path=path, docs=docs, avg_doc_len=avgdl)
        except Exception:
            return None

    def _index_text_file(self, path: Path) -> Optional[SourceIndex]:
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
            if not text.strip():
                return None
            # Split into chunks of ~200 words with 50‑word overlap
            words = text.split()
            chunk_size = 200
            overlap = 50
            chunks: List[str] = []
            start = 0
            while start < len(words):
                chunk = " ".join(words[start:start + chunk_size])
                chunks.append(chunk)
                start += chunk_size - overlap
            if not chunks:
                return None

            docs: List[Doc] = []
            total_len = 0
            rel_path = str(path.relative_to(self.root))
            for i, chunk in enumerate(chunks):
                search_text = f"{rel_path}\n{chunk}"
                tokens = tuple(_tokenize(search_text))
                total_len += len(tokens)
                docs.append(Doc(
                    source=f"{rel_path}#chunk{i}",
                    row=i,
                    excerpt=chunk[:300],
                    text=search_text,
                    tokens=tokens,
                    vector=tuple(_text_to_vector(search_text)),
                ))
            avgdl = total_len / len(docs) if docs else 1.0
            return SourceIndex(path=path, docs=docs, avg_doc_len=avgdl)
        except Exception:
            return None

    def search(
        self,
        query: str,
        max_results: int = 20,
    ) -> List[Dict[str, Any]]:
        """Hybrid BM25 + vector search."""
        if not self.indexes:
            return []

        query_tokens = _tokenize(query)
        query_vec = _text_to_vector(query)

        scored: List[Tuple[float, Doc]] = []
        for idx in self.indexes:
            for doc in idx.docs:
                bm = _bm25(query_tokens, doc.tokens, idx.avg_doc_len)
                vec = _cosine(query_vec, list(doc.vector)) if doc.vector else 0.0
                hybrid = BM25_WEIGHT * bm + VECTOR_WEIGHT * vec
                if hybrid > 0:
                    scored.append((hybrid, doc))

        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            {
                "source": d.source,
                "score": round(s, 4),
                "excerpt": d.excerpt,
            }
            for s, d in scored[:max_results]
        ]


# ---------------------------------------------------------------------------
# CSV helpers (fallback mode)
# ---------------------------------------------------------------------------

def _safe_table_name(filename: str) -> str:
    """Derive a safe SQL table name from a filename."""
    name = Path(filename).stem
    name = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    if name and name[0].isdigit():
        name = "_" + name
    return name or "data"


def _row_to_text(row: Sequence[Any], columns: Sequence[str]) -> str:
    parts = []
    for col, val in zip(columns, row):
        if val is not None:
            parts.append(f"{col}: {val}")
    return " ".join(parts)


def _row_excerpt(row: Sequence[Any], columns: Sequence[str], max_len: int = 200) -> str:
    text = " ".join(str(v) for v in row if v is not None)
    return text[:max_len]


# ---------------------------------------------------------------------------
# Log helpers
# ---------------------------------------------------------------------------

def _log_files() -> List[Dict[str, Any]]:
    if not LOG_ROOT.exists():
        return []
    results: List[Dict[str, Any]] = []
    for path in LOG_ROOT.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".log", ".txt", ".out", ".err"}:
            stat = path.stat()
            results.append({
                "file": str(path.relative_to(LOG_ROOT)),
                "size_bytes": stat.st_size,
                "last_modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
            })
    return results


def _log_search(keyword: str, max_matches: int = 50) -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    for lf in _log_files():
        file_path = LOG_ROOT / lf["file"]
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                for line_num, line in enumerate(f, 1):
                    if keyword.lower() in line.lower():
                        results.append({
                            "file": lf["file"],
                            "line": line_num,
                            "content": line.strip(),
                        })
                        if len(results) >= max_matches:
                            return results
        except Exception:
            continue
    return results


def _log_tail(lines: int = 20) -> Dict[str, List[str]]:
    tails: Dict[str, List[str]] = {}
    for lf in _log_files():
        file_path = LOG_ROOT / lf["file"]
        try:
            with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                all_lines = f.readlines()
                tails[lf["file"]] = [ln.rstrip() for ln in all_lines[-lines:]]
        except Exception:
            tails[lf["file"]] = ["[error reading file]"]
    return tails


# ---------------------------------------------------------------------------
# Knowledge base access
# ---------------------------------------------------------------------------

def _get_kb() -> KnowledgeBase:
    global _kb, _kb_last_refresh
    if _kb is None:
        _kb = KnowledgeBase(KNOWLEDGE_ROOT)
    if _kb.needs_refresh():
        _kb.load()
    return _kb


# ---------------------------------------------------------------------------
# MCP tools
# ---------------------------------------------------------------------------

if _HAS_MCP_SDK:
    @server.list_tools()
    async def handle_list_tools() -> List[types.Tool]:
        return [
            types.Tool(
                name="search_knowledge",
                description=(
                    "Search the knowledge base with hybrid BM25 + vector ranking. "
                    "Returns matching documents with relevance scores."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string",
                            "description": "Natural‑language query or keywords.",
                        },
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum results to return (default 20).",
                        },
                    },
                    "required": ["query"],
                },
            ),
            types.Tool(
                name="analyze_issue",
                description=(
                    "Analyze a support issue description to classify its type, "
                    "detect API‑related signals, and identify relevant platform components."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "subject": {
                            "type": "string",
                            "description": "Ticket or issue subject line.",
                        },
                        "description": {
                            "type": "string",
                            "description": "Full issue description / body.",
                        },
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional list of tags or labels.",
                        },
                    },
                    "required": ["subject"],
                },
            ),
            types.Tool(
                name="build_triage_context",
                description=(
                    "Assemble a triage evidence bundle: knowledge matches + "
                    "issue classification.  Use this before drafting a response."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Issue subject."},
                        "description": {"type": "string", "description": "Issue description."},
                        "tags": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tags.",
                        },
                        "max_evidence": {
                            "type": "integer",
                            "description": "Max knowledge results (default 10).",
                        },
                    },
                    "required": ["subject"],
                },
            ),
            types.Tool(
                name="suggest_resolution",
                description=(
                    "Suggest a resolution draft based on knowledge base matches. "
                    "This is a heuristic assistant — always review before sending."
                ),
                inputSchema={
                    "type": "object",
                    "properties": {
                        "subject": {"type": "string", "description": "Issue subject."},
                        "description": {"type": "string", "description": "Issue description."},
                        "max_evidence": {
                            "type": "integer",
                            "description": "Max knowledge results to consider (default 10).",
                        },
                    },
                    "required": ["subject"],
                },
            ),
            types.Tool(
                name="list_knowledge_files",
                description="List all files in the knowledge base directory.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "extension": {
                            "type": "string",
                            "description": "Optional file extension filter (e.g. '.csv').",
                        },
                    },
                },
            ),
            types.Tool(
                name="debug_logs",
                description="Inspect support‑related debug logs.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "action": {
                            "type": "string",
                            "enum": ["list", "search", "tail"],
                            "description": "'list' files, 'search' for keyword, or 'tail' recent lines.",
                        },
                        "keyword": {
                            "type": "string",
                            "description": "Search term (required when action='search').",
                        },
                        "tail_lines": {
                            "type": "integer",
                            "description": "Lines to show when action='tail' (default 20).",
                        },
                    },
                    "required": ["action"],
                },
            ),
        ]

    @server.call_tool()
    async def handle_call_tool(name: str, arguments: dict) -> List[types.TextContent]:
        try:
            result = _dispatch(name, arguments)
            return [types.TextContent(text=json.dumps(result, indent=2, default=str))]
        except Exception as exc:
            return [types.TextContent(text=json.dumps({"error": str(exc)}, indent=2))]
else:
    # Non‑MCP fallback (for testing / inspection)
    pass


def _dispatch(name: str, args: dict) -> Dict[str, Any]:
    if name == "search_knowledge":
        kb = _get_kb()
        return {
            "results": kb.search(
                query=args["query"],
                max_results=args.get("max_results", 20),
            )
        }

    if name == "analyze_issue":
        return _analyze_issue(
            subject=args.get("subject", ""),
            description=args.get("description", ""),
            tags=args.get("tags"),
        )

    if name == "build_triage_context":
        return _build_triage_context(
            subject=args.get("subject", ""),
            description=args.get("description", ""),
            tags=args.get("tags"),
            max_evidence=args.get("max_evidence", 10),
        )

    if name == "suggest_resolution":
        return _suggest_resolution(
            subject=args.get("subject", ""),
            description=args.get("description", ""),
            max_evidence=args.get("max_evidence", 10),
        )

    if name == "list_knowledge_files":
        ext = args.get("extension", "")
        files: List[str] = []
        if KNOWLEDGE_ROOT.exists():
            for p in sorted(KNOWLEDGE_ROOT.rglob("*")):
                if p.is_file() and (not ext or p.suffix == ext):
                    files.append(str(p.relative_to(KNOWLEDGE_ROOT)))
        return {"files": files, "total": len(files)}

    if name == "debug_logs":
        action = args.get("action", "list")
        if action == "list":
            return {"logs": _log_files()}
        if action == "search":
            return {"matches": _log_search(args.get("keyword", ""))}
        if action == "tail":
            return {"tails": _log_tail(args.get("tail_lines", 20))}
        raise ValueError(f"Unknown debug_logs action: {action}")

    raise ValueError(f"Unknown tool: {name}")


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

_API_SIGNAL_WORDS = {
    "api", "endpoint", "rest", "graphql", "webhook", "oauth", "token",
    "401", "403", "404", "429", "500", "502", "503",
    "rate limit", "timeout", "payload", "request body", "response",
    "client library", "sdk", "postman", "curl",
}

_ISSUE_CATEGORIES = [
    "api_integration",
    "billing",
    "account_management",
    "bug_report",
    "how_to",
    "performance",
    "other",
]


def _classify_issue(subject: str, description: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    """Heuristic issue classifier."""
    text = f"{subject} {description} {' '.join(tags or [])}".lower()
    tokens = set(_tokenize(text))

    api_score = len(tokens & _API_SIGNAL_WORDS)
    is_api = api_score >= 2

    # Simple keyword‑based categorisation
    if "billing" in tokens or "invoice" in tokens or "charge" in tokens:
        category = "billing"
    elif "login" in tokens or "password" in tokens or "account" in tokens:
        category = "account_management"
    elif "bug" in tokens or "error" in tokens or "fail" in tokens:
        category = "bug_report"
    elif "how" in tokens or "help" in tokens or "guide" in tokens:
        category = "how_to"
    elif "slow" in tokens or "timeout" in tokens or "performance" in tokens:
        category = "performance"
    elif is_api:
        category = "api_integration"
    else:
        category = "other"

    return {
        "category": category,
        "is_api_related": is_api,
        "api_signal_count": api_score,
        "matched_signals": sorted(tokens & _API_SIGNAL_WORDS),
    }


def _analyze_issue(subject: str, description: str, tags: Optional[List[str]] = None) -> Dict[str, Any]:
    classification = _classify_issue(subject, description, tags)
    return {
        "classification": classification,
        "input": {
            "subject": subject,
            "description_length": len(description),
            "tag_count": len(tags) if tags else 0,
        },
    }


def _build_triage_context(
    subject: str,
    description: str,
    tags: Optional[List[str]] = None,
    max_evidence: int = 10,
) -> Dict[str, Any]:
    kb = _get_kb()
    query = f"{subject} {description}"
    knowledge = kb.search(query, max_results=max_evidence)
    analysis = _analyze_issue(subject, description, tags)
    return {
        "analysis": analysis["classification"],
        "knowledge_matches": knowledge,
        "query": query,
    }


def _suggest_resolution(
    subject: str,
    description: str,
    max_evidence: int = 10,
) -> Dict[str, Any]:
    ctx = _build_triage_context(subject, description, max_evidence=max_evidence)
    matches = ctx["knowledge_matches"]

    suggestion = (
        "Review the knowledge matches below for similar historical issues. "
        "If a match is found, adapt its resolution steps to this case. "
        "If no close match exists, escalate to the next tier with the analysis attached."
    )

    top_excerpts = [m["excerpt"] for m in matches[:5]]
    return {
        "suggestion": suggestion,
        "top_matches": top_excerpts,
        "category": ctx["analysis"]["category"],
        "is_api_related": ctx["analysis"]["is_api_related"],
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not _HAS_MCP_SDK:
        print(
            "The MCP SDK is not installed.  Install it with:\n"
            "  pip install mcp\n",
            file=sys.stderr,
        )
        sys.exit(1)

    # Pre‑warm the knowledge base
    kb = _get_kb()
    doc_count = kb.load()
    print(f"Knowledge base loaded: {doc_count} documents", file=sys.stderr)

    # Run the MCP server (stdio transport)
    from mcp.server.stdio import stdio_server
    import asyncio

    async def _run() -> None:
        async with stdio_server() as (read_stream, write_stream):
            await server.run(
                read_stream,
                write_stream,
                InitializationOptions(
                    server_name="support-engineer",
                    server_version="2.0.0",
                    capabilities=server.get_capabilities(
                        notification_options=NotificationOptions(),
                        experimental_capabilities={},
                    ),
                ),
            )

    asyncio.run(_run())


if __name__ == "__main__":
    main()
