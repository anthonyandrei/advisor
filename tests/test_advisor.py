import json
import subprocess
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from advisor import (
    ConsultationRequest,
    NativeCapabilities,
    NativeProvider,
    build_brief,
    consult,
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

    def test_consult_selects_capable_native_and_passes_context(self):
        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            calls = []
            request = replace(
                self.request(),
                model="gpt-5",
                reasoning_effort="high",
                task_context="The parent is debugging the parser integration.",
            )

            def native(invocation):
                calls.append(invocation)
                return json.loads(self.advice_output())

            def fallback(*args, **kwargs):
                raise AssertionError("fallback should not run")

            result = consult(
                request,
                repo=repo,
                native_provider=NativeProvider(
                    capabilities=NativeCapabilities(
                        model="gpt-5",
                        reasoning_effort="high",
                        inherits_task_context=True,
                        read_only=True,
                    ),
                    invoke=native,
                ),
                runner=fallback,
            )

            self.assertEqual(result.status, "ok")
            self.assertEqual(result.advice.evidence, ("The tokenizer emits the comma token.",))
            self.assertEqual(
                result.advice.risks_or_alternatives,
                ("Changing the parser may hide malformed input.",),
            )
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0].repository, repo.resolve())
            self.assertEqual(calls[0].task_context, request.task_context)
            self.assertEqual(calls[0].brief, result.brief)
            self.assertEqual(calls[0].model, "gpt-5")
            self.assertEqual(calls[0].reasoning_effort, "high")
            self.assertTrue(calls[0].read_only)

    def test_consult_falls_back_for_unsafe_or_unsupported_native(self):
        request = replace(
            self.request(),
            model="gpt-5",
            reasoning_effort="high",
            task_context="The parent is debugging the parser integration.",
        )
        capabilities = (
            NativeCapabilities(
                model="gpt-5",
                reasoning_effort="high",
                inherits_task_context=False,
                read_only=True,
            ),
            NativeCapabilities(
                model="gpt-5",
                reasoning_effort="high",
                inherits_task_context=True,
                read_only=False,
            ),
            NativeCapabilities(
                model="other-model",
                reasoning_effort="high",
                inherits_task_context=True,
                read_only=True,
            ),
            NativeCapabilities(
                model="gpt-5",
                reasoning_effort="low",
                inherits_task_context=True,
                read_only=True,
            ),
            NativeCapabilities(
                model="gpt-5",
                reasoning_effort="high",
                inherits_task_context=True,
                read_only=True,
                available=False,
            ),
        )

        with tempfile.TemporaryDirectory() as directory:
            repo = Path(directory)
            for native_capabilities in capabilities:
                native_calls = []
                fallback_calls = []

                def native(invocation):
                    native_calls.append(invocation)
                    raise AssertionError("unsafe native provider should not run")

                def fallback(argv, **kwargs):
                    fallback_calls.append((argv, kwargs))
                    return subprocess.CompletedProcess(argv, 0, self.advice_output(), "")

                result = consult(
                    request,
                    repo=repo,
                    native_provider=NativeProvider(native_capabilities, native),
                    codex_executable="codex-test",
                    runner=fallback,
                )

                self.assertEqual(result.status, "ok")
                self.assertEqual(native_calls, [])
                self.assertEqual(len(fallback_calls), 1)
                self.assertEqual(
                    fallback_calls[0][0],
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

    def test_selected_native_failure_is_recoverable(self):
        request = replace(
            self.request(),
            model="gpt-5",
            reasoning_effort="high",
            task_context="The parent is debugging the parser integration.",
        )

        def native(_invocation):
            raise RuntimeError("native child stopped")

        result = consult(
            request,
            native_provider=NativeProvider(
                NativeCapabilities(
                    model="gpt-5",
                    reasoning_effort="high",
                    inherits_task_context=True,
                    read_only=True,
                ),
                native,
            ),
        )

        self.assertEqual(result.status, "failed")
        self.assertTrue(result.recoverable)
        self.assertIn("native child stopped", result.error)

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
