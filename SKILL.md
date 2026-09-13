---
name: advisor
description: Invoke for one read-only second opinion on coding work when a specific uncertainty remains: an unclear failure, repeated failed fixes, an unfamiliar code path, competing designs, a risky change, or a high-risk final check. Also invoke when the user asks for `$advisor`.
metadata:
  targets: [codex]
---

# Advisor

Use this skill for one focused second opinion during coding work. The main
session decides when to ask, verifies the result, and keeps control of the
task.

## When to invoke

Invoke it after repeated failed fixes, when the code path or failure is
unclear, when designs compete, when a change is risky, or when a final check
would reduce uncertainty. The user can also invoke it directly with
`$advisor`.

Do not consult on every turn. Ask one question at a time. Do not retry or
escalate automatically. A failed or weak consultation is recoverable, so
continue the main task and report the limitation.

## User setting

`$advisor set <model> <effort>` changes the user-level advisor preference and
does not start a consultation. For example:

```text
$advisor set astra low
```

The default is `astra` at `low` effort. `astra` is the friendly name for the
current Codex model `gpt-6-astra`.

The preference file is `advisor.toml` beside the installed `advisor.py`. It is
shared across repositories for this skill. It is not a project `.codex` file
and it does not change Codex's main `config.toml`.

## Consult

Build a short request containing the goal, current hypothesis, attempted
approaches, observed failures, constraints, and precise question. Include
`parent_assumptions` for claims the advisor must treat as unverified. Include
the current `task_context`; the native and CLI paths both receive it.

The bundled helper accepts the request as JSON on stdin. Run the `advisor.py`
beside this file against the active repository. It reads the saved preference
once, then makes one consultation:

```text
python "<skill directory>/advisor.py" --repo <active-repository> < request.json
```

Prefer a native child advisor only when it is available, read-only, inherits
the task context, and supports the selected model and effort. Otherwise use
the helper's CLI fallback. The fallback runs one ephemeral `codex exec` with
`--sandbox read-only` and `--ask-for-approval never`. It passes the focused
brief on stdin and never edits the repository.

Use only the returned `advice` object after checking its evidence against the
repository. The advisor does not speak to the user or apply its own advice.
The main session model writes the short user-facing report. It should say
what it asked, what the advisor advised, what it decided, and any important
uncertainty. Do not spend another consultation on that summary.

The advisor may return `evidence`, `risks_or_alternatives`, `next_steps`, and
`uncertainty`. For any other status, use the returned error and diagnostics,
then continue the main task. Do not auto-apply advice, create a transcript or
persistent report, or turn the consultation into a write operation.
