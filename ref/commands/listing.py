from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
import json
from typing import Any, Optional

import typer

from ..backends.zotero_client import NormalizedItem, normalize_item
from ..config import load as load_config
from ..console import console as rich_console
from ..picker.fzf import FzfPicker
from ._registry import CommandBinding
from ..utils.collections import (
    build_collection_index,
    collection_path_from_index,
    resolve_collection,
)
from ..utils.display import (
    active_color_styles,
    format_collection_paths,
    format_listing_line,
    format_tags,
    header_filter,
    match_filter,
)
from ..utils.query import coalesce_query
from ..utils.search import (
    SearchFilters,
    build_search_filters,
    LIST_FILTER_OPTIONS,
    PICK_FILTER_OPTIONS,
)
from ..utils.zotero import zot_from_config

__all__ = ["pick", "list_cmd", "view", "preview", "COMMANDS"]


@dataclass(frozen=True)
class CollectionDisplay:
    """Pre-formatted collection metadata for a normalized Zotero item."""

    paths: tuple[str, ...]
    joined: str
    joined_stylized: str
    primary_path: str
    primary_label: str
    primary_label_stylized: str
    sort_value: str


def build_item_collection_display(
    item: NormalizedItem,
    collection_index: Mapping[str, Mapping[str, Optional[str]]],
    *,
    separator: str = " / ",
    stylize_paths: bool = False,
    stylize_primary: bool = False,
) -> CollectionDisplay:
    """Return sorted collection path strings and formatted display variants."""

    if not collection_index or not item.collections:
        primary_stub = format_collection_paths((" ",), stylize=stylize_primary)
        joined_stylized = (
            format_collection_paths((" ",), stylize=True, separator=separator)
            if stylize_paths
            else ""
        )
        return CollectionDisplay(
            paths=(),
            joined="",
            joined_stylized=joined_stylized,
            primary_path="",
            primary_label="",
            primary_label_stylized=primary_stub,
            sort_value=primary_stub,
        )

    seen: set[str] = set()
    resolved: list[str] = []
    for col_key in item.collections or []:
        path = collection_path_from_index(collection_index, col_key)
        if not path:
            continue
        normalized = path.strip()
        lowered = normalized.lower()
        if not normalized or lowered in seen:
            continue
        seen.add(lowered)
        resolved.append(normalized)

    if not resolved:
        primary_stub = format_collection_paths((" ",), stylize=stylize_primary)
        joined_stylized = (
            format_collection_paths((" ",), stylize=True, separator=separator)
            if stylize_paths
            else ""
        )
        return CollectionDisplay(
            paths=(),
            joined="",
            joined_stylized=joined_stylized,
            primary_path="",
            primary_label="",
            primary_label_stylized=primary_stub,
            sort_value=primary_stub,
        )

    resolved.sort(key=str.lower)
    joined = format_collection_paths(resolved, stylize=False, separator=separator)
    joined_stylized = (
        format_collection_paths(resolved, stylize=True, separator=separator)
        if stylize_paths
        else ""
    )
    primary_path = resolved[0]
    primary_label = format_collection_paths((primary_path,), stylize=False)
    primary_label_stylized = (
        format_collection_paths((primary_path,), stylize=True)
        if stylize_primary
        else primary_label
    )
    sort_value = primary_label_stylized if stylize_primary else primary_label
    return CollectionDisplay(
        paths=tuple(resolved),
        joined=joined,
        joined_stylized=joined_stylized,
        primary_path=primary_path,
        primary_label=primary_label,
        primary_label_stylized=primary_label_stylized,
        sort_value=sort_value,
    )


def _resolve_filters(
    positional_query: Optional[list[str]],
    query: Optional[str],
    limit: Optional[int],
    collection: Optional[str],
    tag: Optional[str],
    author: Optional[str],
    title: Optional[str],
    year: Optional[str],
    doi: Optional[str],
) -> SearchFilters:
    return build_search_filters(
        positional_query,
        query,
        limit,
        collection,
        tag,
        author,
        title,
        year,
        doi,
    )


def _resolve_limit(
    filters_limit: Optional[int],
    default_limit: Optional[int],
    *,
    require_positive: bool,
) -> Optional[int]:
    """Determine the effective item fetch limit, preserving legacy behaviour."""

    if isinstance(filters_limit, int):
        if require_positive and filters_limit > 0:
            return filters_limit
        if not require_positive and filters_limit != 0:
            return filters_limit
    if isinstance(default_limit, int):
        if require_positive and default_limit > 0:
            return default_limit
        if not require_positive and default_limit != 0:
            return default_limit
    return None


