"""Exercise lossless reads and confirmation-bound writes against an HTTP library."""

from copy import deepcopy
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import os
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from typer.testing import CliRunner

from ref.cli import app


def make_item(key, kind="journalArticle", *, parent=None, **fields):
    """Return a small complete server record suitable for concurrency tests."""
    data = {
        "key": key,
        "version": 7,
        "itemType": kind,
        "title": "完整记录",
        "creators": [{"creatorType": "author", "name": "Research Group"}],
        "abstractNote": "摘要\n",
        "extra": "Citation Key: sample2025",
        "date": "2025-03-02",
        "tags": [{"tag": "reading", "type": 1}],
        "collections": [],
        "relations": {},
    }
    if parent:
        data["parentItem"] = parent
    data.update(fields)
    return {
        "key": key,
        "version": 7,
        "data": data,
        "library": {"id": 123, "type": "user"},
        "meta": {"custom": "preserved"},
    }


class DataCommandTests(unittest.TestCase):
    """Verify the command interface using actual requests and an isolated library."""

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.records = {
            "ABCD1234": make_item("ABCD1234"),
            "EFGH5678": make_item("EFGH5678", title="Other item"),
            "NOTE1234": make_item(
                "NOTE1234", "note", parent="ABCD1234", note="<p>原笔记</p>"
            ),
        }
        self.collections = [
            {
                "key": "COLL1234",
                "version": 7,
                "data": {"name": "Reading List", "parentCollection": False},
            }
        ]
        self.tags = [{"tag": "reading", "type": 1, "meta": {"numItems": 2}}]
        self.library_version = 7
        self.headers_enabled = True
        self.snapshot_changes = False
        self.duplicate_pages = False
        self.fail_patch = None
        self.conflict_patch = False
        self.lie_on_patch = False
        self.requests_seen = []
        self.mutations = []
        self.tokens = set()
        state = self

        class Handler(BaseHTTPRequestHandler):
            def respond(self, payload, code=200, *, total=None, version=None):
                encoded = json.dumps(payload, ensure_ascii=False).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                if state.headers_enabled:
                    self.send_header(
                        "Last-Modified-Version", str(version or state.library_version)
                    )
                    if total is not None:
                        self.send_header("Total-Results", str(total))
                self.end_headers()
                self.wfile.write(encoded)

            def do_GET(self):
                parsed = urlparse(self.path)
                state.requests_seen.append(("GET", parsed.path))
                query = parse_qs(parsed.query)
                start = int(query.get("start", ["0"])[0])
                limit = int(query.get("limit", ["100"])[0])
                path = parsed.path.rstrip("/")
                if path.endswith("/collections"):
                    records = state.collections
                elif path.endswith("/tags"):
                    records = state.tags
                elif path.endswith("/items"):
                    records = list(state.records.values())
                elif path.endswith("/children"):
                    key = path.split("/")[-2]
                    records = [
                        item
                        for item in state.records.values()
                        if item["data"].get("parentItem") == key
                    ]
                else:
                    key = path.split("/")[-1]
                    if key not in state.records:
                        self.respond({"error": "missing"}, 404)
                    else:
                        self.respond(
                            state.records[key], version=state.records[key]["version"]
                        )
                    return
                batch = records[start : start + limit]
                if start and state.duplicate_pages:
                    batch = records[: len(batch)]
                version = state.library_version + int(
                    bool(start and state.snapshot_changes)
                )
                self.respond(batch, total=len(records), version=version)

            def do_PATCH(self):
                key = urlparse(self.path).path.split("/")[-1]
                state.requests_seen.append(("PATCH", key))
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )
                if key == state.fail_patch:
                    self.respond({"error": "private-server-error"}, 500)
                    return
                record = state.records[key]
                if state.conflict_patch or self.headers.get(
                    "If-Unmodified-Since-Version"
                ) != str(record["version"]):
                    self.respond({"error": "changed"}, 412)
                    return
                if not state.lie_on_patch:
                    record["data"].update(payload)
                    record["version"] += 1
                    record["data"]["version"] = record["version"]
                    state.library_version += 1
                    state.mutations.append((key, payload))
                self.send_response(204)
                self.end_headers()

            def do_POST(self):
                state.requests_seen.append(("POST", self.path))
                token = self.headers.get("Zotero-Write-Token")
                payload = json.loads(
                    self.rfile.read(int(self.headers["Content-Length"]))
                )[0]
                if not token or token in state.tokens:
                    self.respond({"error": "replayed"}, 412)
                    return
                state.tokens.add(token)
                state.records["NEWNOTE1"] = make_item("NEWNOTE1", **payload)
                state.mutations.append(("NEWNOTE1", payload))
                state.library_version += 1
                self.respond(
                    {"successful": {"0": state.records["NEWNOTE1"]}, "failed": {}}
                )

            def log_message(self, format, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(
            target=self.server.serve_forever,
            kwargs={"poll_interval": 0.01},
            daemon=True,
        )
        thread.start()

        def shutdown():
            self.server.shutdown()
            self.server.server_close()
            thread.join(timeout=5)

        self.addCleanup(shutdown)
        config = self.root / "config.yaml"
        config.write_text(
            "zotero:\n  mode: web\n  library_id: 123\n"
            "  api_key: private-test-key\n"
            f"  endpoint: http://127.0.0.1:{self.server.server_port}\n"
            f"  storage_dir: {self.root}\n",
            encoding="utf-8",
        )
        self.config_path = config
        environment = patch.dict(
            os.environ,
            {
                "REF_CONFIG": str(config),
                "REF_NON_INTERACTIVE": "0",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        self.runner = CliRunner()

    def invoke(self, *arguments, interactive=False, user_input=None):
        prefix = [] if interactive else ["--non-interactive"]
        return self.runner.invoke(app, [*prefix, *arguments], input=user_input)

    def prepare(self, resource, payload, *, note_key=None):
        source = self.root / "input.txt"
        text = payload if isinstance(payload, str) else json.dumps(payload)
        source.write_bytes(text.encode("utf-8"))
        arguments = [
            "write",
            "ABCD1234",
            "--resource",
            resource,
            "--input",
            str(source),
        ]
        if note_key:
            arguments += ["--note-key", note_key]
        result = self.invoke(*arguments)
        self.assertEqual(result.exit_code, 0, result.output)
        plan = json.loads(result.stdout)
        path = self.root / "plan.json"
        path.write_text(result.stdout, encoding="utf-8")
        return plan, path

    def apply(self, plan, path):
        return self.invoke("write", "--apply", str(path), "--confirm", plan["plan_id"])

    def test_full_record_preserves_fields_types_and_versions(self):
        result = self.invoke("read", "ABCD1234")
        self.assertEqual(result.exit_code, 0, result.output)
        response = json.loads(result.stdout)
        self.assertTrue(response["complete"])
        self.assertFalse(response["completeness"]["includes_children"])
        self.assertEqual(response["data"], self.records["ABCD1234"])
        self.assertEqual(
            response["data"]["data"]["creators"][0]["name"], "Research Group"
        )
        self.assertNotIn("private-test-key", result.stdout)
        self.assertFalse(self.mutations)

    def test_field_reads_distinguish_missing_empty_and_null(self):
        for value in ("", None, "完整\n文本\n"):
            with self.subTest(value=value):
                self.records["ABCD1234"]["data"]["extra"] = value
                result = self.invoke("read", "ABCD1234", "--resource", "extra")
                field = json.loads(result.stdout)["data"]
                self.assertTrue(field["present"])
                self.assertEqual(field["value"], value)
        del self.records["ABCD1234"]["data"]["extra"]
        result = self.invoke("read", "ABCD1234", "--resource", "extra")
        self.assertFalse(json.loads(result.stdout)["data"]["present"])

    def test_explicit_null_is_distinct_from_an_absent_field_in_the_diff(self):
        del self.records["ABCD1234"]["data"]["extra"]
        plan, path = self.prepare("item", {"extra": None})
        self.assertEqual(plan["changes"][0]["before"], {})
        self.assertEqual(plan["changes"][0]["after"], {"extra": None})
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("extra", self.records["ABCD1234"]["data"])
        self.assertIsNone(self.records["ABCD1234"]["data"]["extra"])

    def test_notes_are_complete_across_multiple_pages(self):
        for number in range(105):
            key = f"{number:08d}"
            self.records[key] = make_item(
                key, "note", parent="ABCD1234", note=f"note {number}"
            )
        result = self.invoke("read", "ABCD1234", "--resource", "notes")
        self.assertEqual(result.exit_code, 0, result.output)
        response = json.loads(result.stdout)
        self.assertTrue(response["complete"])
        self.assertEqual(response["count"], 106)
        self.assertEqual(len({item["key"] for item in response["data"]}), 106)
        self.assertEqual(response["data"][0]["data"]["note"], "<p>原笔记</p>")

    def test_unverified_completeness_is_explicit(self):
        self.headers_enabled = False
        result = self.invoke("read", "--resource", "tags")
        self.assertEqual(result.exit_code, 0, result.output)
        response = json.loads(result.stdout)
        self.assertFalse(response["complete"])
        self.assertEqual(response["data"], self.tags)
        self.assertIsNone(response["completeness"]["total"])

    def test_directory_reads_preserve_raw_records(self):
        for resource, expected in (
            ("collections", self.collections),
            ("tags", self.tags),
        ):
            with self.subTest(resource=resource):
                result = self.invoke("read", "--resource", resource)
                self.assertEqual(result.exit_code, 0, result.output)
                response = json.loads(result.stdout)
                self.assertTrue(response["complete"])
                self.assertEqual(response["data"], expected)

    def test_tag_directory_preserves_same_name_with_different_types(self):
        self.tags = [
            {"tag": "reading", "meta": {"type": 0, "numItems": 2}},
            {"tag": "reading", "meta": {"type": 1, "numItems": 3}},
        ]
        result = self.invoke("read", "--resource", "tags")
        self.assertEqual(result.exit_code, 0, result.output)
        response = json.loads(result.stdout)
        self.assertTrue(response["complete"])
        self.assertEqual(response["count"], 2)
        self.assertEqual(response["data"], self.tags)

    def test_changed_snapshot_and_duplicate_pages_are_rejected(self):
        for number in range(105):
            key = f"{number:08d}"
            self.records[key] = make_item(key)
        self.snapshot_changes = True
        result = self.invoke("read", "--resource", "items")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "SNAPSHOT_CHANGED")
        self.snapshot_changes = False
        self.duplicate_pages = True
        result = self.invoke("read", "--resource", "items")
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "INCONSISTENT_PAGES"
        )

    def test_attachment_read_returns_location_without_opening_a_viewer(self):
        document = self.root / "paper.pdf"
        document.write_bytes(b"fixture")
        self.records["ATTACH01"] = make_item(
            "ATTACH01",
            "attachment",
            parent="ABCD1234",
            path=str(document),
        )
        result = self.invoke("read", "ABCD1234", "--resource", "attachments")
        self.assertEqual(result.exit_code, 0, result.output)
        response = json.loads(result.stdout)
        self.assertEqual(response["locations"][0]["local_path"], str(document))
        self.assertTrue(response["locations"][0]["exists"])
        self.assertFalse(response["content_included"])

    def test_storage_attachment_location_includes_the_attachment_key(self):
        folder = self.root / "ATTACH01"
        folder.mkdir()
        document = folder / "paper.pdf"
        document.write_bytes(b"fixture")
        self.records["ATTACH01"] = make_item(
            "ATTACH01",
            "attachment",
            parent="ABCD1234",
            path="storage:paper.pdf",
        )
        result = self.invoke("read", "ABCD1234", "--resource", "attachments")
        response = json.loads(result.stdout)
        self.assertEqual(response["locations"][0]["local_path"], str(document))
        self.assertTrue(response["locations"][0]["exists"])

    def test_prepare_and_missing_confirmation_never_write(self):
        before = deepcopy(self.records)
        plan, path = self.prepare("abstract", "新摘要\n")
        self.assertEqual(plan["status"], "confirmation_required")
        self.assertEqual(plan["changes"][0]["before"], {"abstractNote": "摘要\n"})
        self.assertEqual(plan["changes"][0]["after"], {"abstractNote": "新摘要\n"})
        self.requests_seen.clear()
        with patch("ref.commands.data._client") as factory:
            result = self.invoke("write", "--apply", str(path))
        factory.assert_not_called()
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "CONFIRMATION_REQUIRED"
        )
        self.assertFalse(self.requests_seen)
        self.assertEqual(self.records, before)
        self.assertFalse(self.mutations)

    def test_confirmed_field_write_is_read_back_and_not_replayable(self):
        plan, path = self.prepare("extra", "改后的 Extra\n")
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        receipt = json.loads(result.stdout)
        self.assertTrue(receipt["complete"])
        self.assertEqual(receipt["verified"], ["ABCD1234"])
        self.assertEqual(self.records["ABCD1234"]["data"]["extra"], "改后的 Extra\n")
        repeated = self.apply(plan, path)
        self.assertEqual(repeated.exit_code, 1, repeated.output)
        self.assertEqual(len(self.mutations), 1)

    def test_edited_plan_and_wrong_confirmation_are_rejected(self):
        plan, path = self.prepare("item", {"title": "Reviewed title"})
        result = self.invoke("write", "--apply", str(path), "--confirm", "wrong")
        self.assertEqual(result.exit_code, 2, result.output)
        plan["changes"][0]["after"]["title"] = "Unreviewed title"
        path.write_text(json.dumps(plan), encoding="utf-8")
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "PLAN_CHANGED")
        self.assertFalse(self.mutations)

    def test_changed_item_or_library_blocks_submission(self):
        plan, path = self.prepare("item", {"title": "New title"})
        self.records["ABCD1234"]["data"]["title"] = "Remote edit"
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "VERSION_CONFLICT")
        self.assertFalse(self.mutations)
        self.config_path.write_text(
            self.config_path.read_text().replace("library_id: 123", "library_id: 456"),
            encoding="utf-8",
        )
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "LIBRARY_CHANGED")
        self.assertFalse(self.mutations)

    def test_conflict_between_preflight_and_patch_is_not_forced_or_retried(self):
        plan, path = self.prepare("abstract", "Updated")
        self.conflict_patch = True
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(json.loads(result.stdout)["error"]["code"], "VERSION_CONFLICT")
        self.assertEqual(sum(method == "PATCH" for method, _ in self.requests_seen), 1)
        self.assertFalse(self.mutations)

    def test_existing_and_new_note_preserve_html(self):
        plan, path = self.prepare(
            "note", "<p>新内容 &amp; 数学</p>\n", note_key="NOTE1234"
        )
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.records["NOTE1234"]["data"]["note"], "<p>新内容 &amp; 数学</p>\n"
        )
        plan, path = self.prepare("note", "<p>新建笔记</p>")
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["verified"], ["NEWNOTE1"])
        repeated = self.apply(plan, path)
        self.assertEqual(repeated.exit_code, 1, repeated.output)
        self.assertEqual(len(self.tokens), 1)

    def test_wrong_parent_note_is_rejected_before_any_write(self):
        self.records["NOTE1234"]["data"]["parentItem"] = "EFGH5678"
        source = self.root / "note.html"
        source.write_text("<p>New</p>", encoding="utf-8")
        result = self.invoke(
            "write",
            "ABCD1234",
            "--resource",
            "note",
            "--note-key",
            "NOTE1234",
            "--input",
            str(source),
        )
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertFalse(self.mutations)

    def test_reciprocal_relations_are_included_in_review_and_applied(self):
        self.records["ABCD1234"]["data"]["relations"] = {
            "owl:sameAs": "https://example.org/identity"
        }
        plan, path = self.prepare("relations", ["EFGH5678"])
        self.assertEqual(
            {change["key"] for change in plan["changes"]}, {"ABCD1234", "EFGH5678"}
        )
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        parent_relations = self.records["ABCD1234"]["data"]["relations"]
        self.assertEqual(parent_relations["owl:sameAs"], "https://example.org/identity")
        self.assertEqual(
            parent_relations["dc:relation"],
            ["http://zotero.org/users/123/items/EFGH5678"],
        )
        self.assertEqual(
            self.records["EFGH5678"]["data"]["relations"]["dc:relation"],
            ["http://zotero.org/users/123/items/ABCD1234"],
        )
        plan, path = self.prepare("relations", [])
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.records["EFGH5678"]["data"]["relations"]["dc:relation"], []
        )

    def test_partial_relation_failure_is_reported_without_retry(self):
        plan, path = self.prepare("relations", ["EFGH5678"])
        self.fail_patch = "EFGH5678"
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["status"], "partial")
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["verified"], ["ABCD1234"])
        self.assertEqual(receipt["unverified_key"], "EFGH5678")
        self.assertNotIn("private-server-error", result.stdout)
        self.assertEqual(sum(method == "PATCH" for method, _ in self.requests_seen), 2)

    def test_label_replacements_preserve_tag_types(self):
        desired = {
            "tags": [{"tag": "保留类型", "type": 1}],
            "collections": ["COLL1234"],
        }
        plan, path = self.prepare("labels", desired)
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        result = self.invoke("read", "ABCD1234", "--resource", "labels")
        self.assertEqual(json.loads(result.stdout)["data"], desired)

    def test_round_trip_full_record_does_not_normalize_or_drop_fields(self):
        modified = deepcopy(self.records["ABCD1234"])
        modified["data"]["title"] = "Changed title"
        plan, path = self.prepare("item", modified)
        self.assertEqual(plan["changes"][0]["after"], {"title": "Changed title"})
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.records["ABCD1234"]["data"]["creators"], modified["data"]["creators"]
        )
        self.assertEqual(
            self.records["ABCD1234"]["data"]["extra"], modified["data"]["extra"]
        )

    def test_read_envelope_can_be_edited_and_written_back(self):
        result = self.invoke("read", "ABCD1234")
        payload = json.loads(result.stdout)
        payload["data"]["data"]["title"] = "Round-tripped title"
        plan, path = self.prepare("item", payload)
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.records["ABCD1234"]["data"]["title"], "Round-tripped title"
        )

    def test_item_relations_update_preserves_predicates_and_reciprocal_links(self):
        desired = {
            "title": "With relations",
            "relations": {
                "owl:sameAs": "https://example.org/new-identity",
                "dc:relation": "https://zotero.org/users/123/items/EFGH5678",
            },
        }
        plan, path = self.prepare("item", desired)
        self.assertEqual(plan["key"], "ABCD1234")
        self.assertEqual(
            {change["key"] for change in plan["changes"]}, {"ABCD1234", "EFGH5678"}
        )
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            self.records["ABCD1234"]["data"]["relations"], desired["relations"]
        )
        self.assertEqual(
            self.records["EFGH5678"]["data"]["relations"]["dc:relation"],
            ["http://zotero.org/users/123/items/ABCD1234"],
        )

    def test_text_input_preserves_crlf_and_final_newlines(self):
        value = "第一行\r\n第二行\r\n"
        plan, path = self.prepare("extra", value)
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(self.records["ABCD1234"]["data"]["extra"], value)

    def test_local_write_requires_explicit_web_selection(self):
        config = self.config_path.read_text(encoding="utf-8").replace(
            "mode: web", "mode: local"
        )
        self.config_path.write_text(config, encoding="utf-8")
        source = self.root / "abstract.txt"
        source.write_text("Replacement", encoding="utf-8")
        arguments = [
            "write",
            "ABCD1234",
            "--resource",
            "abstract",
            "--input",
            str(source),
        ]
        result = self.invoke(*arguments)
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertEqual(
            json.loads(result.stdout)["error"]["code"], "WRITE_BACKEND_REQUIRED"
        )
        self.assertFalse(self.requests_seen)
        result = self.invoke(*arguments, "--web")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["library"]["mode"], "web")
        self.assertFalse(self.mutations)

    def test_human_apply_shows_plan_and_respects_no(self):
        plan, path = self.prepare("extra", "Reviewed replacement")
        result = self.invoke(
            "write", "--apply", str(path), interactive=True, user_input="n\n"
        )
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Reviewed replacement", result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "cancelled")
        self.assertFalse(self.mutations)

    def test_verification_failure_is_distinct_from_success(self):
        plan, path = self.prepare("extra", "Reviewed replacement")
        self.lie_on_patch = True
        result = self.apply(plan, path)
        self.assertEqual(result.exit_code, 1, result.output)
        receipt = json.loads(result.stdout)
        self.assertFalse(receipt["complete"])
        self.assertEqual(receipt["error"]["code"], "VERIFICATION_FAILED")
        self.assertEqual(receipt["applied"], ["ABCD1234"])
        self.assertEqual(receipt["verified"], [])

    def test_legacy_agent_mutations_need_user_confirmation_before_backend_access(self):
        for arguments in (
            ("set", "ABCD1234", "--tag", "ml"),
            ("add", "--doi", "10.1234/example"),
            ("delete", "ABCD1234"),
        ):
            with self.subTest(arguments=arguments):
                with patch("ref.commands.management.zot_from_config") as factory:
                    result = self.invoke(*arguments)
                self.assertEqual(result.exit_code, 2, result.output)
                factory.assert_not_called()
                self.assertIn("--yes", result.output)

    def test_invalid_key_and_input_are_rejected_without_network_access(self):
        result = self.invoke("read", "../secret")
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertFalse(self.requests_seen)
        source = self.root / "bad.json"
        source.write_text('{"secret": [', encoding="utf-8")
        result = self.invoke("write", "ABCD1234", "--input", str(source))
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertFalse(self.requests_seen)


if __name__ == "__main__":
    unittest.main()
