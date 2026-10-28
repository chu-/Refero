"""Prepare reviewed item changes and apply them with optimistic concurrency."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
from typing import Any, Optional
from urllib.parse import urlparse
import uuid

from ..backends.records import (
    DataError,
    create_note,
    describe_library,
    patch_item,
    read_item,
    read_records,
)
from ..backends.zotero_client import ZoteroClient

WRITE_RESOURCES = ("item", "abstract", "extra", "note", "labels", "relations")


def validate_key(key: str) -> str:
    """Return an item key after checking that it cannot change a request path."""
    if not isinstance(key, str) or not re.fullmatch(r"[A-Z0-9]{8}", key):
        raise DataError(
            "INVALID_KEY", "Use an eight-character uppercase Zotero item key."
        )
    return key


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _version(record: dict[str, Any]) -> int:
    value = record.get("version", record["data"].get("version"))
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise DataError(
            "VERSION_MISSING", "A reviewed write requires a valid item version."
        )
    return value


def _check(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "key": record["key"],
        "version": _version(record),
        "data_hash": _digest(record["data"]),
    }


def _change(
    record: dict[str, Any], updates: dict[str, Any]
) -> Optional[dict[str, Any]]:
    after = {
        field: value
        for field, value in updates.items()
        if field not in record["data"] or record["data"][field] != value
    }
    if not after:
        return None
    return {
        "operation": "update",
        "key": record["key"],
        "version": _version(record),
        "before": {
            field: record["data"][field] for field in after if field in record["data"]
        },
        "after": after,
    }


def _relation_values(relations: dict[str, Any]) -> list[str]:
    if not isinstance(relations, dict):
        raise DataError("INVALID_RELATIONS", "Expected a relations mapping.")
    values = relations.get("dc:relation", [])
    if isinstance(values, str):
        return [values]
    if not isinstance(values, list) or any(
        not isinstance(value, str) for value in values
    ):
        raise DataError(
            "INVALID_RELATIONS", "dc:relation must be a URI or a list of URIs."
        )
    return values


def _relation_key(client: ZoteroClient, uri: str) -> Optional[str]:
    parsed = urlparse(uri)
    prefix = "groups" if client.library_type == "group" else "users"
    path = f"/{prefix}/{client.library_id}/items/"
    if (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == "zotero.org"
        and parsed.path.startswith(path)
    ):
        return validate_key(parsed.path[len(path) :])
    return None


def _relation_changes(
    client: ZoteroClient,
    parent: dict[str, Any],
    payload: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not isinstance(payload, list) or any(
        not isinstance(key, str) for key in payload
    ):
        raise DataError(
            "INVALID_INPUT", "Relations input must be a JSON array of item keys."
        )
    keys = list(dict.fromkeys(validate_key(key) for key in payload))
    if parent["key"] in keys:
        raise DataError("INVALID_RELATIONS", "An item cannot be related to itself.")
    prefix = "groups" if client.library_type == "group" else "users"
    uri_prefix = f"http://zotero.org/{prefix}/{client.library_id}/items/"
    relations = deepcopy(parent["data"].get("relations") or {})
    if not isinstance(relations, dict):
        raise DataError("INVALID_RELATIONS", "Expected a relations mapping.")
    old_uris = _relation_values(relations)
    old_keys = {key for uri in old_uris if (key := _relation_key(client, uri))}
    external = [uri for uri in old_uris if not _relation_key(client, uri)]
    relations["dc:relation"] = external + [uri_prefix + key for key in keys]
    checks = [_check(parent)]
    changes = []
    changed = _change(parent, {"relations": relations})
    if changed:
        changes.append(changed)
    parent_uri = uri_prefix + parent["key"]
    for key in sorted(old_keys | set(keys)):
        other = read_item(client, key)
        checks.append(_check(other))
        reciprocal = deepcopy(other["data"].get("relations") or {})
        uris = _relation_values(reciprocal)
        if key in keys:
            if not any(_relation_key(client, uri) == parent["key"] for uri in uris):
                uris = [*uris, parent_uri]
        else:
            uris = [uri for uri in uris if _relation_key(client, uri) != parent["key"]]
        reciprocal["dc:relation"] = uris
        changed = _change(other, {"relations": reciprocal})
        if changed:
            changes.append(changed)
    return checks, changes


def _metadata_updates(record: dict[str, Any], payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise DataError(
            "INVALID_INPUT", "Item input must be a JSON object of fields to change."
        )
    if isinstance(payload.get("data"), dict):
        if payload.get("key") != record["key"]:
            raise DataError(
                "INVALID_INPUT", "The input record belongs to a different item."
            )
        if payload.get("version") != _version(record):
            raise DataError(
                "VERSION_CONFLICT", "The input record is stale; read the item again."
            )
        payload = payload["data"]
    updates = dict(payload)
    for field, expected in (("key", record["key"]), ("version", _version(record))):
        if field in updates:
            if updates.pop(field) != expected:
                raise DataError("INVALID_INPUT", f"The input cannot change {field}.")
    return updates


def _label_updates(client: ZoteroClient, payload: Any) -> dict[str, Any]:
    if (
        not isinstance(payload, dict)
        or not payload
        or set(payload) - {"tags", "collections"}
    ):
        raise DataError(
            "INVALID_INPUT", "Labels input accepts tags and/or collections arrays."
        )
    updates = deepcopy(payload)
    if "tags" in updates:
        tags = updates["tags"]
        if not isinstance(tags, list) or any(
            not isinstance(tag, dict) or not isinstance(tag.get("tag"), str)
            for tag in tags
        ):
            raise DataError(
                "INVALID_INPUT",
                'Tags must use Zotero objects such as {"tag": "reading"}.',
            )
    if "collections" in updates:
        keys = updates["collections"]
        if not isinstance(keys, list) or any(not isinstance(key, str) for key in keys):
            raise DataError(
                "INVALID_INPUT", "Collections must be an array of collection keys."
            )
        for key in keys:
            validate_key(key)
        collections, evidence = read_records(client, "collections")
        if not evidence["complete"]:
            raise DataError(
                "INCOMPLETE_READ",
                "Collection membership could not be validated completely.",
            )
        if set(keys) - {record["key"] for record in collections}:
            raise DataError(
                "INVALID_INPUT",
                "A requested collection does not exist in this library.",
            )
    return updates


def prepare_plan(
    client: ZoteroClient,
    key: str,
    resource: str,
    payload: Any,
    *,
    note_key: Optional[str] = None,
) -> dict[str, Any]:
    """Prepare a complete diff and version checks without modifying the library.

    Args:
        client: Explicitly selected writable library, read only during preparation.
        key: Parent or item key.
        resource: Item, abstract, extra, note, labels, or relations.
        payload: Editable item JSON, replacement text/HTML, labels, or related keys.
        note_key: Existing child note to update; omission explicitly creates a note.

    Returns:
        A content-addressed plan requiring separate confirmation before submission.
    """
    validate_key(key)
    if resource not in WRITE_RESOURCES or (note_key and resource != "note"):
        raise DataError(
            "INVALID_RESOURCE", "Choose a supported write resource and note selection."
        )
    parent = read_item(client, key)
    checks = [_check(parent)]
    changes: list[dict[str, Any]] = []
    if resource == "relations":
        checks, changes = _relation_changes(client, parent, payload)
    elif resource == "note":
        if not isinstance(payload, str):
            raise DataError("INVALID_INPUT", "Note input must be a UTF-8 HTML file.")
        if note_key:
            record = read_item(client, validate_key(note_key))
            if (
                record["data"].get("itemType") != "note"
                or record["data"].get("parentItem") != key
            ):
                raise DataError(
                    "INVALID_INPUT", "The selected note is not a child of this item."
                )
            checks.append(_check(record))
            changed = _change(record, {"note": payload})
            if changed:
                changes.append(changed)
        else:
            if parent["data"].get("itemType") in {"note", "attachment", "annotation"}:
                raise DataError(
                    "INVALID_INPUT", "Create a note under a regular bibliographic item."
                )
            changes.append(
                {
                    "operation": "create_note",
                    "key": None,
                    "before": None,
                    "after": {
                        "itemType": "note",
                        "parentItem": key,
                        "note": payload,
                        "tags": [],
                        "relations": {},
                    },
                }
            )
    else:
        if resource in {"abstract", "extra"}:
            if not isinstance(payload, str):
                raise DataError(
                    "INVALID_INPUT", "Field input must be a UTF-8 text file."
                )
            field = "abstractNote" if resource == "abstract" else "extra"
            updates = {field: payload}
        elif resource == "labels":
            updates = _label_updates(client, payload)
        else:
            if (
                isinstance(payload, dict)
                and payload.get("schema_version") == 1
                and payload.get("resource") == "item"
            ):
                if payload.get("library") != describe_library(client):
                    raise DataError(
                        "LIBRARY_CHANGED",
                        "The input read belongs to a different library or backend.",
                    )
                payload = payload.get("data")
            updates = _metadata_updates(parent, payload)
            if set(updates) & {"tags", "collections"}:
                _label_updates(
                    client,
                    {
                        field: value
                        for field, value in updates.items()
                        if field in {"tags", "collections"}
                    },
                )
            if "relations" in updates and updates["relations"] != parent["data"].get(
                "relations"
            ):
                uris = _relation_values(updates["relations"])
                related = [
                    key_relation
                    for uri in uris
                    if (key_relation := _relation_key(client, uri))
                ]
                checks, relation_changes = _relation_changes(client, parent, related)
                changes.extend(
                    change for change in relation_changes if change["key"] != key
                )
            if updates.get("parentItem"):
                target = validate_key(updates["parentItem"])
                if target == key:
                    raise DataError(
                        "INVALID_INPUT", "An item cannot be its own parent."
                    )
                if target != parent["data"].get("parentItem"):
                    checks.append(_check(read_item(client, target)))
        changed = _change(parent, updates)
        if changed:
            changes.insert(0, changed)
    checks_by_key = {}
    for check in checks:
        previous = checks_by_key.get(check["key"])
        if previous and previous != check:
            raise DataError(
                "SNAPSHOT_CHANGED", "An item changed while preparing the plan."
            )
        checks_by_key[check["key"]] = check
    plan = {
        "schema_version": 1,
        "kind": "refero.write-plan",
        "status": "confirmation_required" if changes else "no_changes",
        "request_id": uuid.uuid4().hex,
        "library": describe_library(client),
        "resource": resource,
        "key": key,
        "checks": list(checks_by_key.values()),
        "changes": changes,
    }
    return {**plan, "plan_id": _digest(plan)}


def validate_plan(plan: Any) -> dict[str, Any]:
    """Validate a saved plan and its digest before selecting a backend."""
    if (
        not isinstance(plan, dict)
        or plan.get("schema_version") != 1
        or plan.get("kind") != "refero.write-plan"
        or plan.get("resource") not in WRITE_RESOURCES
        or not isinstance(plan.get("library"), dict)
        or not isinstance(plan.get("checks"), list)
        or not isinstance(plan.get("changes"), list)
    ):
        raise DataError(
            "INVALID_PLAN", "Expected a Refero write plan with schema_version 1."
        )
    contents = {field: value for field, value in plan.items() if field != "plan_id"}
    if plan.get("plan_id") != _digest(contents):
        raise DataError(
            "PLAN_CHANGED",
            "The plan changed after preparation; prepare and review it again.",
        )
    validate_key(plan.get("key", ""))
    checked = set()
    for check in plan["checks"]:
        if not isinstance(check, dict):
            raise DataError("INVALID_PLAN", "Invalid version check.")
        key = validate_key(check.get("key", ""))
        if (
            key in checked
            or not isinstance(check.get("version"), int)
            or not isinstance(check.get("data_hash"), str)
        ):
            raise DataError("INVALID_PLAN", "Invalid or duplicate version check.")
        checked.add(key)
    if plan["key"] not in checked:
        raise DataError("INVALID_PLAN", "The parent item must have a version check.")
    changed = set()
    for change in plan["changes"]:
        if not isinstance(change, dict) or not isinstance(change.get("after"), dict):
            raise DataError("INVALID_PLAN", "Invalid change payload.")
        if change.get("operation") == "create_note":
            if (
                plan["resource"] != "note"
                or change["after"].get("itemType") != "note"
                or change["after"].get("parentItem") != plan["key"]
            ):
                raise DataError("INVALID_PLAN", "Invalid child note creation.")
        elif change.get("operation") == "update":
            key = validate_key(change.get("key", ""))
            if (
                key not in checked
                or key in changed
                or set(change["after"]) & {"key", "version"}
            ):
                raise DataError("INVALID_PLAN", "Invalid update target or fields.")
            check = next(value for value in plan["checks"] if value["key"] == key)
            if change.get("version") != check["version"] or not isinstance(
                change.get("before"), dict
            ):
                raise DataError(
                    "INVALID_PLAN", "The reviewed version or before-values are missing."
                )
            changed.add(key)
        else:
            raise DataError("INVALID_PLAN", "Unsupported write operation.")
    return plan


def apply_plan(client: ZoteroClient, plan: dict[str, Any]) -> dict[str, Any]:
    """Apply an already confirmed plan, stopping on conflicts or partial failure.

    Confirmation is enforced by the command before this function is called.
    This function never forces updates, retries writes, or changes libraries.
    """
    validate_plan(plan)
    if describe_library(client) != plan["library"]:
        raise DataError(
            "LIBRARY_CHANGED", "The selected library differs from the reviewed plan."
        )
    for check in plan["checks"]:
        current = read_item(client, check["key"])
        if (
            _version(current) != check["version"]
            or _digest(current["data"]) != check["data_hash"]
        ):
            raise DataError(
                "VERSION_CONFLICT", "An item changed after review; prepare a new plan."
            )
        for change in plan["changes"]:
            if change["operation"] != "update" or change["key"] != check["key"]:
                continue
            before = {
                field: current["data"][field]
                for field in change["after"]
                if field in current["data"]
            }
            if before != change["before"]:
                raise DataError(
                    "INVALID_PLAN", "Reviewed before-values do not match the item."
                )
    receipt = {
        "schema_version": 1,
        "status": "applied",
        "plan_id": plan["plan_id"],
        "library": plan["library"],
        "resource": plan["resource"],
        "key": plan["key"],
        "applied": [],
        "verified": [],
        "complete": True,
    }
    for change in plan["changes"]:
        key = change["key"]
        try:
            if change["operation"] == "create_note":
                key = create_note(client, change["after"], plan["plan_id"][:32])
            else:
                patch_item(client, key, change["after"], change["version"])
            receipt["applied"].append(key)
            current = read_item(client, key)
            if any(
                field not in current["data"] or current["data"][field] != value
                for field, value in change["after"].items()
            ):
                raise DataError(
                    "VERIFICATION_FAILED",
                    "Read-back fields differ from the reviewed values.",
                )
            receipt["verified"].append(key)
        except DataError as exc:
            receipt.update(
                {
                    "status": "partial" if receipt["applied"] else "failed",
                    "complete": False,
                    "unverified_key": key,
                    "error": {"code": exc.code, "message": str(exc)},
                    "remaining": [
                        operation["key"]
                        for operation in plan["changes"]
                        if operation["key"] not in receipt["verified"]
                    ],
                }
            )
            break
    return receipt