def _prepare_listing_data(
    positional_query: Optional[list[str]],
    query: Optional[str],
    limit: Optional[int],
    collection: Optional[str],
    tag: Optional[str],
    author: Optional[str],
    title: Optional[str],
    year: Optional[str],
    doi: Optional[str],
    *,
    default_limit: Optional[int],
    require_positive_limit: bool,
    group_by_collection: bool,
) -> tuple[SearchFilters, list[NormalizedItem], dict[str, dict[str, Optional[str]]]]:
    """Build filters, fetch normalized items, and collect collection metadata."""

    zot = zot_from_config()
    filters = _resolve_filters(
        positional_query,
        query,
        limit,
        collection,
        tag,
        author,
        title,
        year,
        doi,
    )
    effective_limit = _resolve_limit(
        filters.limit,
        default_limit,
        require_positive=require_positive_limit,
    )
    effective_query = coalesce_query(filters.query, list(filters.positional_terms))
    collection_key = resolve_collection(zot, filters.collection)
    items = zot.list_items(
        limit=effective_limit,
        q=effective_query,
        collection=collection_key,
        tag=filters.tag,
        author=filters.author,
        title=filters.title,
        year=filters.year,
        doi=filters.doi,
    )
    normalized_items = [normalize_item(item) for item in items]
    collection_index = build_collection_index(zot) if group_by_collection else {}
    return filters, normalized_items, collection_index


def pick(
    positional_query: Optional[list[str]] = PICK_FILTER_OPTIONS.positional,
    query: Optional[str] = PICK_FILTER_OPTIONS.query,
    limit: Optional[int] = PICK_FILTER_OPTIONS.limit,
    collection: Optional[str] = PICK_FILTER_OPTIONS.collection,
    tag: Optional[str] = PICK_FILTER_OPTIONS.tag,
    author: Optional[str] = PICK_FILTER_OPTIONS.author,
    title: Optional[str] = PICK_FILTER_OPTIONS.title,
    year: Optional[str] = PICK_FILTER_OPTIONS.year,
    doi: Optional[str] = PICK_FILTER_OPTIONS.doi,
    group_by_collection: bool = typer.Option(True, "--group-by-collection", help="Sort and visually group by collection path"),
):
    """Interactively pick items."""

    cfg = load_config()
    _filters, normalized_items, collection_index = _prepare_listing_data(
        positional_query,
        query,
        limit,
        collection,
        tag,
        author,
        title,
        year,
        doi,
        default_limit=cfg.picker_limit,
        require_positive_limit=True,
        group_by_collection=group_by_collection,
    )

    normalized: list[dict[str, Any]] = []
    for normalized_item in normalized_items:
        doc: dict[str, Any] = {
            "key": normalized_item.key,
            "citekey": normalized_item.citekey,
            "title": normalized_item.title,
            "author": "; ".join(normalized_item.creators),
            "year": normalized_item.year,
            "tags": list(normalized_item.tags),
        }
        if group_by_collection:
            display = build_item_collection_display(
                normalized_item,
                collection_index,
                stylize_primary=True,
            )
            doc["title"] = f"{display.primary_label_stylized} | {doc['title']}"
            doc["_collection_path"] = display.sort_value
        normalized.append(doc)

    if group_by_collection:
        normalized.sort(key=lambda data: str(data.get("_collection_path", "(No Collection)")).lower())
    selections = FzfPicker()(normalized, header_filter=header_filter, match_filter=match_filter, default_index=0, context="pick")
    for selection in selections:
        typer.echo(json.dumps(selection, ensure_ascii=False))


