from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import typer

from ..backends.zotero_client import ZoteroClient
from .zotero import retry_or_escalate, run_with_network_retry

__all__ = [
    "load_items_for_keys",
    "apply_bulk_updates",
    "is_attachment_type",
    "is_pdf_attachment",
    "is_html_attachment",
    "resolve_attachment_path",
]


logger = logging.getLogger(__name__)


def load_items_for_keys(zot: ZoteroClient, keys: List[str]) -> Tuple[List[str], Dict[str, Dict[str, Any]]]:
    unique_keys: List[str] = []
    seen: set[str] = set()
    items_data: Dict[str, Dict[str, Any]] = {}
    for key in keys:
        if not isinstance(key, str) or not key.strip() or key in seen:
            continue
        try:
            item = zot.get_item(key)
        except Exception as exc:
            typer.echo(f"Failed to load item {key}: {exc}", err=True)
            raise typer.Exit(code=1)
        data = item.get("data", {}) or {}
        items_data[key] = data
        unique_keys.append(key)
        seen.add(key)
    return unique_keys, items_data


def apply_bulk_updates(
    zot: ZoteroClient,
    keys: List[str],
    items_data: Dict[str, Dict[str, Any]],
    updates_builder: Callable[[Dict[str, Any]], Dict[str, Any]],
    state_extractor: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Apply bulk updates across multiple Zotero items with retries and logging.

    Args:
        zot: Active Zotero client instance.
        keys: Ordered list of item keys to update.
        items_data: Mapping of keys to their current data payload.
        updates_builder: Callable producing the update payload for an item.
        state_extractor: Callable returning the current state to compare against.
    """

    updated: List[str] = []
    for key in keys:
        data = items_data[key]
        updates = updates_builder(data)
        current = state_extractor(data)
        if json.dumps(updates, sort_keys=True) == json.dumps(current, sort_keys=True):
            logger.debug("Skipping update for %s; no changes detected", key)
            continue
        try:
            run_with_network_retry(
                lambda: retry_or_escalate(
                    zot,
                    "update",
                    lambda client: client.update_item_data(key, updates),
                ),
                prompt=f"Network/SSL error updating {key}. Retry now?",
            )
        except Exception as exc:
            logger.exception("Update failed for %s", key)
            typer.echo(f"Update failed for {key}: {exc}", err=True)
            raise typer.Exit(code=1)
        updated.append(key)
        logger.info("Updated item %s", key)
    if updated:
        typer.echo("Updated: " + ", ".join(updated))
    else:
        typer.echo("No changes; nothing to update")


def is_attachment_type(
    data: Dict[str, Any],
    extensions: Iterable[str],
    content_substrings: Iterable[str],
) -> bool:
    """Return whether the attachment matches given extensions or content types.

    Args:
        data: Attachment data structure from Zotero.
        extensions: File extensions to match (case-insensitive, leading dot optional).
        content_substrings: Substrings to search for inside the attachment content type.

    Returns:
        ``True`` when the provided data entry represents an attachment with a
        matching extension or content type fragment, otherwise ``False``.
    """

    if data.get("itemType") != "attachment":
        return False

    def _normalize_extension(extension: str) -> str:
        trimmed = extension.lower().strip()
        if not trimmed:
            return ""
        return trimmed if trimmed.startswith(".") else f".{trimmed}"

    normalized_exts = tuple(_normalize_extension(ext) for ext in extensions)
    normalized_substrings = tuple(substr.lower() for substr in content_substrings)
    content_type = str(data.get("contentType") or "").lower()
    if any(substr and substr in content_type for substr in normalized_substrings):
        return True

    filename = str(data.get("filename") or "").lower()
    path_value = str(data.get("path") or "").lower()
    for ext in normalized_exts:
        if ext and (filename.endswith(ext) or path_value.endswith(ext)):
            return True
    return False


def is_pdf_attachment(data: Dict[str, Any]) -> bool:
    """Return ``True`` when the attachment represents a PDF file."""

    return is_attachment_type(data, (".pdf",), ("pdf",))


def is_html_attachment(data: Dict[str, Any]) -> bool:
    """Return ``True`` when the attachment represents an HTML snapshot."""

    return is_attachment_type(data, (".html", ".htm"), ("html", "text/html"))


def resolve_attachment_path(
    storage_dir: Path,
    attachment_key: Optional[str],
    data: Dict[str, Any],
) -> Optional[Path]:
    """Resolve an attachment's filesystem location.

    Args:
        storage_dir: Zotero storage directory configured for the user.
        attachment_key: Attachment key (if available) used for storage directory lookups.
        data: Attachment data payload from Zotero.

    Returns:
        The resolved filesystem path if it can be determined, otherwise ``None``.
    """

    path_value = data.get("path")
    if isinstance(path_value, str) and path_value:
        if path_value.startswith("storage:"):
            relative_path = path_value[len("storage:") :].lstrip("/\\")
            relative = Path(relative_path)
            if not relative.parts or ".." in relative.parts:
                return None
            legacy_path = storage_dir / relative
            if attachment_key and relative.parts[0] != attachment_key:
                candidate = storage_dir / attachment_key / relative
                if candidate.exists() or not legacy_path.exists():
                    return candidate
            return legacy_path
        absolute_path = Path(path_value).expanduser()
        if absolute_path.is_absolute():
            return absolute_path

    filename = data.get("filename")
    if isinstance(filename, str) and filename and isinstance(attachment_key, str) and attachment_key:
        return storage_dir / attachment_key / filename

    return None
