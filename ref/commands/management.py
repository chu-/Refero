from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence
from typing import NamedTuple

import feedparser
import requests
import typer

from ..backends.zotero_client import normalize_item
from ..config import load as load_config
from ..utils.collections import (
    merge_collections,
    merge_tags,
    normalize_flag_inputs,
    resolve_collection,
    resolve_collections_list,
)
from ..utils.items import is_html_attachment, is_pdf_attachment, resolve_attachment_path
from ..utils.interaction import is_non_interactive, require_write_confirmation
from ..utils.system import open_path as system_open_path
from ..utils.zotero import (
    echo_network_error,
    retry_or_escalate,
    run_with_network_retry,
    zot_from_config,
)
from ._registry import CommandBinding
from ..utils.search import EXPORT_FILTER_OPTIONS, build_search_filters
from ..utils.query import coalesce_query

__all__ = ["open", "export", "set_cmd", "add", "delete", "COMMANDS"]


logger = logging.getLogger(__name__)


HTML_SNAPSHOT_PRIORITY_MODES = {"imported_file", "imported_url"}
HTML_URL_LINK_MODES = {"imported_url", "linked_url"}


class AttachmentCandidate(NamedTuple):
    """Describes an attachment that can be opened from Zotero storage."""

    key: Optional[str]
    data: Optional[Dict[str, Any]]


def _extract_attachment_link(data: Dict[str, Any]) -> Optional[str]:
    """Return the URL or local path associated with an attachment payload."""

    link = data.get("url") or data.get("path")
    return link if isinstance(link, str) and link else None


def _find_pdf_candidate(
    item_key: str,
    item_data: Dict[str, Any],
    children: Sequence[Dict[str, Any]],
) -> Optional[AttachmentCandidate]:
    """Locate the first PDF attachment candidate for the given item."""

    if is_pdf_attachment(item_data):
        return AttachmentCandidate(item_key, item_data)
    for child in children:
        child_data = child.get("data", {}) or {}
        if is_pdf_attachment(child_data):
            return AttachmentCandidate(child.get("key"), child_data)
    return None


def _find_html_targets(
    item_key: str,
    item_data: Dict[str, Any],
    children: Sequence[Dict[str, Any]],
) -> tuple[Optional[AttachmentCandidate], Optional[str]]:
    """Return the preferred HTML snapshot candidate and an optional URL fallback."""

    html_candidate: Optional[AttachmentCandidate] = None
    best_priority = 2
    html_url = _extract_attachment_link(item_data) if is_html_attachment(item_data) else None
    if is_html_attachment(item_data):
        html_candidate = AttachmentCandidate(item_key, item_data)
        best_priority = 1

    for child in children:
        child_data = child.get("data", {}) or {}
        link_mode = str(child_data.get("linkMode", "")).lower()
        if is_html_attachment(child_data):
            priority = 0 if link_mode in HTML_SNAPSHOT_PRIORITY_MODES else 1
            if html_candidate is None or priority <= best_priority:
                candidate_key = child.get("key") or (html_candidate.key if html_candidate else None)
                html_candidate = AttachmentCandidate(candidate_key, child_data)
                best_priority = priority
            link = _extract_attachment_link(child_data)
            if link:
                html_url = link
        elif link_mode in HTML_URL_LINK_MODES:
            link = _extract_attachment_link(child_data)
            if link:
                html_url = link

    return html_candidate, html_url


def _resolve_candidate_path(
    storage_dir: Path,
    candidate: Optional[AttachmentCandidate],
) -> Optional[Path]:
    """Resolve the on-disk path for an attachment candidate, if available."""

    if not candidate or not candidate.key or candidate.data is None:
        return None
    return resolve_attachment_path(storage_dir, candidate.key, candidate.data)


def _resolve_item_url(item_data: Dict[str, Any]) -> Optional[str]:
    """Return the best URL for an item, falling back to DOI when necessary."""

    url_value = item_data.get("url") or item_data.get("URL")
    if url_value:
        return str(url_value)
    doi = item_data.get("DOI")
    if doi:
        return f"https://doi.org/{doi}"
    return None


