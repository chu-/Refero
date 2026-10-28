"""Editing commands focused on related-item management."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import typer

from .._registry import CommandBinding
from ...backends.zotero_client import normalize_item
from ...config import load as load_config
from ...picker.selectors import PickerSession, select_related_items
from ...utils.collections import parse_editor_kv_csv
from ...utils.editor import edit_text_in_editor
from ...utils.zotero import retry_or_escalate, zot_from_config

__all__ = ["relate", "RELATION_COMMANDS"]


def relate(
    key: str = typer.Argument(..., help="Item key to edit related items for"),
    pick: bool = typer.Option(
        False,
        "--pick",
        help="Use interactive picker to choose related items. Use Tab to toggle selection, Enter/Esc to confirm/cancel",
    ),
    limit: Optional[int] = typer.Option(
        None,
        "--limit",
        "-l",
        help="Max items to fetch for picking (defaults to config picker_limit)",
    ),
) -> None:
    """Edit the item's related links via either an editor or picker flow."""

    zot = zot_from_config()
    cfg = load_config()
    library_type = cfg.zotero.library_type
    library_id = cfg.zotero.library_id

    def _relation_uri(item_key: str) -> str:
        base = "http://zotero.org/groups" if library_type == "group" else "http://zotero.org/users"
        return f"{base}/{library_id}/items/{item_key}"

    try:
        item = zot.get_item(key)
    except Exception as exc:
        typer.echo(f"Failed to load item {key}: {exc}", err=True)
        raise typer.Exit(code=1)

    data = item.get("data", {}) or {}
    relations = dict(data.get("relations", {}) or {})
    cur_dc: List[str] = list(relations.get("dc:relation", []) or [])

    def _uri_to_key(uri: str) -> str:
        try:
            if "/items/" in uri:
                return uri.rsplit("/items/", 1)[1]
        except Exception:
            pass
        return ""

    cur_related_keys: List[str] = []
    seen: set[str] = set()
    for uri in cur_dc:
        key_candidate = _uri_to_key(str(uri))
        if key_candidate and key_candidate not in seen and key_candidate != key:
            seen.add(key_candidate)
            cur_related_keys.append(key_candidate)

    header_lines: List[str] = [
        "# Edit related item KEYS (comma-separated).",
        "# Lines starting with # are ignored.",
        "# Example: related: ABCD1234, EFGH5678",
        "#",
        "# Currently related (for reference):",
    ]
    for rk in cur_related_keys:
        try:
            rit = zot.get_item(rk)
            rn = normalize_item(rit)
            header_lines.append(
                f"# - {rk} — {rn.year} {('; '.join(rn.creators))[:30]}: {rn.title[:80]}"
            )
        except Exception:
            header_lines.append(f"# - {rk}")
    header_lines.append("")
    header = "\n".join(header_lines)

    cleaned: List[str] = []

    if pick:
        cfg_limit = limit if isinstance(limit, int) and limit > 0 else cfg.picker_limit
        session = PickerSession(zot)
        selected_keys = select_related_items(
            session,
            cur_related_keys,
            key,
            limit=cfg_limit,
        )
        if selected_keys is None:
            return
        cleaned = selected_keys
    else:
        template = f"related: {', '.join(cur_related_keys)}\n"
        text = edit_text_in_editor("ref-relate-", header, template)
        if text is None:
            return
        parsed = parse_editor_kv_csv(text, ["related"])
        target_keys = parsed.get("related", [])
        seen2: set[str] = set()
        for value in target_keys:
            trimmed = value.strip()
            if trimmed and trimmed != key and trimmed not in seen2:
                seen2.add(trimmed)
                cleaned.append(trimmed)

    for related_key in cleaned:
        try:
            _ = zot.get_item(related_key)
        except Exception:
            typer.echo(f"Related key not found: {related_key}", err=True)
            raise typer.Exit(code=1)

    prev_set = set(cur_related_keys)
    next_set = set(cleaned)
    to_add = sorted(next_set - prev_set)
    to_remove = sorted(prev_set - next_set)

    if not to_add and not to_remove:
        typer.echo("No changes; nothing to update")
        return

    new_relations = dict(relations)
    new_relations["dc:relation"] = [_relation_uri(target) for target in cleaned]

    try:
        retry_or_escalate(
            zot,
            "update",
            lambda client: client.update_item_data(
                key, {"relations": new_relations}
            ),
        )
    except Exception as exc:
        typer.echo(f"Update relations failed for {key}: {exc}", err=True)
        raise typer.Exit(code=1)

    def _ensure_recipient_has(me: str, other: str) -> None:
        it = zot.get_item(other)
        data_other = it.get("data", {}) or {}
        rels = dict(data_other.get("relations", {}) or {})
        dc_list: List[str] = list(rels.get("dc:relation", []) or [])
        uri = _relation_uri(me)
        if uri not in dc_list:
            dc_list.append(uri)
            rels["dc:relation"] = dc_list
            retry_or_escalate(
                zot,
                "update",
                lambda client: client.update_item_data(other, {"relations": rels}),
            )

    def _remove_recipient_link(me: str, other: str) -> None:
        it = zot.get_item(other)
        data_other = it.get("data", {}) or {}
        rels = dict(data_other.get("relations", {}) or {})
        dc_list: List[str] = list(rels.get("dc:relation", []) or [])
        uri = _relation_uri(me)
        new_dc = [value for value in dc_list if value != uri]
        if len(new_dc) != len(dc_list):
            rels["dc:relation"] = new_dc
            retry_or_escalate(
                zot,
                "update",
                lambda client: client.update_item_data(other, {"relations": rels}),
            )

    for related_key in to_add:
        try:
            _ensure_recipient_has(key, related_key)
        except Exception as exc:
            typer.echo(f"Failed to add reciprocal relation on {related_key}: {exc}", err=True)
            raise typer.Exit(code=1)

    for related_key in to_remove:
        try:
            _remove_recipient_link(key, related_key)
        except Exception as exc:
            typer.echo(f"Failed to remove reciprocal relation on {related_key}: {exc}", err=True)
            raise typer.Exit(code=1)

    typer.echo("Relations updated")


RELATION_COMMANDS = (
    CommandBinding(callback=relate, name="relate", aliases=("link",), interactive=True),
)
