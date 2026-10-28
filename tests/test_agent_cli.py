"""Verify opt-in automation and preserve the existing human CLI behavior."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests
from typer import Context
from typer.main import get_command
from typer.testing import CliRunner

from ref.cli import app
from ref.utils.zotero import run_with_network_retry, zot_from_config


class AgentCliTests(unittest.TestCase):
    """Exercise real command parsing and output against an isolated library stub."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.config_path = Path(self.directory.name) / "config.yaml"
        environment = patch.dict(os.environ, {
            "REF_CONFIG": str(self.config_path),
            "REF_NON_INTERACTIVE": "0",
            "NO_COLOR": "1",
        })
        environment.start()
        self.addCleanup(environment.stop)
        self.runner = CliRunner()
        self.item = {
            "key": "ABCD1234", "version": 7,
            "data": {
                "itemType": "journalArticle", "title": "图神经网络",
                "creators": [{"firstName": "Ada", "lastName": "Smith"}],
                "date": "2025-03-02", "DOI": "10.1234/example",
                "publicationTitle": "Example Journal",
                "tags": [{"tag": "reading"}], "collections": ["COLL1234"],
            },
        }
        self.client = Mock()
        self.client.list_items.return_value = [self.item]
        self.client.get_item.return_value = self.item
        self.client.collections.return_value = [{
            "key": "COLL1234", "data": {"name": "Reading List"},
        }]

    def _write_config(self, text: str = "zotero:\n  mode: local\n") -> None:
        self.config_path.write_text(text, encoding="utf-8")

    def _list(self, arguments: list[str]):
        with patch("ref.commands.listing.zot_from_config", return_value=self.client):
            return self.runner.invoke(app, arguments)

    def test_version_does_not_load_configuration(self) -> None:
        result = self.runner.invoke(app, ["--version"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(result.stdout.strip(), "Refero 0.1.0")
        self.assertFalse(self.config_path.exists())

    def test_json_search_has_stable_keys_and_unformatted_unicode(self) -> None:
        result = self._list([
            "--non-interactive", "list", "--query", "graph neural",
            "--limit", "20", "--json",
        ])
        self.assertEqual(result.exit_code, 0, result.output)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["count"], len(payload["items"]))
        self.assertEqual(payload["items"][0]["key"], "ABCD1234")
        self.assertEqual(payload["items"][0]["title"], "图神经网络")
        self.assertEqual(payload["items"][0]["creators"], ["Smith, Ada"])
        self.assertEqual(payload["items"][0]["tags"], ["reading"])
        self.assertNotIn("\x1b", result.stdout)
        self.client.list_items.assert_called_once_with(
            limit=20, q="graph neural", collection=None, tag=None,
            author=None, title=None, year=None, doi=None,
        )

    def test_empty_search_is_successful_json(self) -> None:
        self.client.list_items.return_value = []
        result = self._list(["list", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout), {
            "schema_version": 1, "items": [], "count": 0,
        })

    def test_grouped_json_contains_collection_paths(self) -> None:
        result = self._list(["list", "--json", "--group-by-collection"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(
            json.loads(result.stdout)["items"][0]["collection_paths"],
            ["Reading List"],
        )

    def test_shortcut_json_does_not_consume_query_after_boolean_flag(self) -> None:
        result = self._list(["--json", "graph", "neural", "--year", "2025"])
        self.assertEqual(result.exit_code, 0, result.output)
        json.loads(result.stdout)
        arguments = self.client.list_items.call_args.kwargs
        self.assertEqual(arguments["q"], "graph neural")
        self.assertEqual(arguments["year"], "2025")

    def test_human_shortcut_matches_explicit_list(self) -> None:
        shortcut = self._list(["graph", "neural", "--author", "smith"])
        explicit = self._list(["list", "graph", "neural", "--author", "smith"])
        self.assertEqual(shortcut.exit_code, 0, shortcut.output)
        self.assertEqual(shortcut.stdout, explicit.stdout)
        self.assertIn("图神经网络", shortcut.stdout)
        self.assertIn("ABCD1234", shortcut.stdout)
        self.assertNotIn('"schema_version"', shortcut.stdout)

    def test_view_json_only_shape_and_new_alias_match(self) -> None:
        old = self._list(["view", "ABCD1234", "--json-only"])
        new = self._list(["--non-interactive", "view", "ABCD1234", "--json"])
        self.assertEqual(old.exit_code, 0, old.output)
        self.assertEqual(new.exit_code, 0, new.output)
        self.assertEqual(old.stdout, new.stdout)
        payload = json.loads(old.stdout)
        self.assertEqual(payload["key"], "ABCD1234")
        self.assertEqual(payload["collection_paths"], ["Reading List"])
        self.assertNotIn("schema_version", payload)

    def test_all_interactive_commands_and_aliases_fail_before_backend_access(self) -> None:
        commands = (
            "pick", "edit", "picker-edit", "note", "abstract", "extra",
            "label", "tag", "collection", "coll", "relate", "link",
        )
        with patch("ref.backends.zotero_client.Zotero") as backend:
            for command in commands:
                with self.subTest(command=command):
                    arguments = ["--non-interactive", command]
                    if command != "pick":
                        arguments.append("ABCD1234")
                    result = self.runner.invoke(app, arguments)
                    self.assertEqual(result.exit_code, 2, result.output)
                    self.assertIn("requires interaction", result.output)
            backend.assert_not_called()
        self.assertFalse(self.config_path.exists())

    def test_environment_can_disable_interaction(self) -> None:
        result = self.runner.invoke(
            app, ["edit", "ABCD1234"], env={"REF_NON_INTERACTIVE": "1"},
        )
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("requires interaction", result.output)

    def test_human_picker_still_runs_without_opt_in(self) -> None:
        picker = Mock(return_value=[{"key": "ABCD1234"}])
        with patch("ref.commands.listing.FzfPicker", return_value=picker):
            result = self._list(["pick", "graph"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout), {"key": "ABCD1234"})
        picker.assert_called_once()

    def test_non_interactive_policy_does_not_leak_into_next_invocation(self) -> None:
        blocked = self.runner.invoke(app, ["--non-interactive", "edit", "ABCD1234"])
        self.assertEqual(blocked.exit_code, 2)
        with patch("ref.commands.editing.core.zot_from_config", return_value=self.client):
            with patch(
                "ref.commands.editing.core.subprocess.run",
                side_effect=RuntimeError("editor invoked"),
            ) as editor:
                result = self.runner.invoke(app, ["edit", "ABCD1234"])
        editor.assert_called_once()
        self.assertIsInstance(result.exception, RuntimeError)
        self.assertEqual(str(result.exception), "editor invoked")
        self.client.update_item_data.assert_not_called()

    def test_interactive_command_help_remains_available_in_unattended_mode(self) -> None:
        result = self.runner.invoke(app, ["--non-interactive", "label", "--help"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("--pick", result.output)

    def test_delete_requires_explicit_yes_before_backend_access(self) -> None:
        with patch("ref.commands.management.zot_from_config") as factory:
            result = self.runner.invoke(app, ["--non-interactive", "delete", "ABCD1234"])
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("--yes", result.output)
        factory.assert_not_called()

    def test_human_delete_keeps_confirmation_and_cancel_behavior(self) -> None:
        with patch("ref.commands.management.zot_from_config", return_value=self.client):
            result = self.runner.invoke(app, ["delete", "ABCD1234"], input="n\n")
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Confirm", result.output)
        self.assertIn("Aborted", result.output)
        self.client.trash_items.assert_not_called()

    def test_authorized_non_interactive_delete_does_not_prompt(self) -> None:
        with patch("ref.commands.management.zot_from_config", return_value=self.client):
            with patch(
                "ref.commands.management.typer.confirm",
                side_effect=AssertionError("unexpected prompt"),
            ):
                result = self.runner.invoke(app, [
                    "--non-interactive", "delete", "ABCD1234", "--yes",
                ])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.trash_items.assert_called_once_with(["ABCD1234"])

    def test_unattended_set_and_read_back_preserve_existing_tags(self) -> None:
        self._write_config()

        def update_item(key, updates):
            self.assertEqual(key, self.item["key"])
            self.item["data"].update(updates)

        self.client.update_item_data.side_effect = update_item
        with patch("ref.commands.management.zot_from_config", return_value=self.client):
            result = self.runner.invoke(app, [
                "--non-interactive", "set", "ABCD1234", "--tag", "ml", "--yes",
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        viewed = self._list(["--non-interactive", "view", "ABCD1234", "--json-only"])
        self.assertEqual(json.loads(viewed.stdout)["tags"], ["reading", "ml"])

    def test_human_set_retains_its_original_confirmation_behavior(self) -> None:
        self._write_config()
        with patch("ref.commands.management.zot_from_config", return_value=self.client):
            result = self.runner.invoke(app, ["set", "ABCD1234", "--tag", "ml"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.update_item_data.assert_called_once()

    def test_export_selected_keys_to_a_parseable_file(self) -> None:
        self._write_config()
        destination = Path(self.directory.name) / "references.json"
        self.client.export_csljson.return_value = [{"id": "example2025", "title": "图神经网络"}]
        with patch("ref.commands.management.zot_from_config", return_value=self.client):
            result = self.runner.invoke(app, [
                "--non-interactive", "export", "ABCD1234", "--format", "csljson",
                "--out", str(destination),
            ])
        self.assertEqual(result.exit_code, 0, result.output)
        self.client.export_csljson.assert_called_once_with(["ABCD1234"])
        self.assertEqual(
            json.loads(destination.read_text(encoding="utf-8"))[0]["title"],
            "图神经网络",
        )

    def test_network_failure_does_not_prompt_or_repeat_in_unattended_mode(self) -> None:
        operation = Mock(side_effect=requests.ConnectionError("offline"))
        with Context(get_command(app)) as context:
            context.params["non_interactive"] = True
            with patch("ref.utils.zotero.typer.confirm") as confirm:
                with self.assertRaises(requests.ConnectionError):
                    run_with_network_retry(operation)
        operation.assert_called_once()
        confirm.assert_not_called()

    def test_human_network_retry_still_prompts(self) -> None:
        operation = Mock(side_effect=[requests.ConnectionError("offline"), "done"])
        with Context(get_command(app)):
            with patch("ref.utils.zotero.typer.confirm", return_value=True) as confirm:
                self.assertEqual(run_with_network_retry(operation), "done")
        self.assertEqual(operation.call_count, 2)
        confirm.assert_called_once()

    def test_web_probe_only_changes_for_explicit_unattended_mode(self) -> None:
        self._write_config("zotero:\n  mode: web\n  library_id: 123\n  api_key: private\n")
        for unattended in (False, True):
            with self.subTest(unattended=unattended):
                client = Mock()
                with Context(get_command(app)) as context:
                    context.params["non_interactive"] = unattended
                    with patch("ref.utils.zotero.ZoteroClient", return_value=client):
                        self.assertIs(zot_from_config(), client)
                if unattended:
                    client.list_items.assert_called_once_with(limit=1)
                    client.create_items.assert_not_called()
                else:
                    client.create_items.assert_called_once_with([])
                    client.list_items.assert_not_called()

    def test_doctor_missing_config_does_not_create_files(self) -> None:
        result = self.runner.invoke(app, ["doctor", "--json", "--no-connect"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse(self.config_path.exists())

    def test_doctor_preserves_config_contents_and_permissions(self) -> None:
        self._write_config()
        self.config_path.chmod(0o644)
        contents_before = self.config_path.read_bytes()
        mode_before = stat.S_IMODE(self.config_path.stat().st_mode)
        with patch("ref.commands.diagnostics.ZoteroClient") as factory:
            result = self.runner.invoke(app, ["doctor", "--json", "--no-connect"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertTrue(json.loads(result.stdout)["ok"])
        factory.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), contents_before)
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), mode_before)

    def test_doctor_reads_empty_library_without_writes(self) -> None:
        self._write_config()
        self.client.list_items.return_value = []
        with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
            with patch("ref.commands.diagnostics.shutil.which", return_value=None):
                result = self.runner.invoke(app, ["doctor", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertTrue(report["ok"])
        checks = {check["name"]: check for check in report["checks"]}
        self.assertEqual(checks["picker"]["status"], "warning")
        self.assertEqual(checks["connection"]["status"], "ok")
        self.client.list_items.assert_called_once_with(limit=1)
        self.client.create_items.assert_not_called()
        self.client.update_item_data.assert_not_called()

    def test_doctor_invalid_yaml_does_not_echo_config_secrets(self) -> None:
        self._write_config("zotero:\n  api_key: [private-secret\n")
        with patch("ref.commands.diagnostics.ZoteroClient") as factory:
            result = self.runner.invoke(app, ["doctor", "--json"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertNotIn("private-secret", result.output)
        self.assertFalse(json.loads(result.stdout)["ok"])
        factory.assert_not_called()

    def test_doctor_web_uses_web_target_even_when_local_api_is_unavailable(self) -> None:
        self._write_config(
            "zotero:\n  mode: local\n  library_id: 123\n  library_type: group\n"
            "  api_key: private-secret\n  local_api: http://localhost:23119/api\n"
            "  endpoint: https://web.example/api\n"
        )
        contents_before = self.config_path.read_bytes()
        mode_before = stat.S_IMODE(self.config_path.stat().st_mode)
        local_client = Mock()
        local_client.list_items.side_effect = requests.ConnectionError("offline")

        def client_for_target(**kwargs):
            return local_client if kwargs["local"] else self.client

        with patch("ref.commands.diagnostics.ZoteroClient", side_effect=client_for_target) as factory:
            local = self.runner.invoke(app, ["doctor", "--json"])
            self.assertEqual(local.exit_code, 1, local.output)
            self.assertEqual(json.loads(local.stdout)["backend"], "local")
            result = self.runner.invoke(app, ["doctor", "--web", "--json"])
        self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual(json.loads(result.stdout)["backend"], "web")
        self.assertTrue(json.loads(result.stdout)["ok"])
        factory.assert_called_with(
            library_id=123, library_type="group", api_key="private-secret",
            endpoint="https://web.example/api", local=False,
        )
        local_client.list_items.assert_called_once_with(limit=1)
        self.client.list_items.assert_called_once_with(limit=1)
        self.client.create_items.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), contents_before)
        self.assertEqual(stat.S_IMODE(self.config_path.stat().st_mode), mode_before)
        self.assertNotIn("private-secret", result.output)

    def test_doctor_web_requires_credentials_even_without_connection(self) -> None:
        self._write_config()
        with patch("ref.commands.diagnostics.ZoteroClient") as factory:
            for arguments in (["doctor", "--web", "--json"],
                              ["doctor", "--web", "--json", "--no-connect"]):
                with self.subTest(arguments=arguments):
                    result = self.runner.invoke(app, arguments)
                    self.assertEqual(result.exit_code, 1, result.output)
                    report = json.loads(result.stdout)
                    self.assertEqual(report["backend"], "web")
                    self.assertFalse(report["ok"])
                    self.assertIn("library_id", result.stdout)
            factory.assert_not_called()

    def test_doctor_web_no_connect_validates_without_contacting_either_backend(self) -> None:
        self._write_config("zotero:\n  mode: local\n  library_id: 123\n  api_key: test\n")
        with patch("ref.commands.diagnostics.ZoteroClient") as factory:
            result = self.runner.invoke(app, ["doctor", "--web", "--json", "--no-connect"])
        self.assertEqual(result.exit_code, 0, result.output)
        report = json.loads(result.stdout)
        self.assertEqual(report["backend"], "web")
        check = next(check for check in report["checks"] if check["name"] == "connection")
        self.assertEqual(check["status"], "skipped")
        factory.assert_not_called()

    def test_doctor_web_failure_keeps_explicit_web_retry(self) -> None:
        self._write_config("zotero:\n  mode: local\n  library_id: 123\n  api_key: test\n")
        self.client.list_items.side_effect = requests.ConnectionError("private-secret")
        with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
            result = self.runner.invoke(app, ["doctor", "--web", "--json"])
        self.assertEqual(result.exit_code, 1, result.output)
        report = json.loads(result.stdout)
        check = next(check for check in report["checks"] if check["name"] == "connection")
        self.assertEqual(check["code"], "WEB_READ_FAILED")
        self.assertIn("doctor --web --json", check["next_steps"][-1])
        self.assertNotIn("Zotero Desktop", result.stdout)
        self.assertNotIn("private-secret", result.output)

    def test_doctor_rejects_invalid_config_without_connecting(self) -> None:
        configs = (
            "- invalid\n",
            "zotero: invalid\n",
            "zotero:\n  mode: invalid\n",
            "zotero:\n  mode: web\n",
            "zotero:\n  mode: local\n  library_type: invalid\n",
            "zotero:\n  mode: local\n  unknown: private-secret\n",
        )
        with patch("ref.commands.diagnostics.ZoteroClient") as factory:
            for config in configs:
                with self.subTest(config=config):
                    self._write_config(config)
                    result = self.runner.invoke(app, ["doctor", "--json"])
                    self.assertEqual(result.exit_code, 1, result.output)
                    self.assertFalse(json.loads(result.stdout)["ok"])
                    self.assertNotIn("private-secret", result.output)
            factory.assert_not_called()

    def test_doctor_connection_failure_is_structured_and_redacted(self) -> None:
        self._write_config()
        response = requests.Response()
        response.status_code = 403
        self.client.list_items.side_effect = requests.HTTPError(
            "private-secret", response=response,
        )
        with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
            result = self.runner.invoke(app, ["doctor", "--json"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertIn("HTTP 403", result.stdout)
        self.assertNotIn("private-secret", result.output)

    def test_doctor_local_connection_failure_suggests_desktop_recovery(self) -> None:
        self._write_config()
        contents_before = self.config_path.read_bytes()
        self.client.list_items.side_effect = requests.ConnectionError("private-secret")
        with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
            result = self.runner.invoke(app, ["doctor", "--json"])
        self.assertEqual(result.exit_code, 1, result.output)
        report = json.loads(result.stdout)
        check = next(check for check in report["checks"] if check["name"] == "connection")
        self.assertEqual(check["code"], "LOCAL_API_UNAVAILABLE")
        self.assertIn("may be closed", check["message"])
        self.assertIn("Start Zotero Desktop", check["next_steps"][0])
        self.assertIn("Allow other applications", check["next_steps"][1])
        self.assertFalse(report["ok"])
        self.assertNotIn("private-secret", result.output)
        self.assertEqual(self.config_path.read_bytes(), contents_before)
        self.client.list_items.assert_called_once_with(limit=1)
        self.client.create_items.assert_not_called()

    def test_doctor_http_recovery_is_specific_to_library_mode(self) -> None:
        cases = (
            ("local", 403, "LOCAL_API_ACCESS_DENIED", "Allow other applications"),
            ("web", 403, "WEB_ACCESS_DENIED", "API key"),
            ("web", 404, "WEB_LIBRARY_NOT_FOUND", "library ID"),
            ("web", 500, "WEB_READ_FAILED", "network settings"),
        )
        for mode, status, code, advice in cases:
            with self.subTest(mode=mode, status=status):
                self._write_config(
                    f"zotero:\n  mode: {mode}\n  library_id: 123\n"
                    "  api_key: private-secret\n"
                )
                response = requests.Response()
                response.status_code = status
                self.client.list_items.side_effect = requests.HTTPError(
                    "private-secret", response=response,
                )
                with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
                    result = self.runner.invoke(app, ["doctor", "--json"])
                self.assertEqual(result.exit_code, 1, result.output)
                check = next(
                    check for check in json.loads(result.stdout)["checks"]
                    if check["name"] == "connection"
                )
                self.assertEqual(check["code"], code)
                self.assertIn(advice, " ".join(check["next_steps"]))
                self.assertNotIn("private-secret", result.output)
                if mode == "web":
                    self.assertNotIn("Zotero Desktop", result.stdout)

    def test_doctor_text_includes_recovery_steps(self) -> None:
        self._write_config()
        self.client.list_items.side_effect = requests.ConnectionError("private-secret")
        with patch("ref.commands.diagnostics.ZoteroClient", return_value=self.client):
            result = self.runner.invoke(app, ["doctor"])
        self.assertEqual(result.exit_code, 1, result.output)
        self.assertIn("Start Zotero Desktop", result.stdout)
        self.assertIn("doctor --json again", result.stdout)
        self.assertNotIn("private-secret", result.output)


if __name__ == "__main__":
    unittest.main()
