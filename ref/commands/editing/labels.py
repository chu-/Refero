"""Editing commands covering tag and collection updates."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple, Union
from typing import Literal

import typer

from .._registry import CommandBinding
from ...backends.zotero_client import ZoteroClient
from ...config import load as load_config
from ...picker.selectors import PickerSession, select_collections, select_tags
from ...utils.collections import (
    build_collection_index,
    ordered_union,
    parse_editor_kv_csv,
    resolve_collection,
)
from ...utils.editor import edit_text_in_editor
from ...utils.items import apply_bulk_updates, load_items_for_keys
from ...utils.zotero import zot_from_config

__all__ = [
    "build_editor_prompt",
    "label",
    "tag",
    "collection_cmd",
    "LABEL_COMMANDS",
]


LabelFieldName = Literal["collections", "tags"]

def build_editor_prompt(kind: str, field_values: Dict[str, List[str]]) -> Tuple[str, str]:
    """Build header and template strings for editor-based field updates."""

    header_lines = [
        f"# Edit {kind} for the item(s).",
        "# Provide comma-separated values. Lines starting with # are ignored.",
    ]
    field_hints = {
        "collections": "# collections: accept KEYS or NAMES (exact/partial).",
        "tags": "# tags: free text, comma-separated.",
    }
    for field in field_values:
        hint = field_hints.get(field)
        if hint:
            header_lines.append(hint)
    header = "\n".join(header_lines) + "\n\n"

    template_lines = [f"{field}: {', '.join(values)}" for field, values in field_values.items()]
    template = "\n".join(template_lines) + "\n"
    return header, template


def _existing_collection_keys(items_data: Dict[str, Dict[str, Any]], keys: List[str]) -> List[str]:
    collections_all: List[List[str]] = []
    for key in keys:
        data = items_data.get(key, {})
        collections_all.append(list((data.get("collections", []) or [])))
    return ordered_union(collections_all)


def _existing_tag_texts(items_data: Dict[str, Dict[str, Any]], keys: List[str]) -> List[str]:
    tags_all: List[List[str]] = []
    for key in keys:
        data = items_data.get(key, {})
        tag_objs = data.get("tags", []) or []
        tags_all.append(
            [str(tag_obj.get("tag", "")) for tag_obj in tag_objs if tag_obj]
        )
    return ordered_union(tags_all)


def _apply_bulk_field_updates(
    zot: ZoteroClient,
    unique_keys: List[str],
    items_data: Dict[str, Dict[str, Any]],
    *,
    collections: Optional[List[str]] = None,
    tags: Optional[List[str]] = None,
) -> None:
    if collections is None and tags is None:
        return

    def _build_updates(_: Dict[str, Any]) -> Dict[str, Any]:
        updates: Dict[str, Any] = {}
        if collections is not None:
            updates["collections"] = list(collections)
        if tags is not None:
            updates["tags"] = [{"tag": tag} for tag in tags]
        return updates

    def _extract_state(item_data: Dict[str, Any]) -> Dict[str, Any]:
        state: Dict[str, Any] = {}
        if collections is not None:
            state["collections"] = list(item_data.get("collections", []) or [])
        if tags is not None:
            cur_tags: List[Dict[str, Any]] = list(item_data.get("tags", []) or [])
            state["tags"] = [
                {"tag": str(tag_obj.get("tag", ""))}
                for tag_obj in cur_tags
            ]
        return state

    apply_bulk_updates(zot, unique_keys, items_data, _build_updates, _extract_state)


def _collection_names_from_index(
    index: Dict[str, Dict[str, Optional[str]]],
    keys: List[str],
) -> List[str]:
    names: List[str] = []
    for key in keys:
        data = index.get(key) or {}
        name = str(data.get("name", "")) if data else ""
        names.append(name.strip() or key)
    return names


def _unique_field_order(fields: Sequence[LabelFieldName]) -> List[LabelFieldName]:
    order: List[LabelFieldName] = []
    seen: set[str] = set()
    for field in fields:
        if field not in {"collections", "tags"}:
            raise ValueError(f"Unsupported label field: {field}")
        if field not in seen:
            order.append(field)
            seen.add(field)
    return order


def _run_label_flow(
    keys: List[str],
    *,
    fields: Sequence[LabelFieldName],
    pick: bool,
    limit: Optional[int],
    editor_kind: str,
    editor_prefix: str,
) -> None:
    field_order = _unique_field_order(fields)
    if not field_order:
        return

    zot = zot_from_config()
    unique_keys, items_data = load_items_for_keys(zot, keys)

    needs_collections = "collections" in field_order
    needs_tags = "tags" in field_order

    session_client: Union[ZoteroClient, PickerSession]
    session_client = PickerSession(zot) if pick else zot

    existing_col_keys: List[str] = []
    existing_col_names: List[str] = []
    if needs_collections:
        existing_col_keys = _existing_collection_keys(items_data, unique_keys)
        col_index = build_collection_index(session_client)
        existing_col_names = _collection_names_from_index(col_index, existing_col_keys)

    existing_tags: List[str] = _existing_tag_texts(items_data, unique_keys) if needs_tags else []

    if pick:
        session = session_client if isinstance(session_client, PickerSession) else PickerSession(zot)

        selected_tags: Optional[List[str]] = None
        if needs_tags:
            effective_limit = limit if isinstance(limit, int) and limit > 0 else load_config().picker_limit
            selected_tags = select_tags(session, existing_tags, limit=effective_limit)
            if selected_tags is None:
                return

        selected_col_keys: Optional[List[str]] = None
        if needs_collections:
            selected_col_keys = select_collections(session, existing_col_keys)
            if selected_col_keys is None:
                return

        _apply_bulk_field_updates(
            zot,
            unique_keys,
            items_data,
            collections=selected_col_keys if needs_collections else None,
            tags=selected_tags if needs_tags else None,
        )
        return

    field_values: Dict[str, List[str]] = {}
    for field in field_order:
        if field == "collections":
            field_values[field] = existing_col_names
        else:
            field_values[field] = existing_tags

    header, template = build_editor_prompt(editor_kind, field_values)
    text = edit_text_in_editor(editor_prefix, header, template)
    if text is None:
        return

    parsed = parse_editor_kv_csv(text, list(field_order))

    target_col_keys: Optional[List[str]] = None
    if needs_collections:
        target_col_inputs = parsed.get("collections", [])
        target_col_keys = []
        for value in target_col_inputs:
            try:
                resolved = resolve_collection(zot, value)
            except typer.BadParameter as exc:
                typer.echo(str(exc), err=True)
                raise typer.Exit(code=1)
            if resolved not in target_col_keys:
                target_col_keys.append(resolved)

    target_tags: Optional[List[str]] = parsed.get("tags", []) if needs_tags else None

    _apply_bulk_field_updates(
        zot,
        unique_keys,
        items_data,
        collections=target_col_keys if needs_collections else None,
        tags=target_tags if needs_tags else None,
    )


def label(
    keys: List[str] = typer.Argument(
        ...,
        help="Item key(s) to edit labels for",
    ),
    pick: bool = typer.Option(
        False,
        "--pick",
        help="Use interactive pickers to choose collections and tags. Use Tab to select/unselect labels, then Enter/Esc to confirm/cancel selection",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Max entries to show in pickers (defaults to config picker_limit)",
    ),
) -> None:
    """Edit tags and collections for one or more items."""

    _run_label_flow(
        keys,
        fields=("collections", "tags"),
        pick=pick,
        limit=limit,
        editor_kind="collections and tags",
        editor_prefix="ref-label-bulk-",
    )


def tag(
    keys: List[str] = typer.Argument(
        ...,
        help="Item key(s) to edit tags for",
    ),
    pick: bool = typer.Option(
        False,
        "--pick",
        help="Use interactive picker to choose tags. Use Tab to select/unselect tags, then Enter/Esc to confirm/cancel selection",
    ),
) -> None:
    """Edit tags for one or more items."""

    _run_label_flow(
        keys,
        fields=("tags",),
        pick=pick,
        limit=None,
        editor_kind="tags",
        editor_prefix="ref-tag-bulk-",
    )


def collection_cmd(
    keys: List[str] = typer.Argument(
        ...,
        help="Item key(s) to edit collections for",
    ),
    pick: bool = typer.Option(
        False,
        "--pick",
        help="Use interactive picker to choose collections. Use Tab to select/unselect collections, then Enter/Esc to confirm/cancel selection",
    ),
) -> None:
    """Edit collections for one or more items."""

    _run_label_flow(
        keys,
        fields=("collections",),
        pick=pick,
        limit=None,
        editor_kind="collections",
        editor_prefix="ref-collection-bulk-",
    )


LABEL_COMMANDS = (
    CommandBinding(callback=label, name="label", interactive=True),
    CommandBinding(callback=collection_cmd, name="collection", aliases=("coll",), interactive=True),
    CommandBinding(callback=tag, name="tag", interactive=True),
)
