from contextlib import redirect_stdout
from dataclasses import replace
from io import StringIO
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from advisor import (
    AdvisorPreferences,
    ConsultationRequest,
    build_brief,
    consult,
    load_preferences,
    main,
    update_preferences,
)


class AdvisorTests(unittest.TestCase):
    def request(self):
        return ConsultationRequest(
            goal="Make the parser accept trailing commas.",
            current_hypothesis="The tokenizer rejects the comma before the closing bracket.",
            attempted_approaches=("Changed the parser rule.", "Added a tokenizer branch."),
            failures=("The fixture still fails with an unexpected token.",),
            constraints=("Use the standard library only.",),
            question="Which layer should own the fix?",
            parent_assumptions=("The existing fixture represents the production input.",),
        )

    def advice_output(self):
        return json.dumps(
            {
                "evidence": ["The tokenizer emits the comma token."],
                "risks_or_alternatives": ["Changing the parser may hide malformed input."],
                "next_steps": ["Trace the token stream for the failing fixture."],
                "uncertainty": "The fixture does not cover nested arrays.",
            }
        )

    def test_brief_separates_reported_facts_from_parent_assumptions(self):
        brief = build_brief(self.request())

        self.assertIn("## Facts reported by the parent", brief)
        self.assertIn("## Constraints reported by the parent", brief)
        self.assertIn("## Parent assumptions (unverified)", brief)
        self.assertIn("- Current hypothesis: The tokenizer rejects", brief)
        self.assertIn("- Attempted approach: Changed the parser rule.", brief)
        self.assertIn("- Observed failure: The fixture still fails", brief)
        self.assertIn("- Constraint: Use the standard library only.", brief)
        self.assertIn("- Parent assumption: The existing fixture represents the production input.", brief)
        self.assertIn("## Precise question\nWhich layer should own the fix?", brief)

    def test_consult_uses_ephemeral_read_only_invocation_and_preserves_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            fixture = repo / "fixture.txt"
            fixture.write_text("unchanged\n", encoding="utf-8")
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                return subprocess.CompletedProcess(argv, 0, self.advice_output(), "")

            result = consult(
                self.request(),
                repo=repo,
                codex_executable="codex-test",
                runner=runner,
            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.advice.evidence, ("The tokenizer emits the comma token.",))
            self.assertEqual(result.advice.next_steps, ("Trace the token stream for the failing fixture.",))
            self.assertEqual(
                calls[0][0],
                [
                    "codex-test",
                    "--sandbox",
                    "read-only",
                    "--ask-for-approval",
                    "never",
                    "exec",
                    "--ephemeral",
                    "-",
                ],
            )
            self.assertEqual(calls[0][1]["cwd"], str(repo.resolve()))
            self.assertEqual(calls[0][1]["input"], result.brief)
            self.assertTrue(calls[0][1]["text"])
            self.assertTrue(calls[0][1]["capture_output"])
            self.assertFalse(calls[0][1]["check"])
            self.assertFalse(calls[0][1]["shell"])
            self.assertEqual(fixture.read_text(encoding="utf-8"), "unchanged\n")

    def test_consult_snapshots_project_preferences_before_starting_codex(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            preferences = repo / ".codex" / "advisor.toml"
            preferences.parent.mkdir()
            preferences.write_text(
                'model = "o3"\nreasoning_effort = "high"\n',
                encoding="utf-8",
            )
            calls = []

            def runner(argv, **kwargs):
                calls.append((argv, kwargs))
                preferences.write_text(
                    'model = "gpt-5"\nreasoning_effort = "low"\n',
                    encoding="utf-8",
                )
                return subprocess.CompletedProcess(argv, 0, self.advice_output(), "")

            result = consult(
                self.request(),
                repo=repo,
                codex_executable="codex-test",
                runner=runner,
            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(
                calls[0][0],
                [
                    "codex-test",
                    "--sandbox",
                    "read-only",
                    "--ask-for-approval",
                    "never",
                    "--model",
                    "o3",
                    "--config",
                    'model_reasoning_effort="high"',
                    "exec",
                    "--ephemeral",
                    "-",
                ],
            )

    def test_one_off_settings_override_defaults_without_persisting(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            preferences = repo / ".codex" / "advisor.toml"
            preferences.parent.mkdir()
            original = 'model = "o3"\nreasoning_effort = "high"\n'
            preferences.write_text(original, encoding="utf-8")
            calls = []

            def runner(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, self.advice_output(), "")

            result = consult(
                replace(self.request(), model="gpt-5", reasoning_effort="low"),
                repo=repo,
                codex_executable="codex-test",
                runner=runner,
            )

            self.assertEqual(result.status, "ok")
            self.assertIn("--model", calls[0])
            self.assertEqual(calls[0][calls[0].index("--model") + 1], "gpt-5")
            self.assertIn('model_reasoning_effort="low"', calls[0])
            self.assertEqual(preferences.read_text(encoding="utf-8"), original)

    def test_preference_update_writes_advisor_file_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "project"
            repo.mkdir()
            preferences = repo / ".codex" / "advisor.toml"
            preferences.parent.mkdir()
            preferences.write_text(
                'model = "o3"\nreasoning_effort = "high"\n',
                encoding="utf-8",
            )
            home = root / "home"
            global_config = home / ".codex" / "config.toml"
            global_config.parent.mkdir(parents=True)
            global_config.write_text('model = "global-default"\n', encoding="utf-8")

            with patch("pathlib.Path.home", return_value=home):
                updated = update_preferences("set model to gpt-5", repo=repo)

            self.assertEqual(updated, AdvisorPreferences("gpt-5", "high"))
            self.assertEqual(load_preferences(repo), updated)
            self.assertEqual(global_config.read_text(encoding="utf-8"), 'model = "global-default"\n')

    def test_incompatible_settings_fail_before_codex_without_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            calls = []

            def runner(argv, **kwargs):
                calls.append(argv)
                return subprocess.CompletedProcess(argv, 0, self.advice_output(), "")

            result = consult(
                self.request(),
                repo=directory,
                model="gpt-5",
                reasoning_effort="xhigh",
                runner=runner,
            )

            self.assertEqual(result.status, "invalid_preferences")
            self.assertIn("not supported", result.error)
            self.assertEqual(calls, [])

    def test_invalid_preference_change_does_not_rewrite_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            preferences = repo / ".codex" / "advisor.toml"
            preferences.parent.mkdir()
            original = 'model = "o3"\nreasoning_effort = "high"\n'
            preferences.write_text(original, encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "unsupported model"):
                update_preferences("set model to unsupported-model", repo=repo)

            self.assertEqual(preferences.read_text(encoding="utf-8"), original)

    def test_main_exposes_the_narrow_preference_update_entry_point(self):
        with tempfile.TemporaryDirectory() as directory:
            output = StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    ["--repo", directory, "--set-preference", "set model to o3"]
                )

            self.assertEqual(exit_code, 0)
            self.assertEqual(
                json.loads(output.getvalue()),
                {
                    "status": "ok",
                    "preferences": {"model": "o3", "reasoning_effort": None},
                },
            )

    def test_unavailable_codex_is_recoverable(self):
        def runner(*args, **kwargs):
            raise FileNotFoundError("codex")

        result = consult(self.request(), runner=runner)

        self.assertEqual(result.status, "unavailable")
        self.assertTrue(result.recoverable)
        self.assertIn("not found", result.error)
        self.assertEqual(result.advice.evidence, ())

    def test_nonzero_exit_is_recoverable_and_keeps_diagnostics(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 9, "partial advice", "sandbox failure")

        result = consult(self.request(), runner=runner)

        self.assertEqual(result.status, "failed")
        self.assertTrue(result.recoverable)
        self.assertEqual(result.exit_code, 9)
        self.assertEqual(result.raw_output, "partial advice")
        self.assertEqual(result.error, "sandbox failure")

    def test_empty_advice_is_recoverable(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "\n", "")

        result = consult(self.request(), runner=runner)

        self.assertEqual(result.status, "empty")
        self.assertTrue(result.recoverable)
        self.assertEqual(result.advice.next_steps, ())
        self.assertIn("no advice", result.error.lower())

    def test_invalid_advice_is_recoverable(self):
        def runner(argv, **kwargs):
            return subprocess.CompletedProcess(argv, 0, "not json", "")

        result = consult(self.request(), runner=runner)

        self.assertEqual(result.status, "invalid_advice")
        self.assertTrue(result.recoverable)
        self.assertEqual(result.raw_output, "not json")
        self.assertIn("valid JSON", result.error)


if __name__ == "__main__":
    unittest.main()
