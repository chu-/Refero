"""Exercise portable skill installation without changing personal agent config."""

import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from scripts.install_skill import main


class SkillInstallTests(unittest.TestCase):
    """Verify project discovery paths and explicit replacement behavior."""

    def test_installs_self_contained_skill_for_all_targets(self) -> None:
        source = Path(__file__).resolve().parents[1] / "skills" / "refero"
        for agent, discovery in (
            ("agents", ".agents"), ("codex", ".agents"), ("claude-code", ".claude"),
        ):
            with (
                self.subTest(agent=agent),
                tempfile.TemporaryDirectory() as directory,
                redirect_stdout(StringIO()),
            ):
                main(["--agent", agent, "--project", directory])
                installed = Path(directory) / discovery / "skills" / "refero"
                expected = {
                    path.relative_to(source): path.read_bytes()
                    for path in source.rglob("*") if path.is_file()
                }
                actual = {
                    path.relative_to(installed): path.read_bytes()
                    for path in installed.rglob("*") if path.is_file()
                }
                self.assertEqual(actual, expected)

    def test_shared_target_defaults_to_user_scope(self) -> None:
        with (
            tempfile.TemporaryDirectory() as directory,
            patch("scripts.install_skill.Path.home", return_value=Path(directory)),
            redirect_stdout(StringIO()),
        ):
            main(["--agent", "agents"])
            self.assertTrue(
                (Path(directory) / ".agents/skills/refero/SKILL.md").is_file()
            )

    def test_existing_skill_is_preserved_until_replacement_is_explicit(self) -> None:
        with tempfile.TemporaryDirectory() as directory, redirect_stdout(StringIO()):
            arguments = ["--agent", "codex", "--project", directory]
            main(arguments)
            installed = Path(directory) / ".agents" / "skills" / "refero"
            skill = installed / "SKILL.md"
            skill.write_text("local edits", encoding="utf-8")
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as failure:
                main(arguments)
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(skill.read_text(), "local edits")
            custom = installed / "custom.md"
            custom.write_text("local reference", encoding="utf-8")
            shared = ["--agent", "agents", "--project", directory]
            with redirect_stderr(StringIO()), self.assertRaises(SystemExit) as failure:
                main(shared)
            self.assertEqual(failure.exception.code, 1)
            self.assertEqual(skill.read_text(), "local edits")
            main([*shared, "--replace"])
            self.assertIn("name: refero", skill.read_text())
            self.assertEqual(custom.read_text(), "local reference")


if __name__ == "__main__":
    unittest.main()
