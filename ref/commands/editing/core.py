"""Editing commands focused on metadata and note content."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, List, Optional, TypeVar

import typer

from .._registry import CommandBinding
from ...backends.zotero_client import ZoteroClient
from ...picker.fzf import fzf_multiselect_lines
from ...utils.diffing import (
    print_item_data_diff_selected,
    prompt_concurrent_action,
    three_way_merge_item_data,
)
from ...utils.editor import (
    NoteEditorMode,
    edit_text_in_editor,
    get_note_editor_mode,
)
from ...utils.zotero import (
    load_latest_item_data_and_version,
    retry_or_escalate,
    run_with_network_retry,
    zot_from_config,
)

__all__ = [
    "edit",
    "picker_edit",
    "abstract",
    "extra",
    "note",
    "edit_item_text_field",
    "CORE_COMMANDS",
]

T = TypeVar("T")

PROMPTS_RETRY = {
    "default": "Network/SSL error during operation. Retry now?",
    "update": "Network/SSL error during update. Retry now?",
    "note_update": "Network/SSL error during note update. Retry now?",
}


def run_write_with_retry(
    *,
    zot: ZoteroClient,
    action_label: str,
    op: Callable[[ZoteroClient], T],
    prompt_key: str,
    failure_message: str,
) -> T:
    """Run a Zotero write with retry/escalation handling shared across edit flows."""

    prompt = PROMPTS_RETRY.get(prompt_key, PROMPTS_RETRY["default"])
    try:
        return run_with_network_retry(
            lambda: retry_or_escalate(zot, action_label, op),
            prompt=prompt,
        )
    except Exception as exc:
        typer.echo(f"{failure_message}: {exc}", err=True)
        raise typer.Exit(code=1)


def resolve_concurrency_loop(
    *,
    load_latest: Callable[[], tuple[dict, int]],
    get_base_version: Callable[[], int],
    apply_update: Callable[[], None],
    render_diff: Callable[[dict], None],
    prompt_action: Callable[[], str],
    handle_action: Callable[[str, dict, int], bool],
) -> None:
    """Resolve concurrent edits by reloading, diffing, and applying user decisions."""

    while True:
        latest_data, latest_version = load_latest()

        if latest_version == get_base_version():
            apply_update()
            return

        render_diff(latest_data)
        action = prompt_action()

        if handle_action(action, latest_data, latest_version):
            return


def edit(key: str) -> None:
    """Open item metadata in the editor and push updates to Zotero."""

    zot = zot_from_config()
    item = zot.get_item(key)
    data = item.get("data", {})
    base_version = int(item.get("version") or 0)

    fd, tmp_path_str = tempfile.mkstemp(prefix=f"ref-edit-{key}-", suffix=".json")
    tmp_path = Path(tmp_path_str)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, ensure_ascii=False, indent=2))
            handle.write("\n")

        editor = os.environ.get("VISUAL") or os.environ.get("EDITOR") or "vi"
        cmd = shlex.split(editor) + [str(tmp_path)]
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            typer.echo(f"Editor exited with non-zero status: {exc}", err=True)
            raise typer.Exit(code=1)

        try:
            new_text = tmp_path.read_text(encoding="utf-8")
            new_data = json.loads(new_text)
        except Exception as exc:
            typer.echo(f"Failed to parse edited JSON: {exc}", err=True)
            raise typer.Exit(code=1)

        if not isinstance(new_data, dict):
            typer.echo("Edited content must be a JSON object (the item's 'data')", err=True)
            raise typer.Exit(code=1)

        if json.dumps(new_data, sort_keys=True) == json.dumps(data, sort_keys=True):
            typer.echo("No changes; nothing to update")
            return

        def load_latest() -> tuple[dict, int]:
            return load_latest_item_data_and_version(zot, key)

        def get_base_version() -> int:
            return base_version

        def apply_update() -> None:
            run_write_with_retry(
                zot=zot,
                action_label="update",
                op=lambda client: client.update_item_data(key, new_data),
                prompt_key="update",
                failure_message="Update failed",
            )
            typer.echo("Updated")

        def render_diff(latest_data: dict) -> None:
            print_item_data_diff_selected(data, new_data, latest_data)

        def prompt_action() -> str:
            return prompt_concurrent_action(allow_merge=True, allow_remote=True, default="y")

        def handle_action(action: str, latest_data: dict, latest_version: int) -> bool:
            nonlocal base_version, data, new_data

            if action == "abort":
                typer.echo("Aborted")
                return True

            if action == "merge":
                merged = three_way_merge_item_data(data, new_data, latest_data)
                run_write_with_retry(
                    zot=zot,
                    action_label="update",
                    op=lambda client: client.update_item_data(key, merged),
                    prompt_key="update",
                    failure_message="Update failed",
                )
                typer.echo("Updated (merged)")
                return True

            if action == "yours":
                run_write_with_retry(
                    zot=zot,
                    action_label="update",
                    op=lambda client: client.update_item_data_force(
                        key,
                        new_data,
                        strict=True,
                        force=True,
                    ),
                    prompt_key="update",
                    failure_message="Update failed",
                )
                typer.echo("Updated (yours)")
                return True

            if action == "remote":
                tmp_path.write_text(
                    json.dumps(latest_data, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                try:
                    subprocess.run(cmd, check=True)
                except subprocess.CalledProcessError as exc:
                    typer.echo(f"Editor exited with non-zero status: {exc}", err=True)
                    raise typer.Exit(code=1)
                try:
                    new_text2 = tmp_path.read_text(encoding="utf-8")
                    new_data2 = json.loads(new_text2)
                except Exception as exc:
                    typer.echo(f"Failed to parse edited JSON: {exc}", err=True)
                    raise typer.Exit(code=1)
                if not isinstance(new_data2, dict):
                    typer.echo("Edited content must be a JSON object (the item's 'data')", err=True)
                    raise typer.Exit(code=1)
                if json.dumps(new_data2, sort_keys=True) == json.dumps(latest_data, sort_keys=True):
                    typer.echo("No changes; nothing to update")
                    return True
                data = latest_data
                base_version = latest_version
                new_data = new_data2
                return False

            typer.echo("Invalid choice; no changes applied", err=True)
            return False

        resolve_concurrency_loop(
            load_latest=load_latest,
            get_base_version=get_base_version,
            apply_update=apply_update,
            render_diff=render_diff,
            prompt_action=prompt_action,
            handle_action=handle_action,
        )
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
        except Exception:
            pass


def edit_item_text_field(key: str, field_name: str, editor_prefix: str, header: str = "") -> None:
    """Edit a plain text Zotero data field with basic concurrency handling."""

    zot = zot_from_config()

    try:
        item = zot.get_item(key)
    except Exception as exc:
        typer.echo(f"Failed to load item {key}: {exc}", err=True)
        raise typer.Exit(code=1)

    data = item.get("data", {}) or {}
    base_text = str(data.get(field_name) or "")
    base_version = int(item.get("version") or 0)

    edited = edit_text_in_editor(prefix=editor_prefix, header=header, template=base_text)
    if edited is None:
        return
    new_text = edited.rstrip("\n")
    if new_text == base_text.rstrip("\n"):
        typer.echo("No changes; nothing to update")
        return

    def load_latest() -> tuple[dict, int]:
        try:
            return load_latest_item_data_and_version(zot, key)
        except Exception as exc:
            typer.echo(f"Failed to reload item {key}: {exc}", err=True)
            raise typer.Exit(code=1)

    def get_base_version() -> int:
        return base_version

    def apply_update() -> None:
        run_write_with_retry(
            zot=zot,
            action_label="update",
            op=lambda client: client.update_item_data(key, {field_name: new_text}),
            prompt_key="update",
            failure_message="Update failed",
        )
        typer.echo("Updated")

    def render_diff(latest_data: dict) -> None:
        latest_text = str(latest_data.get(field_name) or "")
        print_item_data_diff_selected(
            {field_name: base_text},
            {field_name: new_text},
            {field_name: latest_text},
        )

    def prompt_action() -> str:
        return prompt_concurrent_action(allow_merge=False, allow_remote=True, default="y")

    def handle_action(action: str, latest_data: dict, latest_version: int) -> bool:
        nonlocal base_text, base_version, new_text

        latest_text = str(latest_data.get(field_name) or "")

        if action == "abort":
            typer.echo("Aborted")
            return True

        if action == "remote":
            edited_remote = edit_text_in_editor(
                prefix=editor_prefix,
                header=header,
                template=latest_text,
            )
            if edited_remote is None:
                typer.echo("Aborted")
                return True
            edited_remote_norm = edited_remote.rstrip("\n")
            if edited_remote_norm == latest_text.rstrip("\n"):
                typer.echo("No changes; nothing to update")
                return True
            base_text = latest_text
            base_version = latest_version
            new_text = edited_remote_norm
            return False

        if action == "yours":
            run_write_with_retry(
                zot=zot,
                action_label="update",
                op=lambda client: client.update_item_data_force(
                    key,
                    {field_name: new_text},
                    strict=False,
                    force=True,
                ),
                prompt_key="update",
                failure_message="Update failed",
            )
            typer.echo("Updated (yours)")
            return True

        typer.echo("Invalid choice; no changes applied", err=True)
        return False

    resolve_concurrency_loop(
        load_latest=load_latest,
        get_base_version=get_base_version,
        apply_update=apply_update,
        render_diff=render_diff,
        prompt_action=prompt_action,
        handle_action=handle_action,
    )


def abstract(key: str) -> None:
    """Edit the item's abstract text."""

    edit_item_text_field(key, "abstractNote", editor_prefix=f"ref-abstract-{key}-")