def open(
    key: str,
    pdf: bool = typer.Option(False, "--pdf", help="Open the best PDF attachment only"),
    html: bool = typer.Option(False, "--html", help="Open HTML snapshot/attachment or linked URL only"),
    url_only: bool = typer.Option(False, "--url", help="Open the item's URL/DOI only"),
) -> None:
    """Open the best available attachment (PDF/HTML) or URL for an item."""

    zot = zot_from_config()
    cfg = load_config()
    storage_dir = cfg.zotero.storage_dir

    if int(bool(pdf)) + int(bool(html)) + int(bool(url_only)) > 1:
        raise typer.BadParameter("Options --pdf, --html, and --url are mutually exclusive")

    item = zot.get_item(key)
    item_data = item.get("data", {})
    children = zot.children(key)

    pdf_candidate = _find_pdf_candidate(key, item_data, children)
    html_candidate, html_url = _find_html_targets(key, item_data, children)
    item_url = _resolve_item_url(item_data)

    def _open_path(path: str) -> None:
        if not system_open_path(path):
            typer.echo(path)

    def _open_pdf(strict: bool) -> bool:
        if not pdf_candidate:
            if strict:
                typer.echo("No PDF attachment found", err=True)
                raise typer.Exit(code=1)
            return False
        pdf_path = _resolve_candidate_path(storage_dir, pdf_candidate)
        if pdf_path and pdf_path.exists():
            _open_path(str(pdf_path))
            return True
        if strict:
            message_path = str(pdf_path) if pdf_path else "unknown"
            typer.echo(
                f"PDF file not found in Zotero storage at: {message_path}",
                err=True,
            )
            raise typer.Exit(code=1)
        typer.echo("PDF attachment found but file not present in Zotero storage", err=True)
        return False

    def _open_html(strict: bool) -> bool:
        html_path = _resolve_candidate_path(storage_dir, html_candidate)
        if html_path and html_path.exists():
            _open_path(str(html_path))
            return True
        if html_url:
            _open_path(html_url)
            return True
        if strict:
            typer.echo("No HTML snapshot or URL attachment found", err=True)
            raise typer.Exit(code=1)
        return False

    if pdf:
        _open_pdf(strict=True)
        return
    if html:
        _open_html(strict=True)
        return
    if url_only:
        if not item_url:
            typer.echo("No item URL/DOI found", err=True)
            raise typer.Exit(code=1)
        _open_path(item_url)
        return

    if _open_pdf(strict=False):
        return
    if _open_html(strict=False):
        return

    url_value = item_url
    if not url_value:
        typer.echo("No attachment or URL to open", err=True)
        raise typer.Exit(code=1)
    _open_path(url_value)


