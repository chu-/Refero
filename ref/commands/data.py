"""Explicit non-interactive reads and reviewed writes for editor-backed data."""

from __future__ import annotations

from contextlib import contextmanager, redirect_stdout
import json
import os
from pathlib import Path
import sys
from typing import Any, Iterator, Optional

import typer
import yaml

from ..backends.records import DataError, describe_library, read_item, read_records
from ..backends.zotero_client import ZoteroClient
from ..config import load as load_config
from ..utils.interaction import is_non_interactive
from ..utils.items import resolve_attachment_path
from ..utils.write_plans import (
    WRITE_RESOURCES,
    apply_plan,
    prepare_plan,
    validate_key,
    validate_plan,
)
from ._registry import CommandBinding

READ_RESOURCES = (
    "item",
    "items",
    "abstract",
    "extra",
    "notes",
    "relations",
    "labels",
    "children",
    "attachments",
    "collections",
    "tags",
)


def _emit(payload: dict[str, Any], out: Optional[Path] = None) -> None:
    text = json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False)
    if out:
        descriptor = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
    typer.echo(text)


@contextmanager
def _report_errors() -> Iterator[None]:
    try:
        yield
    except DataError as exc:
        _emit(
            {
                "schema_version": 1,
                "status": "error",
                "complete": False,
                "error": {"code": exc.code, "message": str(exc)},
            }
        )
        code = (
            2
            if exc.code.startswith("INVALID_") or exc.code == "CONFIRMATION_REQUIRED"
            else 1
        )
        raise typer.Exit(code=code) from exc
    except (OSError, ValueError, TypeError, yaml.YAMLError) as exc:
        _emit(
            {
                "schema_version": 1,
                "status": "error",
                "complete": False,
                "error": {
                    "code": "INPUT_ERROR",
                    "message": "Check file access, UTF-8/JSON input, and configuration syntax.",
                },
            }
        )
        raise typer.Exit(code=2) from exc


def _client(*, web: bool = False, writing: bool = False) -> ZoteroClient:
    cfg = load_config(read_only=True)
    zotero = cfg.zotero
    if (
        cfg.zotero_unknown_keys
        or zotero.mode not in {"local", "web"}
        or zotero.library_type not in {"user", "group"}
    ):
        raise DataError(
            "INVALID_CONFIG", "Correct the Zotero configuration before accessing data."
        )
    local = zotero.is_local and not web
    if writing and local:
        raise DataError(
            "WRITE_BACKEND_REQUIRED",
            "Reviewed writes use the Web API. For a local configuration, "
            "select --web explicitly and configure the corresponding library credentials.",
        )
    if not local and not (zotero.library_id and zotero.api_key):
        raise DataError(
            "CREDENTIALS_REQUIRED", "Web access requires library_id and api_key."
        )
    return ZoteroClient(
        library_id=0 if local else zotero.library_id,
        library_type=zotero.library_type,
        api_key=zotero.api_key,
        endpoint=zotero.local_api if local else zotero.endpoint,
        local=local,
    )


def read(
    key: Optional[str] = typer.Argument(
        None, help="Item key; omit for library-wide resources"
    ),
    resource: str = typer.Option(
        "item",
        "--resource",
        help="item|items|abstract|extra|notes|relations|labels|children|attachments|collections|tags",
    ),
    web: bool = typer.Option(
        False, "--web", help="Explicitly read the configured Web library"
    ),
) -> None:
    """Return lossless JSON data with scope and completeness evidence."""
    with _report_errors():
        if resource not in READ_RESOURCES:
            raise DataError("INVALID_RESOURCE", "Choose a supported read resource.")
        library_resource = resource in {"items", "collections", "tags"}
        if library_resource and key:
            raise DataError(
                "INVALID_INPUT", "Library-wide reads do not accept an item key."
            )
        if not library_resource:
            if not key:
                raise DataError(
                    "INVALID_INPUT", "An item key is required for this resource."
                )
            validate_key(key)
        client = _client(web=web)
        response: dict[str, Any] = {
            "schema_version": 1,
            "resource": resource,
            "key": key,
            "library": describe_library(client),
            "complete": True,
        }
        if library_resource or resource in {"children", "notes", "attachments"}:
            path = resource if library_resource else "children"
            records, evidence = read_records(client, path, key)
            selected = records
            if resource in {"notes", "attachments"}:
                kind = "note" if resource == "notes" else "attachment"
                selected = [
                    record
                    for record in records
                    if record.get("data", {}).get("itemType") == kind
                ]
            response.update(
                {
                    "data": selected,
                    "count": len(selected),
                    "complete": evidence["complete"],
                    "completeness": {
                        **evidence,
                        "source_returned": len(records),
                        "source_total": evidence["total"],
                        "returned": len(selected),
                        "scope": resource,
                        "total": (
                            len(selected)
                            if evidence["complete"]
                            else evidence["total"]
                            if selected is records
                            else None
                        ),
                    },
                }
            )
            if resource == "attachments":
                storage = load_config(read_only=True).zotero.storage_dir
                locations = []
                for record in selected:
                    path = resolve_attachment_path(
                        storage, record["key"], record["data"]
                    )
                    locations.append(
                        {
                            "key": record["key"],
                            "local_path": str(path) if path else None,
                            "exists": path.is_file() if path else False,
                        }
                    )
                response["locations"] = locations
                response["content_included"] = False
        else:
            record = read_item(client, key)
            response["version"] = record.get("version", record["data"].get("version"))
            response["completeness"] = {
                "complete": True,
                "scope": resource,
                "includes_children": False,
            }
            if resource == "item":
                response["data"] = record
            elif resource == "labels":
                response["data"] = {
                    field: record["data"].get(field)
                    for field in ("tags", "collections")
                }
                response["fields_present"] = [
                    field
                    for field in ("tags", "collections")
                    if field in record["data"]
                ]
            else:
                field = "abstractNote" if resource == "abstract" else resource
                response["data"] = {
                    "field": field,
                    "present": field in record["data"],
                    "value": record["data"].get(field),
                }
        _emit(response)