def extra(key: str) -> None:
    """Edit the item's Extra field."""

    edit_item_text_field(key, "extra", editor_prefix=f"ref-extra-{key}-")


def note(
    key: str,
    note_editor: Optional[str] = typer.Option(
        None,
        "--note-editor",
        help="Editor mode for notes: text | markdown",
        case_sensitive=False,
    ),
) -> None:
    """Edit or create a child note for the Zotero item."""

    if note_editor:
        value = str(note_editor).strip().lower()
        if value not in {NoteEditorMode.TEXT, NoteEditorMode.MARKDOWN}:
            raise typer.BadParameter("--note-editor must be one of: text, markdown")
        os.environ["REF_NOTE_EDITOR"] = value

    zot = zot_from_config()
    try:
        children = zot.children(key)
    except Exception as exc:
        typer.echo(f"Failed to list children for {key}: {exc}", err=True)
        raise typer.Exit(code=1)

    note_children = [ch for ch in children if (ch.get("data", {}).get("itemType") == "note")]

    def _html_to_text(text: str) -> str:
        converted = text.replace("<br>", "\n").replace("<br/>", "\n").replace("<li>", "- ")
        converted = converted.replace("</li>", "\n").replace("<p>", "\n").replace("</p>", "\n")
        converted = converted.replace("<div>", "\n").replace("</div>", "\n")
        converted = converted.replace("<&lt;", "<").replace("&gt;", ">")
        return typer.unstyle(converted)

    def _text_to_html(text: str) -> str:
        escaped = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<p>" + escaped.replace("\n\n", "</p><p>").replace("\n", "<br>") + "</p>"

    def _md_to_html(text: str) -> str:
        try:
            import markdown
        except ImportError as exc:
            typer.echo(
                "python-markdown is required for markdown note editing (pip install markdown)",
                err=True,
            )
            raise typer.Exit(code=1) from exc
        return markdown.markdown(text, extensions=["extra", "sane_lists"], output_format="html5")

    existing_note = None
    if not note_children:
        pass
    elif len(note_children) == 1:
        existing_note = note_children[0]
    else:
        lines: List[str] = []
        for child in note_children:
            note_data = child.get("data", {})
            note_text = note_data.get("note", "")
            preview = _html_to_text(note_text or "")[:120]
            lines.append(f"{child.get('key')} :: {preview}")
        header = (
            "Enter: select note  |  Esc: cancel  |  Ctrl-g: hidden help\n"
            "Ctrl-n/p: navigate notes"
        )
        try:
            selected, _ = fzf_multiselect_lines(
                lines,
                prompt="note",
                header=header,
                marker="★",
                preselect_all=False,
                print_query=False,
                with_help=True,
                context="notes",
            )
        except FileNotFoundError:
            typer.echo("fzf not found for note selection", err=True)
            raise typer.Exit(code=1)
        if not selected:
            return
        selected_key = selected[0].split(" :: ", 1)[0]
        existing_note = next((ch for ch in note_children if ch.get("key") == selected_key), None)

    mode = get_note_editor_mode()
    base_text = ""
    note_key: Optional[str] = None
    if existing_note:
        note_key = existing_note.get("key")
        note_data = existing_note.get("data", {})
        html_content = note_data.get("note", "")
        if isinstance(html_content, str):
            if mode == NoteEditorMode.MARKDOWN:
                base_text = html_content
            else:
                base_text = _html_to_text(html_content)

    edited = edit_text_in_editor(prefix=f"ref-note-{key}-", header="", template=base_text)
    if edited is None:
        return
    new_text = edited.rstrip("\n")
    if new_text == base_text.rstrip("\n"):
        typer.echo("No changes; nothing to update")
        return

    zot = zot_from_config()

    if existing_note and note_key:
        new_html = _md_to_html(new_text) if mode == NoteEditorMode.MARKDOWN else _text_to_html(new_text)
        run_write_with_retry(
            zot=zot,
            action_label="update note",
            op=lambda client: client.update_child_item_fields(
                note_key,
                {"itemType": "note", "note": new_html, "parentItem": key},
            ),
            prompt_key="note_update",
            failure_message="Update note failed",
        )
        typer.echo("Updated")
        return

    new_html = _md_to_html(new_text) if mode == NoteEditorMode.MARKDOWN else _text_to_html(new_text)

    retry_or_escalate(
        zot,
        "create note",
        lambda client: client.create_items(
            [
                {
                    "itemType": "note",
                    "note": new_html,
                    "parentItem": key,
                }
            ]
        ),
    )
    typer.echo("Created note")


def picker_edit(key: str) -> None:
    """Run ``ref edit`` with retry prompts suitable for picker bindings."""

    def _ask(default: str) -> str:
        prompt = "Action? [r=retry / q=quit]: "
        try:
            response = input(prompt)
        except EOFError:
            return default
        text = response.strip().lower()
        return text if text else default

    while True:
        try:
            edit(key)
            return
        except typer.Exit as exc:
            if exc.exit_code == 0:
                raise
            typer.echo("\n[ERROR] Edit failed (see details above).", err=True)
            choice = _ask("q" if exc.exit_code else "r")
            if choice.startswith("r"):
                continue
            raise typer.Exit(code=exc.exit_code)
        except Exception as exc:
            typer.echo(f"[ERROR] Unexpected failure: {exc}", err=True)
            choice = _ask("q")
            if choice.startswith("r"):
                continue
            raise typer.Exit(code=1)


CORE_COMMANDS = (
    CommandBinding(callback=edit, name="edit", interactive=True),
    CommandBinding(callback=picker_edit, name="picker-edit", interactive=True),
    CommandBinding(callback=abstract, name="abstract", interactive=True),
    CommandBinding(callback=extra, name="extra", interactive=True),
    CommandBinding(callback=note, name="note", interactive=True),
)
