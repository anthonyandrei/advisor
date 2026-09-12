---
name: advisor
description: Get a one-shot, read-only Codex consultation for a difficult coding question.
disable-model-invocation: true
---

# Advisor

Use this skill only when the user explicitly asks for an advisor consultation. It is passive and does not run automatically on each turn.

## Install and run

Copy this directory to `${CODEX_HOME:-$HOME/.codex}/skills/advisor`. Keep `SKILL.md` and `advisor.py` together. A fresh Codex installation recognizes the directory from the `SKILL.md` front matter.

Run the helper by its installed path, so the caller's current directory does not matter:

```text
python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> < request.json
```

On PowerShell, set the path once and use the same command:

```powershell
$skillDir = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME "skills\advisor" } else { Join-Path $HOME ".codex\skills\advisor" }
python (Join-Path $skillDir "advisor.py") --repo <active-repository> < request.json
```

Build a focused request with the user's goal, current hypothesis, attempted approaches, observed failures, constraints, and precise question. Put additional parent assumptions in `parent_assumptions`. Add `task_context` when a native host provider can pass the parent's task context safely. The helper labels reported facts and unverified assumptions separately.

Advisor defaults are project-local in `<active-repository>/.codex/advisor.toml`:

```toml
model = "o3"
reasoning_effort = "high"
```

Update one default with the installed helper and an exact, narrow command such as `python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> --set-preference "set model to o3"`. The command writes only that advisor file. It does not write the global Codex configuration.

For a single consultation, add `model` and/or `reasoning_effort` to the JSON request. They override the saved defaults for that call and are never persisted. The helper validates the model and its supported reasoning efforts before invoking Codex.

The helper prefers a host-supplied native provider only when it is available, read-only, inherits the task context, and matches the requested model and reasoning effort. The CLI entry point uses the fallback path. It runs one `codex exec --ephemeral` process with `--sandbox read-only` and `--ask-for-approval never`, passes the focused brief on stdin, and uses the active repository as `cwd`. A native runtime error is returned as a recoverable result instead of starting a second consultation.

Read the JSON result. On `status: "ok"`, use only its `advice` object:

```json
{
  "evidence": [],
  "risks_or_alternatives": [],
  "next_steps": [],
  "uncertainty": ""
}
```

The parent agent must verify the advice against repository evidence before acting. For any other status, keep the parent task moving with the returned `error`, `raw_output`, and uncertainty. Do not retry automatically, apply advice automatically, create a transcript, or turn the consultation into a write operation. This skill does not add cross-vendor adapters or global Codex configuration changes.