def export(
    keys: Optional[list[str]] = typer.Argument(None, help="Item key(s) to export. Omit to use search filters."),
    format: str = typer.Option("bibtex", case_sensitive=False, help="bibtex|csljson|md"),
    out: Optional[Path] = typer.Option(None, "--out", help="Output file (default stdout)"),
    query: Optional[str] = EXPORT_FILTER_OPTIONS.query,
    limit: Optional[int] = EXPORT_FILTER_OPTIONS.limit,
    collection: Optional[str] = EXPORT_FILTER_OPTIONS.collection,
    tag: Optional[str] = EXPORT_FILTER_OPTIONS.tag,
    author: Optional[str] = EXPORT_FILTER_OPTIONS.author,
    title: Optional[str] = EXPORT_FILTER_OPTIONS.title,
    year: Optional[str] = EXPORT_FILTER_OPTIONS.year,
    doi: Optional[str] = EXPORT_FILTER_OPTIONS.doi,
) -> None:
    """Export items in BibTeX, CSL-JSON, or Markdown formats.

    Args:
        keys: Explicit item keys to export. If omitted, search filters are used.
        format: Target export format (``bibtex``, ``csljson``, or ``md``).
        out: Optional output path; defaults to stdout when omitted.
        query: Text search passed to the Zotero API.
        limit: Maximum number of items when filters are used.
        collection: Collection filter (accepts partial names or keys).
        tag: Tag substring filter.
        author: Author substring filter.
        title: Title substring filter.
        year: Year (exact or prefix) filter.
        doi: DOI substring filter.

    Raises:
        typer.BadParameter: If no keys or active filters are provided.
    """

    zot = zot_from_config()
    cfg = load_config()
    filters = build_search_filters(
        None,
        query,
        limit,
        collection,
        tag,
        author,
        title,
        year,
        doi,
    )

    def _unique_keys(values: Optional[list[str]]) -> list[str]:
        unique_keys: list[str] = []
        seen: set[str] = set()
        for value in values or []:
            trimmed = value.strip()
            if not trimmed or trimmed in seen:
                continue
            unique_keys.append(trimmed)
            seen.add(trimmed)
        return unique_keys

    selected_keys = _unique_keys(keys)

    filters_active = any(
        (
            filters.positional_terms,
            filters.query,
            filters.collection,
            filters.tag,
            filters.author,
            filters.title,
            filters.year,
            filters.doi,
        )
    )

    if filters_active:
        effective_query = coalesce_query(filters.query, list(filters.positional_terms))
        collection_key = resolve_collection(zot, filters.collection)
        fetch_limit = (
            filters.limit if isinstance(filters.limit, int) and filters.limit > 0 else cfg.picker_limit
        )
        fetched_items = zot.list_items(
            limit=fetch_limit,
            q=effective_query,
            collection=collection_key,
            tag=filters.tag,
            author=filters.author,
            title=filters.title,
            year=filters.year,
            doi=filters.doi,
        )
        fetched_keys = [normalize_item(item).key for item in fetched_items]
        selected_keys = _unique_keys(selected_keys + fetched_keys)

    if not selected_keys:
        raise typer.BadParameter("Provide item keys or search filters to export.")

    fmt = format.lower()
    if fmt == "bibtex":
        text = zot.export_bibtex(selected_keys)
        if out:
            out.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)
        return
    if fmt == "csljson":
        data = zot.export_csljson(selected_keys)
        text = json.dumps(data, ensure_ascii=False, indent=2)
        if out:
            out.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)
        return
    if fmt == "md":
        items = [normalize_item(zot.get_item(key_value)) for key_value in selected_keys]
        cites = [f"[@{it.citekey}]" for it in items]
        text = ", ".join(cites)
        if out:
            out.write_text(text, encoding="utf-8")
        else:
            typer.echo(text)
        return
    raise typer.BadParameter("Unsupported format")


