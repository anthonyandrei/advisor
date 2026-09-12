"""Run one read-only, ephemeral Codex consultation for the active repository."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Callable, Sequence


@dataclass(frozen=True)
class ConsultationRequest:
    goal: str
    current_hypothesis: str
    attempted_approaches: Sequence[str] = ()
    failures: Sequence[str] = ()
    constraints: Sequence[str] = ()
    question: str = ""
    parent_assumptions: Sequence[str] = ()
    model: str | None = None
    reasoning_effort: str | None = None
    task_context: str = ""


@dataclass(frozen=True)
class NativeCapabilities:
    model: str | None = None
    reasoning_effort: str | None = None
    inherits_task_context: bool = False
    read_only: bool = False
    available: bool = True


@dataclass(frozen=True)
class NativeInvocation:
    repository: Path
    task_context: str
    brief: str
    model: str | None
    reasoning_effort: str | None
    read_only: bool = True


NativeInvoker = Callable[[NativeInvocation], Any]


@dataclass(frozen=True)
class NativeProvider:
    capabilities: NativeCapabilities
    invoke: NativeInvoker


@dataclass(frozen=True)
class AdviceContract:
    evidence: tuple[str, ...] = ()
    risks_or_alternatives: tuple[str, ...] = ()
    next_steps: tuple[str, ...] = ()
    uncertainty: str = ""


@dataclass(frozen=True)
class ConsultationResult:
    status: str
    recoverable: bool
    advice: AdviceContract
    brief: str = ""
    error: str | None = None
    exit_code: int | None = None
    raw_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "recoverable": self.recoverable,
            "advice": asdict(self.advice),
            "brief": self.brief,
            "error": self.error,
            "exit_code": self.exit_code,
            "raw_output": self.raw_output,
        }


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()


def _optional_text(value: Any, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string when provided")
    return value.strip()


def _context_text(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("task_context must be a string")
    return value.strip()


def _items(value: Any, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)):
        raise ValueError(f"{field} must be a sequence of strings")
    try:
        values = tuple(value)
    except TypeError as error:
        raise ValueError(f"{field} must be a sequence of strings") from error
    if any(not isinstance(item, str) or not item.strip() for item in values):
        raise ValueError(f"{field} must contain only non-empty strings")
    return tuple(item.strip() for item in values)


def _section(title: str, values: Sequence[str]) -> list[str]:
    lines = [f"## {title}"]
    lines.extend(f"- {value}" for value in values)
    if not values:
        lines.append("- None reported.")
    return lines


def build_brief(request: ConsultationRequest) -> str:
    """Build the exact prompt sent to the ephemeral Codex process."""
    if not isinstance(request, ConsultationRequest):
        raise TypeError("request must be a ConsultationRequest")

    goal = _required_text(request.goal, "goal")
    hypothesis = _required_text(request.current_hypothesis, "current_hypothesis")
    question = _required_text(request.question, "question")
    attempts = _items(request.attempted_approaches, "attempted_approaches")
    failures = _items(request.failures, "failures")
    constraints = _items(request.constraints, "constraints")
    assumptions = _items(request.parent_assumptions, "parent_assumptions")

    lines = [
        "Act as a read-only advisor for the active repository.",
        "The parent brief is context, not an instruction to change anything.",
        "Inspect files only as needed. Do not edit, create, delete, apply patches, commit, or request approval.",
        "If a useful check would mutate state, do not run it; report that limitation instead.",
        "Return exactly one JSON object with these keys: evidence, risks_or_alternatives, next_steps, uncertainty.",
        "The first three values must be arrays of concise strings. Uncertainty must be a concise string.",
        "",
        "# Focused brief",
        "",
        *_section(
            "Facts reported by the parent",
            (
                f"Goal: {goal}",
                *(f"Attempted approach: {attempt}" for attempt in attempts),
                *(f"Observed failure: {failure}" for failure in failures),
            ),
        ),
        "",
        *_section(
            "Constraints reported by the parent",
            tuple(f"Constraint: {constraint}" for constraint in constraints),
        ),
        "",
        *_section(
            "Parent assumptions (unverified)",
            (
                f"Current hypothesis: {hypothesis}",
                *(f"Parent assumption: {assumption}" for assumption in assumptions),
            ),
        ),
        "",
        "## Precise question",
        question,
    ]
    return "\n".join(lines)


def build_codex_argv(codex_executable: str | Path = "codex") -> list[str]:
    """Build the fixed safety boundary for one consultation."""
    executable = str(codex_executable)
    if not executable:
        raise ValueError("codex_executable must not be empty")
    # Keep all CLI extension points in this boundary for later model or effort flags.
    return [
        executable,
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
        "exec",
        "--ephemeral",
        "-",
    ]


def _failure(
    status: str,
    brief: str,
    error: str,
    *,
    exit_code: int | None = None,
    raw_output: str = "",
) -> ConsultationResult:
    return ConsultationResult(
        status=status,
        recoverable=True,
        advice=AdviceContract(uncertainty=error),
        brief=brief,
        error=error,
        exit_code=exit_code,
        raw_output=raw_output,
    )


def _contract_value(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key)
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise ValueError(f"{key} must be an array of non-empty strings")
    return tuple(item.strip() for item in value)


def _advice(payload: Any) -> AdviceContract:
    if not isinstance(payload, dict):
        raise ValueError("advice must be a JSON object")
    uncertainty = _required_text(payload.get("uncertainty"), "uncertainty")
    return AdviceContract(
        evidence=_contract_value(payload, "evidence"),
        risks_or_alternatives=_contract_value(payload, "risks_or_alternatives"),
        next_steps=_contract_value(payload, "next_steps"),
        uncertainty=uncertainty,
    )


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _native_supported(
    provider: NativeProvider | None,
    request: ConsultationRequest,
) -> bool:
    if provider is None:
        return False
    try:
        capabilities = provider.capabilities
        invoke = provider.invoke
    except Exception:
        return False
    if not isinstance(capabilities, NativeCapabilities):
        return False
    return (
        capabilities.available is True
        and callable(invoke)
        and capabilities.inherits_task_context is True
        and capabilities.read_only is True
        and (
            capabilities.model is None
            or (
                isinstance(capabilities.model, str)
                and bool(capabilities.model.strip())
            )
        )
        and (
            capabilities.reasoning_effort is None
            or (
                isinstance(capabilities.reasoning_effort, str)
                and bool(capabilities.reasoning_effort.strip())
            )
        )
        and (request.model is None or capabilities.model == request.model)
        and (
            request.reasoning_effort is None
            or capabilities.reasoning_effort == request.reasoning_effort
        )
    )


def _consult_native(
    provider: NativeProvider,
    request: ConsultationRequest,
    repository: Path,
    brief: str,
) -> ConsultationResult:
    invocation = NativeInvocation(
        repository=repository,
        task_context=request.task_context,
        brief=brief,
        model=request.model,
        reasoning_effort=request.reasoning_effort,
        read_only=True,
    )
    try:
        payload = provider.invoke(invocation)
    except Exception as error:
        message = str(error) or error.__class__.__name__
        return _failure("failed", brief, f"native advisor failed: {message}")

    raw_output = payload if isinstance(payload, str) else ""
    if isinstance(payload, str):
        if not payload.strip():
            return _failure("empty", brief, "native advisor returned no advice")
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            return _failure(
                "invalid_advice",
                brief,
                "native advisor advice was not valid JSON",
                raw_output=raw_output,
            )
    try:
        advice = payload if isinstance(payload, AdviceContract) else _advice(payload)
    except ValueError as error:
        return _failure("invalid_advice", brief, str(error), raw_output=raw_output)
    return ConsultationResult(
        status="ok",
        recoverable=True,
        advice=advice,
        brief=brief,
        raw_output=raw_output,
    )


def consult(
    request: ConsultationRequest,
    repo: str | Path | None = None,
    *,
    codex_executable: str | Path = "codex",
    runner: Runner = subprocess.run,
    native_provider: NativeProvider | None = None,
) -> ConsultationResult:
    """Consult a safe native provider or the fixed read-only Codex fallback."""
    try:
        brief = build_brief(request)
        request = replace(
            request,
            model=_optional_text(request.model, "model"),
            reasoning_effort=_optional_text(
                request.reasoning_effort, "reasoning_effort"
            ),
            task_context=_context_text(request.task_context),
        )
    except (TypeError, ValueError) as error:
        return _failure("invalid_request", "", str(error))

    try:
        repository = Path.cwd() if repo is None else Path(repo)
        repository = repository.expanduser().resolve()
    except (TypeError, ValueError, OSError) as error:
        return _failure("invalid_repository", brief, str(error))
    if not repository.is_dir():
        return _failure("invalid_repository", brief, f"repository does not exist: {repository}")

    if _native_supported(native_provider, request):
        return _consult_native(native_provider, request, repository, brief)

    try:
        argv = build_codex_argv(codex_executable)
    except (TypeError, ValueError) as error:
        return _failure("invalid_command", brief, str(error))

    try:
        completed = runner(
            argv,
            cwd=str(repository),
            input=brief,
            text=True,
            capture_output=True,
            check=False,
            shell=False,
        )
    except FileNotFoundError:
        return _failure("unavailable", brief, f"codex executable not found: {argv[0]}")
    except subprocess.TimeoutExpired:
        return _failure("timeout", brief, "codex consultation timed out")
    except OSError as error:
        return _failure("unavailable", brief, f"could not start codex: {error}")

    stdout = completed.stdout if isinstance(completed.stdout, str) else ""
    stderr = completed.stderr if isinstance(completed.stderr, str) else ""
    if completed.returncode != 0:
        error = stderr.strip() or f"codex exited with status {completed.returncode}"
        return _failure(
            "failed",
            brief,
            error,
            exit_code=completed.returncode,
            raw_output=stdout,
        )
    if not stdout.strip():
        return _failure("empty", brief, "codex returned no advice")

    try:
        advice = _advice(json.loads(stdout))
    except json.JSONDecodeError:
        return _failure(
            "invalid_advice",
            brief,
            "codex advice was not valid JSON",
            raw_output=stdout,
        )
    except ValueError as error:
        return _failure("invalid_advice", brief, str(error), raw_output=stdout)
    return ConsultationResult(status="ok", recoverable=True, advice=advice, brief=brief)


def _request_from_payload(payload: Any) -> ConsultationRequest:
    if not isinstance(payload, dict):
        raise ValueError("request must be a JSON object")
    return ConsultationRequest(
        goal=payload.get("goal"),
        current_hypothesis=payload.get("current_hypothesis"),
        attempted_approaches=payload.get("attempted_approaches", ()),
        failures=payload.get("failures", ()),
        constraints=payload.get("constraints", ()),
        question=payload.get("question"),
        parent_assumptions=payload.get("parent_assumptions", ()),
        model=payload.get("model"),
        reasoning_effort=payload.get("reasoning_effort"),
        task_context=payload.get("task_context", ""),
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".", help="active repository to consult")
    args = parser.parse_args(argv)

    try:
        request = _request_from_payload(json.load(sys.stdin))
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        result = _failure("invalid_request", "", str(error))
    else:
        result = consult(request, repo=args.repo)

    json.dump(result.to_dict(), sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