def list_cmd(
    positional_query: Optional[list[str]] = LIST_FILTER_OPTIONS.positional,
    query: Optional[str] = LIST_FILTER_OPTIONS.query,
    limit: Optional[int] = LIST_FILTER_OPTIONS.limit,
    collection: Optional[str] = LIST_FILTER_OPTIONS.collection,
    tag: Optional[str] = LIST_FILTER_OPTIONS.tag,
    author: Optional[str] = LIST_FILTER_OPTIONS.author,
    title: Optional[str] = LIST_FILTER_OPTIONS.title,
    year: Optional[str] = LIST_FILTER_OPTIONS.year,
    doi: Optional[str] = LIST_FILTER_OPTIONS.doi,
    group_by_collection: bool = typer.Option(False, "--group-by-collection", help="Group output by collection path"),
    json_output: bool = typer.Option(
        False, "--json",
        help="Print a versioned JSON result instead of formatted lines",
    ),
) -> None:
    """List matching items as formatted lines or an opt-in JSON envelope."""

    _, normalized_items, collection_index = _prepare_listing_data(
        positional_query,
        query,
        limit,
        collection,
        tag,
        author,
        title,
        year,
        doi,
        default_limit=50,
        require_positive_limit=False,
        group_by_collection=group_by_collection,
    )

    if json_output:
        items = [asdict(item) for item in normalized_items]
        if group_by_collection:
            for item, normalized_item in zip(items, normalized_items):
                item["collection_paths"] = list(
                    build_item_collection_display(normalized_item, collection_index).paths
                )
        typer.echo(json.dumps(
            {"schema_version": 1, "items": items, "count": len(items)},
            ensure_ascii=False,
        ))
        return

    styles = active_color_styles()
    key_style = styles.get("key", "")
    title_style = styles.get("title", "")
    author_style = styles.get("author", "")
    venue_style = styles.get("venue", "")

    for normalized_item in normalized_items:
        which = normalized_item.journalAbbreviation or normalized_item.publication or normalized_item.itemType
        authors_part = "; ".join(normalized_item.creators)
        tags_str = format_tags(normalized_item.tags)
        formatted_paths = ""
        if group_by_collection:
            display = build_item_collection_display(normalized_item, collection_index)
            formatted_paths = display.joined
        line = format_listing_line(
            key=normalized_item.key,
            title=normalized_item.title,
            authors=authors_part,
            venue=which,
            year=normalized_item.year,
            tags=tags_str,
            key_style=key_style,
            title_style=title_style,
            author_style=author_style,
            venue_style=venue_style,
            collection_paths=formatted_paths,
        )
        rich_console.print(line, markup=True)


def view(
    key: str,
    json_only: bool = typer.Option(
        False,
        "--json-only",
        "--json",
        help="Print normalized item JSON without the formatted header.",
    ),
):
    """Show normalized item metadata and optionally emit only JSON.

    Args:
        key: Zotero item key.
        json_only: Emit only the JSON payload when set.
    """

    zot = zot_from_config()
    item = zot.get_item(key)
    normalized_item = normalize_item(item)
    collection_index = build_collection_index(zot)
    collection_display = build_item_collection_display(normalized_item, collection_index)
    collection_paths = list(collection_display.paths)

    if not json_only:
        styles = active_color_styles()
        author_style = styles.get("author", "")
        title_style = styles.get("title", "")
        venue_style = styles.get("venue", "")
        which = (
            normalized_item.journalAbbreviation
            or normalized_item.publication
            or normalized_item.itemType
        )
        header = (
            (f"[{title_style}]{normalized_item.title}[/]" if title_style else normalized_item.title)
            + " — "
            + (f"[{venue_style}]{which}[/]" if venue_style else which)
            + " — "
            + (
                f"[{author_style}]{'; '.join(normalized_item.creators)}[/]"
                if author_style
                else "; ".join(normalized_item.creators)
            )
        )
        rich_console.print(header, markup=True)

    data = dict(normalized_item.__dict__)
    data["collection_paths"] = collection_paths
    payload = json.dumps(data, ensure_ascii=False, indent=2)
    if json_only:
        typer.echo(payload)
    else:
        rich_console.print(payload, markup=False)


def preview(key: str):
    """Print a compact, single-line metadata preview for an item."""
    zot = zot_from_config()
    item = zot.get_item(key)
    normalized_item = normalize_item(item)
    creators_str = "; ".join(normalized_item.creators)
    tags_str = format_tags(normalized_item.tags)
    which = normalized_item.journalAbbreviation or normalized_item.publication or normalized_item.itemType
    styles = active_color_styles()
    author_style = styles.get("author", "")
    title_style = styles.get("title", "")
    venue_style = styles.get("venue", "")
    title_part = (f"[{title_style}]{normalized_item.title}[/]" if title_style else normalized_item.title)
    venue_part = (f"[{venue_style}]{which}[/]" if venue_style else which)
    authors_part = (f"[{author_style}]{creators_str}[/]" if author_style else creators_str)
    line = f"{normalized_item.year} • {venue_part} • {authors_part} • {tags_str} • {title_part}"
    rich_console.print(line, markup=True)


COMMANDS = (
    CommandBinding(callback=pick, name="pick", interactive=True),
    CommandBinding(callback=list_cmd, name="list"),
    CommandBinding(callback=view, name="view"),
    CommandBinding(callback=preview, name="preview"),
)