def set_cmd(
    key: str = typer.Argument(..., help="Item key to update"),
    collections: List[str] = typer.Option([], "--collection", "-c", help="One or more collection KEYS or NAMES (repeat or comma-separate)"),
    tags: List[str] = typer.Option([], "--tag", "-g", help="Add one or more tags (repeat or comma-separate)"),
    attach_pdf: Optional[Path] = typer.Option(None, "--pdf", help="Attach a local PDF as a linked file"),
    collections_rm: List[str] = typer.Option([], "--collection-rm", help="Remove from collections (repeat or comma-separate; accepts key or name)"),
    tags_rm: List[str] = typer.Option([], "--tag-rm", help="Remove one or more tags (repeat or comma-separate)"),
    pdf_rm: bool = typer.Option(False, "--pdf-rm", help="Remove PDF attachment(s) from the item"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Acknowledge user approval for non-interactive writes"),
) -> None:
    """Set metadata on an existing item: tags, collections, and attachments."""

    require_write_confirmation("set", yes)
    if not (tags or collections or attach_pdf or collections_rm or tags_rm or pdf_rm):
        raise typer.BadParameter(
            "Provide at least one of --tag/--collection/--pdf or removal flags --tag-rm/--collection-rm/--pdf-rm"
        )

    zot = zot_from_config()

    try:
        item = zot.get_item(key)
    except Exception as exc:
        typer.echo(f"Failed to load item {key}: {exc}", err=True)
        raise typer.Exit(code=1)

    data = item.get("data", {}) or {}
    updates: Dict[str, Any] = {}

    if collections or collections_rm:
        existing_cols: List[str] = list(data.get("collections", []) or [])
        updates["collections"] = merge_collections(
            zot,
            existing_cols,
            normalize_flag_inputs(collections),
            normalize_flag_inputs(collections_rm),
        )

    if tags or tags_rm:
        existing_tag_objs: List[Dict[str, Any]] = list(data.get("tags", []) or [])
        existing_texts: List[str] = [str(tag.get("tag", "")) for tag in existing_tag_objs]
        merged_texts = merge_tags(
            existing_texts,
            normalize_flag_inputs(tags),
            normalize_flag_inputs(tags_rm),
        )
        updates["tags"] = [{"tag": value} for value in merged_texts]

    if updates:
        try:
            run_with_network_retry(
                lambda: retry_or_escalate(
                    zot,
                    "update",
                    lambda client: client.update_item_data(key, updates),
                ),
                prompt="Network/SSL error during update. Retry now?",
            )
        except Exception as exc:
            typer.echo(f"Update failed: {exc}", err=True)
            raise typer.Exit(code=1)
        typer.echo("Updated")

    if pdf_rm:
        try:
            children = zot.children(key)
        except Exception as exc:
            typer.echo(f"Failed to list attachments for {key}: {exc}", err=True)
            raise typer.Exit(code=1)

        pdf_keys: List[str] = []
        for child in children:
            child_data = child.get("data", {})
            if is_pdf_attachment(child_data):
                child_key = child.get("key")
                if isinstance(child_key, str) and child_key:
                    pdf_keys.append(child_key)
        if not pdf_keys:
            typer.echo("No PDF attachments to remove")
        else:
            try:
                retry_or_escalate(
                    zot,
                    "delete attachment",
                    lambda client: client.delete_items(pdf_keys),
                )
            except Exception as exc:
                typer.echo(f"Remove PDF failed: {exc}", err=True)
                raise typer.Exit(code=1)
            typer.echo("Removed PDF attachment(s): " + ", ".join(pdf_keys))

    if attach_pdf is not None:
        abs_path = Path(attach_pdf).expanduser().resolve()
        if not abs_path.exists():
            typer.echo(f"PDF not found: {abs_path}", err=True)
            raise typer.Exit(code=1)
        attachment: Dict[str, Any] = {
            "itemType": "attachment",
            "linkMode": "linked_file",
            "title": abs_path.name,
            "contentType": "application/pdf",
            "path": str(abs_path),
            "parentItem": key,
        }
        try:
            response = retry_or_escalate(
                zot,
                "create attachment",
                lambda client: client.create_items([attachment]),
            )
        except Exception as exc:
            typer.echo(f"Attach failed: {exc}", err=True)
            raise typer.Exit(code=1)
        successes = response.get("successful", {}) if isinstance(response, dict) else {}
        if successes:
            created = [value.get("key") for value in successes.values() if isinstance(value, dict)]
            if created:
                typer.echo("Attached: " + ", ".join(created))
                return
        failures = response.get("failed", {}) if isinstance(response, dict) else {}
        if failures:
            typer.echo("Attach failed: " + json.dumps(failures, ensure_ascii=False), err=True)
            raise typer.Exit(code=1)
        typer.echo("No response from Zotero when creating attachment", err=True)
        raise typer.Exit(code=1)


def add(
    doi: Optional[str] = typer.Option(None, "--doi", "-d", help="Add by DOI"),
    arxiv: Optional[str] = typer.Option(None, "--arxiv", "-x", help="Add by arXiv ID"),
    collections: List[str] = typer.Option([], "--collection", "-c", help="One or more collection KEYS or NAMES (repeat or comma-separate)"),
    tags: List[str] = typer.Option([], "--tag", "-g", help="Add one or more tags (repeat or comma-separate)"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Print the Zotero JSON and exit"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Acknowledge user approval for non-interactive creation"),
) -> None:
    """Add an item to Zotero by resolving metadata from DOI or arXiv."""

    if not dry_run:
        require_write_confirmation("add", yes)
    if bool(doi) + bool(arxiv) != 1:
        raise typer.BadParameter("Provide exactly one of --doi or --arxiv")

    zot = zot_from_config()

    col_keys = resolve_collections_list(zot, normalize_flag_inputs(collections))
    norm_tags = normalize_flag_inputs(tags)

    def _date_from_crossref(metadata: Dict[str, Any]) -> str:
        parts = metadata.get("issued", {}).get("date-parts") or metadata.get("created", {}).get("date-parts")
        if isinstance(parts, list) and parts:
            numbers = [str(part) for part in parts[0] if part is not None]
            return "-".join(numbers)
        raw = metadata.get("issued", {}).get("raw") or metadata.get("created", {}).get("raw")
        return str(raw) if raw else ""

    def _container_title(metadata: Dict[str, Any]) -> str:
        container = metadata.get("container-title")
        if isinstance(container, list):
            return container[0] if container else ""
        return container or ""

    def _container_title_short(metadata: Dict[str, Any]) -> str:
        container_short = metadata.get("container-title-short")
        if isinstance(container_short, list):
            return container_short[0] if container_short else ""
        if isinstance(container_short, str) and container_short:
            return container_short
        short = metadata.get("short-container-title")
        if isinstance(short, list):
            return short[0] if short else ""
        if isinstance(short, str) and short:
            return short
        abbreviation = metadata.get("journalAbbreviation")
        if isinstance(abbreviation, str):
            return abbreviation
        return ""

    def _map_crossref_item(metadata: Dict[str, Any]) -> Dict[str, Any]:
        type_map = {
            "journal-article": "journalArticle",
            "proceedings-article": "conferencePaper",
            "book": "book",
            "book-chapter": "bookSection",
            "report": "report",
            "posted-content": "journalArticle",
            "dataset": "document",
        }
        cr_type = str(metadata.get("type", "")).lower()
        item_type = type_map.get(cr_type, "journalArticle")
        item = zot.item_template(item_type)
        title = metadata.get("title")
        if isinstance(title, list):
            title = title[0] if title else ""
        item.update(
            {
                "title": title,
                "date": _date_from_crossref(metadata),
                "url": metadata.get("URL"),
                "DOI": metadata.get("DOI"),
            }
        )
        if item_type == "journalArticle":
            item["publicationTitle"] = _container_title(metadata)
            abbreviation = _container_title_short(metadata)
            if abbreviation:
                item["journalAbbreviation"] = abbreviation
            if metadata.get("volume"):
                item["volume"] = metadata.get("volume")
            if metadata.get("issue"):
                item["issue"] = metadata.get("issue")
            if metadata.get("page"):
                item["pages"] = metadata.get("page")
        creators: List[Dict[str, Any]] = []
        for creator in metadata.get("author", []) or []:
            first = creator.get("given") or ""
            last = creator.get("family") or ""
            if first or last:
                creators.append({"creatorType": "author", "firstName": first, "lastName": last})
        if creators:
            item["creators"] = creators
        if col_keys:
            item["collections"] = col_keys
        if norm_tags:
            item["tags"] = [{"tag": tag_value} for tag_value in norm_tags]
        return item

    def _fetch_crossref(doi_value: str) -> Dict[str, Any]:
        headers = {"Accept": "application/vnd.citationstyles.csl+json"}
        response = requests.get(f"https://doi.org/{doi_value}", headers=headers, timeout=30)
        response.raise_for_status()
        metadata = response.json()
        return _map_crossref_item(metadata)

    def _fetch_arxiv(arxiv_id: str) -> Dict[str, Any]:
        feed = feedparser.parse(f"http://export.arxiv.org/api/query?id_list={arxiv_id}")
        if not feed.entries:
            raise typer.BadParameter(f"No arXiv entry found for: {arxiv_id}")
        entry = feed.entries[0]
        item = zot.item_template("journalArticle")
        item.update(
            {
                "title": getattr(entry, "title", ""),
                "date": (getattr(entry, "published", "") or "").split("T")[0],
                "url": getattr(entry, "link", None),
                "publicationTitle": "arXiv",
                "journalAbbreviation": "arXiv",
                "abstractNote": getattr(entry, "summary", None),
            }
        )
        aid = getattr(entry, "id", "")
        if aid:
            item["archiveLocation"] = arxiv_id
        creators: List[Dict[str, Any]] = []
        for author in getattr(entry, "authors", []) or []:
            name = getattr(author, "name", "")
            parts = [part for part in name.split() if part]
            first = " ".join(parts[:-1]) if len(parts) > 1 else ""
            last = parts[-1] if parts else ""
            if first or last:
                creators.append({"creatorType": "author", "firstName": first, "lastName": last})
        if creators:
            item["creators"] = creators
        if col_keys:
            item["collections"] = col_keys
        if norm_tags:
            item["tags"] = [{"tag": tag_value} for tag_value in norm_tags]
        return item

    try:
        item = _fetch_crossref(doi) if doi else _fetch_arxiv(arxiv or "")
    except requests.exceptions.RequestException as exc:
        echo_network_error(exc)
        raise typer.Exit(code=1)

    if dry_run:
        typer.echo(json.dumps(item, ensure_ascii=False, indent=2))
        return

    try:
        response = retry_or_escalate(
            zot,
            "create",
            lambda client: client.create_items([item]),
        )
    except Exception as exc:
        lower = str(exc).lower()
        if ("403" in lower or "forbidden" in lower or "write access denied" in lower) and not zot.is_local():
            typer.echo(
                "Zotero Web API write denied (HTTP 403).\n"
                "- Ensure your API key has write access to your Personal Library.\n"
                "  Home -> Settings -> Security -> Edit Key -> Personal Library (https://www.zotero.org/settings/keys/edit/) -> Allow write access -> save\n"
                "- Then update config.yaml with the correct api_key."
                "- It's better to Backup database of Zotero.",
                err=True,
            )
            raise typer.Exit(code=1)
        raise

    successes = response.get("successful", {}) if isinstance(response, dict) else {}
    failures = response.get("failed", {}) if isinstance(response, dict) else {}
    if successes:
        created = [value.get("key") for value in successes.values() if isinstance(value, dict)]
        if created:
            typer.echo("Created: " + ", ".join(created))
            return
    if failures:
        typer.echo("Creation failed: " + json.dumps(failures, ensure_ascii=False), err=True)
        raise typer.Exit(code=1)
    typer.echo("No response from Zotero create_items", err=True)
    raise typer.Exit(code=1)


def delete(
    keys: List[str] = typer.Argument(..., help="Item key(s) to delete"),
    permanent: bool = typer.Option(
        False,
        "--permanent",
        "-P",
        help="Attempt permanent delete (Zotero typically moves items to Trash; empty Trash in Zotero to purge)",
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Do not prompt for confirmation"),
) -> None:
    """Delete one or more items, optionally bypassing confirmation.

    Args:
        keys: Item keys to delete or move to trash.
        permanent: When ``True`` attempt permanent deletion, otherwise trash.
        yes: Skip the interactive confirmation prompt when ``True``.
    """

    if is_non_interactive() and not yes:
        raise typer.BadParameter("delete requires --yes in non-interactive mode")

    zot = zot_from_config()
    logger.info("Deleting %s item(s); permanent=%s", len(keys), permanent)
    try:
        previews = []
        for key_value in keys[:3]:
            item = zot.get_item(key_value)
            normalized = normalize_item(item)
            previews.append(
                f"{normalized.key} — {normalized.year} {('; '.join(normalized.creators))[:30]}: {normalized.title[:60]}"
            )
    except Exception as exc:
        typer.echo(f"Failed to load items for confirmation: {exc}", err=True)
        raise typer.Exit(code=1)

    action = "permanently delete (moves to Trash)" if permanent else "move to Trash"
    if len(keys) == 1:
        prompt = f"Confirm {action} {previews[0]}?"
    else:
        more = "" if len(keys) <= 3 else f" and {len(keys) - 3} more"
        prompt = f"Confirm {action} {len(keys)} items: " + "; ".join(previews) + more + "?"
    if not yes and not typer.confirm(prompt, default=False):
        typer.echo("Aborted")
        logger.info("Deletion aborted by user prompt")
        raise typer.Exit(code=0)

    try:
        action_label = "delete" if permanent else "trash"

        def _operation(client) -> None:
            if permanent:
                client.delete_items(keys)
            else:
                client.trash_items(keys)

        retry_or_escalate(zot, action_label, _operation)
    except Exception as exc:
        logger.exception("Delete operation failed")
        typer.echo(f"Delete failed: {exc}", err=True)
        raise typer.Exit(code=1)

    typer.echo("Deleted" if permanent else "Moved to Trash")
    logger.info(
        "Deletion completed; permanent=%s; keys=%s",
        permanent,
        ", ".join(keys),
    )


COMMANDS = (
    CommandBinding(callback=open, name="open"),
    CommandBinding(callback=export, name="export"),
    CommandBinding(callback=set_cmd, name="set"),
    CommandBinding(callback=add, name="add"),
    CommandBinding(callback=delete, name="delete"),
)
