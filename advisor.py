"""Run one read-only, ephemeral Codex consultation for the active repository."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tomllib
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
class AdvisorPreferences:
    model: str | None = None
    reasoning_effort: str | None = None


PREFERENCES_PATH = Path(__file__).resolve().with_name("advisor.toml")
DEFAULT_PREFERENCES = AdvisorPreferences("astra", "low")
MODEL_ALIASES = {"astra": "gpt-6-astra"}
MODEL_REASONING_EFFORTS = {
    "gpt-6-astra": frozenset(("low", "medium", "high", "xhigh")),
    "gpt-5": frozenset(("minimal", "low", "medium", "high")),
    "gpt-5-mini": frozenset(("minimal", "low", "medium", "high")),
    "gpt-5-codex": frozenset(("low", "medium", "high", "xhigh")),
    "gpt-5.1-codex": frozenset(("low", "medium", "high", "xhigh")),
    "gpt-5.1-codex-max": frozenset(("low", "medium", "high", "xhigh")),
    "o1": frozenset(("low", "medium", "high")),
    "o3": frozenset(("low", "medium", "high")),
    "o4-mini": frozenset(("low", "medium", "high")),
}
REASONING_EFFORTS = frozenset(
    effort for efforts in MODEL_REASONING_EFFORTS.values() for effort in efforts
)


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


def _repository(repo: str | Path | None) -> Path:
    repository = Path.cwd() if repo is None else Path(repo)
    repository = repository.expanduser().resolve()
    if not repository.is_dir():
        raise ValueError(f"repository does not exist: {repository}")
    return repository


def _validate_settings(
    model: Any = None,
    reasoning_effort: Any = None,
) -> AdvisorPreferences:
    selected_model = None if model is None else _required_text(model, "model")
    canonical_model = MODEL_ALIASES.get(selected_model, selected_model)
    selected_effort = (
        None
        if reasoning_effort is None
        else _required_text(reasoning_effort, "reasoning_effort").lower()
    )
    if canonical_model is not None and canonical_model not in MODEL_REASONING_EFFORTS:
        supported = ", ".join((*MODEL_ALIASES, *MODEL_REASONING_EFFORTS))
        raise ValueError(f"unsupported model {selected_model!r}; choose one of: {supported}")
    if selected_effort is not None and selected_effort not in REASONING_EFFORTS:
        supported = ", ".join(sorted(REASONING_EFFORTS))
        raise ValueError(
            f"unsupported reasoning_effort {selected_effort!r}; choose one of: {supported}"
        )
    if (
        canonical_model is not None
        and selected_effort is not None
        and selected_effort not in MODEL_REASONING_EFFORTS[canonical_model]
    ):
        raise ValueError(
            f"reasoning_effort {selected_effort!r} is not supported by model {selected_model!r}"
        )
    return AdvisorPreferences(selected_model, selected_effort)


def load_preferences(repo: str | Path | None = None) -> AdvisorPreferences:
    """Read user-level advisor defaults beside the installed helper."""
    path = PREFERENCES_PATH
    if not path.exists():
        return DEFAULT_PREFERENCES
    if not path.is_file():
        raise ValueError(f"advisor preferences path is not a file: {path}")
    try:
        payload = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as error:
        raise ValueError(f"invalid advisor preferences in {path}: {error}") from error
    except (OSError, UnicodeError) as error:
        raise ValueError(f"could not read advisor preferences {path}: {error}") from error
    if not isinstance(payload, dict):
        raise ValueError(f"advisor preferences must be a TOML table: {path}")
    unknown = sorted(set(payload) - {"model", "reasoning_effort"})
    if unknown:
        raise ValueError(f"unsupported advisor preference key(s) in {path}: {', '.join(unknown)}")
    try:
        return _validate_settings(
            payload.get("model", DEFAULT_PREFERENCES.model),
            payload.get("reasoning_effort", DEFAULT_PREFERENCES.reasoning_effort),
        )
    except ValueError as error:
        raise ValueError(f"invalid advisor preferences in {path}: {error}") from error


_PREFERENCE_CHANGE = re.compile(
    r"^\s*(?:set|change|update)\s+(model|reasoning\s+effort)\s+to\s+([A-Za-z0-9._-]+)\s*$",
    re.IGNORECASE,
)
_COMPACT_PREFERENCE_CHANGE = re.compile(
    r"^\s*(?:set|change|update)\s+([A-Za-z0-9._-]+)\s+([A-Za-z0-9._-]+)\s*$",
    re.IGNORECASE,
)


def update_preferences(
    change: str,
    repo: str | Path | None = None,
) -> AdvisorPreferences:
    """Apply one exact change to user-level advisor defaults."""
    if not isinstance(change, str):
        raise ValueError("preference change must be a string")
    match = _PREFERENCE_CHANGE.fullmatch(change)
    compact_match = _COMPACT_PREFERENCE_CHANGE.fullmatch(change)
    if match is None and compact_match is None:
        raise ValueError(
            "preference change must look like 'set astra low', 'set model to o3', "
            "or 'set reasoning effort to high'"
        )

    current = load_preferences(repo)
    values = {
        "model": current.model,
        "reasoning_effort": current.reasoning_effort,
    }
    if compact_match is not None:
        values["model"] = compact_match.group(1)
        values["reasoning_effort"] = compact_match.group(2)
    else:
        field = "reasoning_effort" if "effort" in match.group(1) else "model"
        values[field] = match.group(2)
    updated = _validate_settings(values["model"], values["reasoning_effort"])

    path = PREFERENCES_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if updated.model is not None:
        lines.append(f"model = {json.dumps(updated.model)}")
    if updated.reasoning_effort is not None:
        lines.append(f"reasoning_effort = {json.dumps(updated.reasoning_effort)}")
    try:
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError as error:
        raise ValueError(f"could not write advisor preferences {path}: {error}") from error
    return updated


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
    task_context = _context_text(request.task_context)

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
    if task_context:
        lines.extend(("", "## Current task context", task_context))
    return "\n".join(lines)


def build_codex_argv(
    codex_executable: str | Path = "codex",
    *,
    model: str | None = None,
    reasoning_effort: str | None = None,
) -> list[str]:
    """Build the fixed safety boundary for one consultation."""
    executable = str(codex_executable)
    if not executable:
        raise ValueError("codex_executable must not be empty")
    settings = _validate_settings(model, reasoning_effort)
    argv = [
        executable,
        "--sandbox",
        "read-only",
        "--ask-for-approval",
        "never",
    ]
    if settings.model is not None:
        argv.extend(("--model", MODEL_ALIASES.get(settings.model, settings.model)))
    if settings.reasoning_effort is not None:
        argv.extend(
            ("--config", f"model_reasoning_effort={json.dumps(settings.reasoning_effort)}")
        )
    argv.extend(("exec", "--ephemeral", "--skip-git-repo-check", "-"))
    return argv


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
    requested_model = MODEL_ALIASES.get(request.model, request.model)
    capability_model = MODEL_ALIASES.get(capabilities.model, capabilities.model)
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
        and (requested_model is None or capability_model == requested_model)
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
        model=MODEL_ALIASES.get(request.model, request.model),
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
    runner: Runner | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    native_provider: NativeProvider | None = None,
) -> ConsultationResult:
    """Consult a safe native provider or the fixed read-only Codex fallback."""
    try:
        request = replace(
            request,
            model=_optional_text(request.model, "model"),
            reasoning_effort=_optional_text(
                request.reasoning_effort, "reasoning_effort"
            ),
            task_context=_context_text(request.task_context),
        )
        brief = build_brief(request)
    except (TypeError, ValueError) as error:
        return _failure("invalid_request", "", str(error))

    try:
        repository = _repository(repo)
    except (TypeError, ValueError, OSError) as error:
        return _failure("invalid_repository", brief, str(error))

    try:
        settings = load_preferences(repository)
    except (TypeError, ValueError, OSError) as error:
        return _failure("invalid_preferences", brief, str(error))

    requested_model = model if model is not None else request.model
    requested_effort = (
        reasoning_effort if reasoning_effort is not None else request.reasoning_effort
    )
    try:
        settings = _validate_settings(
            settings.model if requested_model is None else requested_model,
            settings.reasoning_effort if requested_effort is None else requested_effort,
        )
    except (TypeError, ValueError) as error:
        return _failure("invalid_preferences", brief, str(error))

    request = replace(
        request,
        model=settings.model,
        reasoning_effort=settings.reasoning_effort,
    )
    if _native_supported(native_provider, request):
        return _consult_native(native_provider, request, repository, brief)

    try:
        argv = build_codex_argv(
            codex_executable,
            model=settings.model,
            reasoning_effort=settings.reasoning_effort,
        )
    except (TypeError, ValueError) as error:
        return _failure("invalid_command", brief, str(error))

    if runner is None:
        runner = subprocess.run
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
    parser.add_argument(
        "--set-preference",
        metavar="CHANGE",
        help="update user-level advisor defaults, for example: 'set astra low'",
    )
    args = parser.parse_args(argv)

    if args.set_preference is not None:
        try:
            preferences = update_preferences(args.set_preference, repo=args.repo)
        except (TypeError, ValueError, OSError) as error:
            result = {"status": "invalid_preferences", "error": str(error)}
        else:
            result = {"status": "ok", "preferences": asdict(preferences)}
    else:
        try:
            request = _request_from_payload(json.load(sys.stdin))
        except (json.JSONDecodeError, TypeError, ValueError) as error:
            result = _failure("invalid_request", "", str(error))
        else:
            result = consult(request, repo=args.repo)

    json.dump(result.to_dict() if isinstance(result, ConsultationResult) else result, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
