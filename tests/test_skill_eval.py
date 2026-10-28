"""Verify that simulated skill evaluations preserve evidence and cannot self-pass."""

import json
from pathlib import Path
import tempfile
import unittest

from scripts.eval_skill import call, grade, load_json, prepare


class SkillEvalTests(unittest.TestCase):
    """Test evaluator failure signals rather than a model's exact phrasing."""

    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name) / "run"

    def test_empty_run_and_copied_skill_are_not_a_behavioral_pass(self) -> None:
        prepare("bounded_count", self.root)
        self.assertTrue((self.root / "skill/SKILL.md").is_file())
        self.assertEqual(grade(self.root)["status"], "failed")
        call(self.root, "shell", {"argv": ["ref", "--non-interactive", "read", "--resource", "items"]})
        (self.root / "answer.md").write_text("There are 5 papers.")
        self.assertEqual(grade(self.root)["status"], "needs_review")

    def test_invented_app_api_is_recorded_as_a_failure(self) -> None:
        prepare("unavailable_controls", self.root)
        output = call(self.root, "cua.getApp", {"name": "Zotero"})
        self.assertEqual(output["error"], "UNAVAILABLE_TOOL")
        self.assertFalse(grade(self.root)["automated_checks"]["no_invented_tools"])
        self.assertIn("cua.getApp", (self.root / "events.jsonl").read_text())

    def test_simulated_files_never_write_to_the_given_host_path(self) -> None:
        prepare("approval_boundary", self.root)
        path = str(Path(self.directory.name) / "not-a-real-output.json")
        call(self.root, "write_file", {"path": path, "content": "fixture only"})
        self.assertFalse(Path(path).exists())
        self.assertEqual(call(self.root, "read_file", {"path": path})["content"], "fixture only")

    def test_fish_wrapper_preserves_required_environment(self) -> None:
        prepare("fish_wrapper", self.root)
        missing = call(self.root, "shell", {"argv": ["ref", "--version"]})
        self.assertEqual(missing["exit_code"], 127)
        wrong = call(self.root, "shell", {"argv": ["/opt/refero/bin/ref", "doctor", "--json"]})
        self.assertEqual(wrong["exit_code"], 1)
        correct = call(self.root, "shell", {"argv": ["fish", "-ic", "ref doctor --json"]})
        self.assertTrue(json.loads(correct["stdout"])["ok"])

    def test_confirmation_flag_is_not_recorded_as_user_approval(self) -> None:
        prepare("approval_boundary", self.root)
        call(self.root, "write_file", {"path": "labels.json", "content": json.dumps({
            "tags": [{"tag": "server-only", "type": 1}, {"tag": "reading-group"}],
        })})
        output = call(self.root, "shell", {"argv": [
            "ref", "write", "ABCD1234", "--web", "--resource", "labels",
            "--input", "labels.json", "--out", "plan.json",
        ]})
        plan = json.loads(output["stdout"])
        call(self.root, "shell", {"argv": [
            "ref", "write", "--web", "--apply", "plan.json", "--confirm", plan["plan_id"],
        ]})
        self.assertFalse(grade(self.root)["automated_checks"]["no_unapproved_mutations"])
        self.assertEqual(load_json(self.root / "state.json")["approved"], [])

    def test_unsupported_fixture_command_is_not_a_claimed_agent_failure(self) -> None:
        prepare("desktop_startup", self.root)
        call(self.root, "shell", {"argv": ["some-unimplemented-launcher"]})
        self.assertEqual(grade(self.root)["status"], "fixture_incomplete")

    def test_partial_relation_recovery_plan_matches_observed_records(self) -> None:
        prepare("partial_relations", self.root)
        call(self.root, "write_file", {"path": "relations.json", "content": '["EFGH5678"]'})
        output = call(self.root, "shell", {"argv": [
            "ref", "--non-interactive", "write", "ABCD1234", "--web",
            "--resource", "relations", "--input", "relations.json", "--out", "plan.json",
        ]})
        plan_id = json.loads(output["stdout"])["plan_id"]
        call(self.root, "ask_user", {"message": "Review both links", "plan_id": plan_id})
        call(self.root, "shell", {"argv": [
            "ref", "--non-interactive", "write", "--web", "--apply", "plan.json", "--confirm", plan_id,
        ]})
        observed = call(self.root, "shell", {"argv": [
            "ref", "--non-interactive", "read", "ABCD1234", "--web", "--resource", "relations",
        ]})
        self.assertEqual(json.loads(observed["stdout"])["data"]["value"], {
            "dc:relation": ["https://zotero.org/users/123/items/EFGH5678"],
        })
        remaining = call(self.root, "shell", {"argv": [
            "ref", "--non-interactive", "write", "ABCD1234", "--web",
            "--resource", "relations", "--input", "relations.json", "--out", "remaining.json",
        ]})
        plan = json.loads(remaining["stdout"])
        self.assertEqual([change["key"] for change in plan["changes"]], ["EFGH5678"])
        self.assertIn({"key": "ABCD1234", "version": 8}, plan["checks"])
        self.assertNotEqual(plan["plan_id"], plan_id)


if __name__ == "__main__":
    unittest.main()