def _read_json(path: Path) -> Any:
    def reject_constant(value: str) -> None:
        raise ValueError("JSON must not contain non-finite numbers")

    return json.loads(path.read_text(encoding="utf-8"), parse_constant=reject_constant)


def write(
    key: Optional[str] = typer.Argument(
        None, help="Item/parent key when preparing a plan"
    ),
    resource: str = typer.Option(
        "item", "--resource", help="item|abstract|extra|note|labels|relations"
    ),
    input_file: Optional[Path] = typer.Option(
        None, "--input", help="UTF-8 JSON, text, or HTML file"
    ),
    note_key: Optional[str] = typer.Option(
        None, "--note-key", help="Existing child note key; omit to create a note"
    ),
    out: Optional[Path] = typer.Option(
        None, "--out", help="Save a new review plan file; existing files are refused"
    ),
    apply_file: Optional[Path] = typer.Option(
        None, "--apply", help="Submit a previously reviewed plan"
    ),
    confirm: Optional[str] = typer.Option(
        None, "--confirm", help="Reviewed plan_id, supplied only after user approval"
    ),
    web: bool = typer.Option(
        False,
        "--web",
        help="Explicitly select configured Web credentials in local mode",
    ),
) -> None:
    """Prepare a JSON diff, or submit the exact plan after user confirmation."""
    with _report_errors():
        if apply_file:
            if key or input_file or note_key or out or resource != "item":
                raise DataError(
                    "INVALID_INPUT",
                    "--apply cannot be combined with preparation arguments.",
                )
            plan = validate_plan(_read_json(apply_file))
            if not plan["changes"]:
                _emit({"schema_version": 1, "status": "no_changes", "complete": True})
                return
            if confirm:
                if confirm != plan["plan_id"]:
                    raise DataError(
                        "CONFIRMATION_REQUIRED",
                        "Confirmation must match the reviewed plan_id.",
                    )
            elif is_non_interactive():
                raise DataError(
                    "CONFIRMATION_REQUIRED",
                    "Show this plan to the user. After explicit approval, pass its plan_id with --confirm.",
                )
            else:
                typer.echo(json.dumps(plan, ensure_ascii=False, indent=2), err=True)
                # Typer versions differ in which stream receives input echoes.
                # Keep every part of the confirmation off the JSON stream.
                with redirect_stdout(sys.stderr):
                    approved = typer.confirm(
                        "Apply this exact plan to the displayed library?",
                        default=False,
                        err=True,
                    )
                if not approved:
                    _emit(
                        {"schema_version": 1, "status": "cancelled", "complete": False}
                    )
                    return
            client = _client(web=web, writing=True)
            receipt = apply_plan(client, plan)
            _emit(receipt)
            if not receipt["complete"]:
                raise typer.Exit(code=1)
            return
        if confirm or not key or not input_file or resource not in WRITE_RESOURCES:
            raise DataError(
                "INVALID_INPUT",
                "Prepare with KEY --resource RESOURCE --input FILE; confirmation is only for --apply.",
            )
        validate_key(key)
        if note_key:
            validate_key(note_key)
        payload = (
            input_file.read_bytes().decode("utf-8")
            if resource in {"abstract", "extra", "note"}
            else _read_json(input_file)
        )
        client = _client(web=web, writing=True)
        _emit(prepare_plan(client, key, resource, payload, note_key=note_key), out)


COMMANDS = (
    CommandBinding(callback=read, name="read"),
    CommandBinding(callback=write, name="write"),
)
