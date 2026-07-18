"""
paperless_mcp — MCP server for Paperless-ngx
=============================================

Connects Claude (or any MCP client) to a self-hosted Paperless-ngx
instance through its REST API. Version 1 is read-only by design.

Tools:
    - paperless_search_documents   full-text search with filters
    - paperless_get_document       metadata + OCR content of one document
    - paperless_list_taxonomy      tags / correspondents / document types / storage paths
    - paperless_get_statistics     archive-wide statistics

Configuration (environment variables, .env supported):
    PAPERLESS_URL    e.g. http://192.168.1.44:8010   (no trailing slash needed)
    PAPERLESS_TOKEN  API token of a (preferably read-only) Paperless user

Run:
    python server.py                          # stdio (Claude Desktop)
    python server.py --transport http         # streamable HTTP (remote/Docker)
    python server.py --transport http --port 8802

Author: Tiziano Coco — portfolio project
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from enum import Enum
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

load_dotenv()

PAPERLESS_URL: str = os.getenv("PAPERLESS_URL", "http://192.168.1.44:8010").rstrip("/")
PAPERLESS_TOKEN: str = os.getenv("PAPERLESS_TOKEN", "")

HTTP_TIMEOUT_SECONDS: float = 30.0
TAXONOMY_CACHE_TTL_SECONDS: float = 300.0
MAX_PAGE_SIZE: int = 100

MCP_HOST: str = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT: int = int(os.getenv("MCP_PORT", "8802"))

SERVER_INSTRUCTIONS = (
    "Read-only bridge to a self-hosted Paperless-ngx document archive. "
    "Use paperless_search_documents to find documents, then "
    "paperless_get_document to read one. Tag/correspondent/type filters accept "
    "human names (case-insensitive); use paperless_list_taxonomy to discover them."
)

mcp = FastMCP(
    "paperless_mcp",
    instructions=SERVER_INSTRUCTIONS,
    host=MCP_HOST,
    port=MCP_PORT,
)

# ---------------------------------------------------------------------------
# HTTP client + taxonomy cache (shared infrastructure)
# ---------------------------------------------------------------------------

_client: Optional[httpx.AsyncClient] = None

# kind -> {"fetched_at": float, "items": list[dict]}
_taxonomy_cache: dict[str, dict[str, Any]] = {}

_TAXONOMY_ENDPOINTS = {
    "tags": "/api/tags/",
    "correspondents": "/api/correspondents/",
    "document_types": "/api/document_types/",
    "storage_paths": "/api/storage_paths/",
}

_ORDERING_CHOICES = {
    "created", "-created", "added", "-added", "title", "-title",
    "correspondent__name", "-correspondent__name",
    "archive_serial_number", "-archive_serial_number",
}

_HIGHLIGHT_TAG_RE = re.compile(r"<[^>]+>")


def _get_client() -> httpx.AsyncClient:
    """Lazily create a shared async HTTP client authenticated against Paperless."""
    global _client
    if _client is None:
        if not PAPERLESS_TOKEN:
            raise RuntimeError(
                "PAPERLESS_TOKEN is not set. Create an API token in Paperless-ngx "
                "(profile menu -> 'API Auth Token' or Django admin) and export it, "
                "e.g. in a .env file next to server.py."
            )
        _client = httpx.AsyncClient(
            base_url=PAPERLESS_URL,
            headers={
                "Authorization": f"Token {PAPERLESS_TOKEN}",
                "Accept": "application/json",
            },
            timeout=HTTP_TIMEOUT_SECONDS,
        )
    return _client


async def _api_get(path: str, params: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """GET a Paperless endpoint and return parsed JSON, raising on HTTP errors."""
    client = _get_client()
    response = await client.get(path, params=params)
    response.raise_for_status()
    return response.json()


def _handle_error(exc: Exception) -> str:
    """Map exceptions to clear, actionable messages for the model/user."""
    if isinstance(exc, RuntimeError):
        return f"Error: {exc}"
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 401:
            return (
                "Error: authentication failed (401). The PAPERLESS_TOKEN is missing, "
                "wrong or revoked. Generate a new token in Paperless-ngx and update the env."
            )
        if status == 403:
            return (
                "Error: permission denied (403). The token's user cannot access this "
                "resource. Grant view permissions in Paperless-ngx."
            )
        if status == 404:
            return "Error: resource not found (404). Check that the ID exists (use paperless_search_documents first)."
        return f"Error: Paperless API returned HTTP {status}: {exc.response.text[:200]}"
    if isinstance(exc, httpx.ConnectError):
        return (
            f"Error: cannot reach Paperless-ngx at {PAPERLESS_URL}. "
            "Check PAPERLESS_URL, that the container is running, and network/firewall rules."
        )
    if isinstance(exc, httpx.TimeoutException):
        return f"Error: request to Paperless timed out after {HTTP_TIMEOUT_SECONDS}s. Try again or narrow the query."
    return f"Error: unexpected {type(exc).__name__}: {exc}"


async def _fetch_taxonomy(kind: str) -> list[dict[str, Any]]:
    """Fetch (with TTL cache) the full list of tags/correspondents/types/paths."""
    now = time.monotonic()
    cached = _taxonomy_cache.get(kind)
    if cached and now - cached["fetched_at"] < TAXONOMY_CACHE_TTL_SECONDS:
        return cached["items"]

    items: list[dict[str, Any]] = []
    params: dict[str, Any] = {"page_size": 1000, "page": 1}
    while True:
        data = await _api_get(_TAXONOMY_ENDPOINTS[kind], params=params)
        items.extend(data.get("results", []))
        if not data.get("next"):
            break
        params["page"] += 1

    _taxonomy_cache[kind] = {"fetched_at": now, "items": items}
    return items


async def _resolve_name_to_id(kind: str, name: str) -> int:
    """Resolve a human name (case-insensitive) to a Paperless ID.

    Tries exact match first, then unique substring match. Raises RuntimeError
    with an actionable message when nothing (or too much) matches.
    """
    items = await _fetch_taxonomy(kind)
    lowered = name.strip().lower()

    exact = [i for i in items if i["name"].lower() == lowered]
    if len(exact) == 1:
        return exact[0]["id"]

    partial = [i for i in items if lowered in i["name"].lower()]
    if len(partial) == 1:
        return partial[0]["id"]
    if len(partial) > 1:
        options = ", ".join(f"'{i['name']}'" for i in partial[:10])
        raise RuntimeError(
            f"Ambiguous {kind[:-1]} name '{name}': matches {options}. Use the exact name."
        )
    raise RuntimeError(
        f"No {kind[:-1]} named '{name}' found. "
        f"Call paperless_list_taxonomy(kind='{kind}') to see available names."
    )


async def _id_to_name_maps() -> dict[str, dict[int, str]]:
    """Build id->name lookup maps for formatting document results."""
    maps: dict[str, dict[int, str]] = {}
    for kind in ("tags", "correspondents", "document_types"):
        items = await _fetch_taxonomy(kind)
        maps[kind] = {i["id"]: i["name"] for i in items}
    return maps


def _clean_highlight(html: str) -> str:
    """Strip HTML tags from a Paperless search highlight snippet."""
    return _HIGHLIGHT_TAG_RE.sub("", html).strip()


def _format_document(
    doc: dict[str, Any],
    maps: dict[str, dict[int, str]],
    include_snippet: bool = False,
) -> dict[str, Any]:
    """Compact, human-readable representation of a document API object."""
    formatted: dict[str, Any] = {
        "id": doc.get("id"),
        "title": doc.get("title"),
        "created": (doc.get("created_date") or str(doc.get("created", "")))[:10],
        "added": str(doc.get("added", ""))[:10],
        "correspondent": maps["correspondents"].get(doc.get("correspondent")),
        "document_type": maps["document_types"].get(doc.get("document_type")),
        "tags": [maps["tags"].get(t, f"#{t}") for t in doc.get("tags", [])],
        "archive_serial_number": doc.get("archive_serial_number"),
        "original_file_name": doc.get("original_file_name"),
    }
    if include_snippet:
        hit = doc.get("__search_hit__") or {}
        if hit.get("highlights"):
            formatted["snippet"] = _clean_highlight(hit["highlights"])
        if hit.get("score") is not None:
            formatted["search_score"] = round(hit["score"], 3)
    return formatted


def _json(payload: Any) -> str:
    """Consistent JSON serialization for tool outputs."""
    return json.dumps(payload, ensure_ascii=False, indent=2, default=str)


# ---------------------------------------------------------------------------
# Tool: search documents
# ---------------------------------------------------------------------------

class SearchDocumentsInput(BaseModel):
    """Input model for paperless_search_documents."""
    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    query: Optional[str] = Field(
        default=None,
        description=(
            "Full-text search query. Supports Paperless syntax, e.g. 'enel 2024', "
            "'correspondent:enel', 'created:[2024-01-01 to 2024-12-31]'. "
            "Omit to list documents by date only."
        ),
        max_length=300,
    )
    tags: Optional[list[str]] = Field(
        default=None,
        description="Filter: documents must have ALL these tag names (case-insensitive), e.g. ['casa', 'bollette'].",
        max_length=10,
    )
    correspondent: Optional[str] = Field(
        default=None,
        description="Filter by correspondent name (case-insensitive), e.g. 'Enel'.",
        max_length=100,
    )
    document_type: Optional[str] = Field(
        default=None,
        description="Filter by document type name (case-insensitive), e.g. 'Bolletta'.",
        max_length=100,
    )
    created_after: Optional[str] = Field(
        default=None,
        description="Only documents created on/after this date, format YYYY-MM-DD.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    created_before: Optional[str] = Field(
        default=None,
        description="Only documents created on/before this date, format YYYY-MM-DD.",
        pattern=r"^\d{4}-\d{2}-\d{2}$",
    )
    ordering: str = Field(
        default="-created",
        description=(
            "Sort order. One of: created, -created, added, -added, title, -title, "
            "correspondent__name, -correspondent__name, archive_serial_number, "
            "-archive_serial_number. Prefix '-' = descending."
        ),
    )
    page: int = Field(default=1, description="Page number (1-based).", ge=1)
    page_size: int = Field(default=10, description="Results per page.", ge=1, le=MAX_PAGE_SIZE)


@mcp.tool(
    name="paperless_search_documents",
    annotations={
        "title": "Search Paperless documents",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def paperless_search_documents(params: SearchDocumentsInput) -> str:
    """Search or list documents in the Paperless-ngx archive.

    Combines optional full-text search with metadata filters (tags,
    correspondent, document type, creation date range). Names are resolved
    case-insensitively to Paperless IDs. Returns a compact paginated list;
    use paperless_get_document to read a specific result.

    Args:
        params (SearchDocumentsInput): query, tags, correspondent,
            document_type, created_after/before, ordering, page, page_size.

    Returns:
        str: JSON with {total_count, page, page_size, has_more, next_page,
            documents: [{id, title, created, added, correspondent,
            document_type, tags, archive_serial_number, original_file_name,
            snippet?, search_score?}]}.
    """
    try:
        if params.ordering not in _ORDERING_CHOICES:
            raise RuntimeError(
                f"Invalid ordering '{params.ordering}'. "
                f"Valid values: {', '.join(sorted(_ORDERING_CHOICES))}."
            )

        api_params: dict[str, Any] = {
            "page": params.page,
            "page_size": params.page_size,
            "ordering": params.ordering,
        }
        if params.query:
            api_params["query"] = params.query
        if params.correspondent:
            api_params["correspondent__id"] = await _resolve_name_to_id(
                "correspondents", params.correspondent
            )
        if params.document_type:
            api_params["document_type__id"] = await _resolve_name_to_id(
                "document_types", params.document_type
            )
        if params.tags:
            tag_ids = [await _resolve_name_to_id("tags", t) for t in params.tags]
            api_params["tags__id__all"] = ",".join(str(t) for t in tag_ids)
        if params.created_after:
            api_params["created__date__gte"] = params.created_after
        if params.created_before:
            api_params["created__date__lte"] = params.created_before

        data = await _api_get("/api/documents/", params=api_params)
        maps = await _id_to_name_maps()

        results = data.get("results", [])
        total = data.get("count", 0)
        has_more = bool(data.get("next"))
        payload = {
            "total_count": total,
            "page": params.page,
            "page_size": params.page_size,
            "has_more": has_more,
            "next_page": params.page + 1 if has_more else None,
            "documents": [
                _format_document(d, maps, include_snippet=bool(params.query))
                for d in results
            ],
        }
        return _json(payload)
    except Exception as exc:  # noqa: BLE001 — single formatting point
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool: get single document
# ---------------------------------------------------------------------------

class GetDocumentInput(BaseModel):
    """Input model for paperless_get_document."""
    model_config = ConfigDict(extra="forbid")

    document_id: int = Field(..., description="Paperless document ID (from search results).", ge=1)
    include_content: bool = Field(
        default=True,
        description="Include the OCR text content of the document.",
    )
    max_content_chars: int = Field(
        default=4000,
        description="Truncate OCR content to this many characters.",
        ge=200,
        le=50000,
    )


@mcp.tool(
    name="paperless_get_document",
    annotations={
        "title": "Get Paperless document details",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def paperless_get_document(params: GetDocumentInput) -> str:
    """Retrieve full metadata (and optionally OCR text) of one document.

    Args:
        params (GetDocumentInput): document_id, include_content,
            max_content_chars.

    Returns:
        str: JSON with {id, title, created, added, modified, correspondent,
            document_type, tags, archive_serial_number, original_file_name,
            notes, download_url, preview_url, content?, content_truncated?,
            content_total_chars?}.
    """
    try:
        doc = await _api_get(f"/api/documents/{params.document_id}/")
        maps = await _id_to_name_maps()

        payload = _format_document(doc, maps)
        payload.update(
            {
                "modified": str(doc.get("modified", ""))[:10],
                "notes": doc.get("notes") or [],
                "download_url": f"{PAPERLESS_URL}/api/documents/{params.document_id}/download/",
                "preview_url": f"{PAPERLESS_URL}/documents/{params.document_id}/details",
            }
        )

        if params.include_content:
            content = doc.get("content") or ""
            total = len(content)
            truncated = total > params.max_content_chars
            payload["content"] = content[: params.max_content_chars]
            payload["content_truncated"] = truncated
            payload["content_total_chars"] = total
            if truncated:
                payload["content_note"] = (
                    f"Content truncated to {params.max_content_chars} of {total} chars. "
                    "Raise max_content_chars to read more."
                )
        return _json(payload)
    except Exception as exc:  # noqa: BLE001
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool: list taxonomy (tags / correspondents / document types / storage paths)
# ---------------------------------------------------------------------------

class TaxonomyKind(str, Enum):
    """Kinds of organizational metadata in Paperless-ngx."""
    TAGS = "tags"
    CORRESPONDENTS = "correspondents"
    DOCUMENT_TYPES = "document_types"
    STORAGE_PATHS = "storage_paths"


class ListTaxonomyInput(BaseModel):
    """Input model for paperless_list_taxonomy."""
    model_config = ConfigDict(extra="forbid")

    kind: TaxonomyKind = Field(
        ...,
        description="Which list to return: 'tags', 'correspondents', 'document_types' or 'storage_paths'.",
    )
    name_filter: Optional[str] = Field(
        default=None,
        description="Optional case-insensitive substring filter on the name.",
        max_length=100,
    )


@mcp.tool(
    name="paperless_list_taxonomy",
    annotations={
        "title": "List Paperless tags/correspondents/types",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def paperless_list_taxonomy(params: ListTaxonomyInput) -> str:
    """List organizational metadata: tags, correspondents, document types or storage paths.

    Useful to discover exact names before filtering searches, or to get an
    overview of how the archive is organized (each entry includes its
    document_count).

    Args:
        params (ListTaxonomyInput): kind, optional name_filter.

    Returns:
        str: JSON with {kind, count, items: [{id, name, document_count}]}
            sorted by document_count descending.
    """
    try:
        items = await _fetch_taxonomy(params.kind.value)
        if params.name_filter:
            needle = params.name_filter.lower()
            items = [i for i in items if needle in i["name"].lower()]

        slim = sorted(
            (
                {
                    "id": i["id"],
                    "name": i["name"],
                    "document_count": i.get("document_count", 0),
                }
                for i in items
            ),
            key=lambda x: x["document_count"],
            reverse=True,
        )
        return _json({"kind": params.kind.value, "count": len(slim), "items": slim})
    except Exception as exc:  # noqa: BLE001
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Tool: statistics
# ---------------------------------------------------------------------------

@mcp.tool(
    name="paperless_get_statistics",
    annotations={
        "title": "Get Paperless archive statistics",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
async def paperless_get_statistics() -> str:
    """Return archive-wide statistics from Paperless-ngx.

    Returns:
        str: JSON with document totals, inbox count, counts of tags,
            correspondents and document types, plus file-type breakdown
            when available.
    """
    try:
        stats = await _api_get("/api/statistics/")
        payload = {
            "documents_total": stats.get("documents_total"),
            "documents_inbox": stats.get("documents_inbox"),
            "tag_count": stats.get("tag_count"),
            "correspondent_count": stats.get("correspondent_count"),
            "document_type_count": stats.get("document_type_count"),
            "storage_path_count": stats.get("storage_path_count"),
            "character_count": stats.get("character_count"),
            "file_type_counts": stats.get("document_file_type_counts"),
        }
        return _json({k: v for k, v in payload.items() if v is not None})
    except Exception as exc:  # noqa: BLE001
        return _handle_error(exc)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Parse CLI args and run the server with the chosen transport."""
    parser = argparse.ArgumentParser(description="MCP server for Paperless-ngx")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="stdio for local clients (Claude Desktop), http for remote access.",
    )
    parser.add_argument("--host", default=MCP_HOST, help="HTTP bind host (http transport).")
    parser.add_argument("--port", type=int, default=MCP_PORT, help="HTTP port (http transport).")
    args = parser.parse_args()

    if not PAPERLESS_TOKEN:
        print(
            "WARNING: PAPERLESS_TOKEN is not set — tools will return an auth error. "
            "Set it in the environment or in a .env file.",
            file=sys.stderr,
        )

    if args.transport == "stdio":
        mcp.run()
        return

    mcp.settings.host = args.host
    mcp.settings.port = args.port
    try:
        mcp.run(transport="streamable-http")
    except (ValueError, TypeError):
        # Older SDK versions use the underscore variant.
        mcp.run(transport="streamable_http")


if __name__ == "__main__":
    main()
