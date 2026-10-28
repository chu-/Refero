"""Lossless reads and version-checked writes for the data CLI.

These operations keep server fields intact, verify paginated collections, and
never retry writes or switch libraries implicitly. Existing picker operations
continue to use the original Zotero client methods.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.parse import urlparse

import requests

from .zotero_client import ZoteroClient


class DataError(Exception):
    """Describe a machine-readable failure without exposing request secrets."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def describe_library(client: ZoteroClient) -> dict[str, Any]:
    """Return the library identity used to bind a reviewed write plan."""
    endpoint = client._endpoint_override or (
        "http://localhost:23119/api" if client.is_local() else "https://api.zotero.org"
    )
    parsed = urlparse(endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise DataError(
            "INVALID_ENDPOINT",
            "Use an HTTP(S) endpoint without embedded credentials or query parameters.",
        )
    return {
        "mode": "local" if client.is_local() else "web",
        "library_type": client.library_type,
        "library_id": client.library_id,
        "endpoint": endpoint.rstrip("/"),
    }


def _request(
    client: ZoteroClient,
    method: str,
    path: str,
    *,
    params: Optional[dict[str, Any]] = None,
    payload: Any = None,
    headers: Optional[dict[str, str]] = None,
) -> requests.Response:
    library = describe_library(client)
    prefix = "groups" if client.library_type == "group" else "users"
    url = f"{library['endpoint']}/{prefix}/{client.library_id}/{path}"
    request_headers = {"Zotero-API-Version": "3"}
    if not client.is_local() and client.api_key:
        request_headers["Zotero-API-Key"] = client.api_key
    request_headers.update(headers or {})
    try:
        with requests.Session() as session:
            if client.is_local():
                session.trust_env = False
            response = session.request(
                method,
                url,
                params=params,
                json=payload,
                headers=request_headers,
                timeout=30,
                allow_redirects=False,
            )
    except requests.RequestException as exc:
        raise DataError(
            "REQUEST_FAILED",
            f"{method} request failed ({type(exc).__name__}); "
            "a failed write may have reached the server. Inspect the item before retrying.",
        ) from exc
    if response.status_code == 412:
        raise DataError(
            "VERSION_CONFLICT",
            "The server rejected the reviewed version or write token.",
        )
    if not 200 <= response.status_code < 300:
        raise DataError(
            "HTTP_ERROR", f"{method} request failed (HTTP {response.status_code})."
        )
    return response


def _decode(response: requests.Response) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise DataError(
            "INVALID_RESPONSE", "The server did not return valid JSON."
        ) from exc


def read_item(client: ZoteroClient, key: str) -> dict[str, Any]:
    """Return a complete Zotero item record, including its raw data and version."""
    record = _decode(_request(client, "GET", f"items/{key}"))
    if not isinstance(record, dict) or not isinstance(record.get("data"), dict):
        raise DataError(
            "INVALID_RESPONSE", "Expected a Zotero item record with a data mapping."
        )
    if record.get("key") != key:
        raise DataError(
            "INVALID_RESPONSE", "The returned item key does not match the request."
        )
    return record


def read_records(
    client: ZoteroClient,
    resource: str,
    key: Optional[str] = None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read all pages and report whether cardinality and snapshot were verified.

    Args:
        client: Selected Zotero library client.
        resource: ``items``, ``children``, ``collections``, or ``tags``.
        key: Parent item key for child records.

    Returns:
        Raw records and completeness evidence. Inconsistent pages raise DataError.
    """
    if resource not in {"items", "children", "collections", "tags"}:
        raise ValueError("Unsupported record resource")
    path = f"items/{key}/children" if resource == "children" else resource
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    totals: set[int] = set()
    versions: set[str] = set()
    start = 0
    while True:
        response = _request(client, "GET", path, params={"limit": 100, "start": start})
        batch = _decode(response)
        if not isinstance(batch, list) or any(
            not isinstance(record, dict) for record in batch
        ):
            raise DataError("INVALID_RESPONSE", "Expected a list of record mappings.")
        total = response.headers.get("Total-Results")
        version = response.headers.get("Last-Modified-Version")
        try:
            if total:
                totals.add(int(total))
        except ValueError as exc:
            raise DataError(
                "INVALID_RESPONSE", "Invalid Total-Results header."
            ) from exc
        if version:
            versions.add(version)
        if len(totals) > 1 or len(versions) > 1:
            raise DataError(
                "SNAPSHOT_CHANGED",
                "The library changed between pages; repeat the read.",
            )
        for record in batch:
            identity = record.get("tag") if resource == "tags" else record.get("key")
            if not isinstance(identity, str) or not identity:
                raise DataError("INVALID_RESPONSE", "A record is missing its identity.")
            if resource == "tags":
                # Zotero may return the same name once per manual/automatic type.
                # Keep both records and their metadata instead of collapsing them.
                tag_type = record.get("type", (record.get("meta") or {}).get("type"))
                identity = json.dumps([identity, tag_type])
            if identity in seen:
                raise DataError(
                    "INCONSISTENT_PAGES",
                    "Missing or duplicate record identity across pages.",
                )
            seen.add(identity)
            records.append(record)
        start += len(batch)
        expected = next(iter(totals), None)
        if expected is not None and start > expected:
            raise DataError(
                "INCONSISTENT_PAGES", "Returned records exceed the server total."
            )
        if not batch or len(batch) < 100 or start == expected:
            break
    expected = next(iter(totals), None)
    if expected is not None and start != expected:
        raise DataError(
            "INCOMPLETE_READ", "Pagination ended before the server's declared total."
        )
    verified = expected is not None and bool(versions)
    return records, {
        "complete": verified,
        "returned": len(records),
        "total": expected,
        "pagination_exhausted": True,
        "snapshot_version": next(iter(versions), None),
        "reason": None
        if verified
        else "Server omitted total or snapshot evidence; completeness is unverified.",
    }


def patch_item(
    client: ZoteroClient,
    key: str,
    updates: dict[str, Any],
    version: int,
) -> None:
    """Apply only reviewed fields against the reviewed version, without retries."""
    _request(
        client,
        "PATCH",
        f"items/{key}",
        payload=updates,
        headers={"If-Unmodified-Since-Version": str(version)},
    )


def create_note(
    client: ZoteroClient,
    data: dict[str, Any],
    token: str,
) -> str:
    """Create one reviewed child note using a replay-resistant write token."""
    result = _decode(
        _request(
            client,
            "POST",
            "items",
            payload=[data],
            headers={"Zotero-Write-Token": token},
        )
    )
    if not isinstance(result, dict) or result.get("failed"):
        raise DataError(
            "CREATE_FAILED",
            "The server did not confirm note creation; inspect before retrying.",
        )
    entry = (result.get("successful") or {}).get("0")
    key = entry.get("key") if isinstance(entry, dict) else None
    key = key or (result.get("success") or {}).get("0")
    if not isinstance(key, str) or not key:
        raise DataError(
            "CREATE_UNVERIFIED", "The creation response has no confirmed item key."
        )
    return key
