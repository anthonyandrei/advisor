from contextlib import redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class InstalledSkillSmokeTests(unittest.TestCase):
    def test_installed_helper_runs_from_unrelated_directory_with_read_only_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            skill = root / "skills" / "advisor"
            skill.mkdir(parents=True)
            shutil.copy2(PROJECT_ROOT / "SKILL.md", skill / "SKILL.md")
            shutil.copy2(PROJECT_ROOT / "advisor.py", skill / "advisor.py")

            repository = root / "active-repository"
            repository.mkdir()
            fixture = repository / "fixture.txt"
            fixture.write_text("unchanged\n", encoding="utf-8")
            caller = root / "caller"
            caller.mkdir()

            request = {
                "goal": "Check the installed helper.",
                "current_hypothesis": "The helper should not write the repository.",
                "question": "What should the parent verify?",
            }
            spec = importlib.util.spec_from_file_location("installed_advisor", skill / "advisor.py")
            self.assertIsNotNone(spec)
            self.assertIsNotNone(spec.loader)
            installed_advisor = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = installed_advisor
            spec.loader.exec_module(installed_advisor)
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(
                    argv,
                    0,
                    json.dumps(
                        {
                            "evidence": ["fixture was inspected"],
                            "risks_or_alternatives": [],
                            "next_steps": ["verify the fix"],
                            "uncertainty": "the smoke test uses a fake CLI",
                        }
                    ),
                    "",
                )

            output = io.StringIO()
            previous_directory = Path.cwd()
            try:
                os.chdir(caller)
                with patch.object(installed_advisor.subprocess, "run", runner), patch(
                    "sys.stdin", io.StringIO(json.dumps(request))
                ), redirect_stdout(output):
                    exit_code = installed_advisor.main(["--repo", str(repository)])
            finally:
                os.chdir(previous_directory)
                sys.modules.pop(spec.name, None)

            self.assertEqual(exit_code, 0)
            result = json.loads(output.getvalue())
            self.assertEqual(result["status"], "ok", result)
            self.assertIn(
                "name: advisor",
                (skill / "SKILL.md").read_text(encoding="utf-8"),
            )
            self.assertEqual(
                calls[0][0],
                [
                    "codex",
                    "--sandbox",
                    "read-only",
                    "--ask-for-approval",
                    "never",
                    "exec",
                    "--ephemeral",
                    "-",
                ],
            )
            self.assertIn(
                "Check the installed helper.",
                calls[0][1]["input"],
            )
            self.assertEqual(calls[0][1]["cwd"], str(repository.resolve()))
            self.assertEqual(fixture.read_text(encoding="utf-8"), "unchanged\n")
            self.assertFalse((repository / ".codex").exists())


if __name__ == "__main__":
    unittest.main()
