# Codex advisor

A Codex skill for one focused, read-only second opinion when coding work is
stuck or uncertain.

The main session chooses when to ask. The advisor reads the active repository
and returns evidence, risks or alternatives, next steps, and uncertainty. It
does not edit files, commit changes, or apply its own advice.

## Install

Copy this directory to:

```text
${CODEX_HOME:-$HOME/.codex}/skills/advisor
```

Keep `SKILL.md` and `advisor.py` together. Codex discovers the skill from the
front matter in `SKILL.md`.

Users normally interact with the skill through Codex:

```text
$advisor
$advisor set astra low
```

Codex runs the Python helper for you.

The first command asks one question. The second saves the user-level model
and effort preference without asking a question.

## Preferences

The default is `astra` at `low` effort. `astra` is the friendly name for the
current Codex model `gpt-6-astra`.

The setting lives in `advisor.toml` beside the installed `advisor.py`. That
file is shared across repositories for this skill. It is not
`<active-repository>/.codex/advisor.toml`, and it does not change Codex's main
`config.toml`.

The helper also accepts the exact setting command used by the skill:

```text
python "<skill directory>/advisor.py" --set-preference "set astra low"
```

A request can provide `model` and `reasoning_effort` for a one-off override.
Those values take precedence for that consultation and are not saved.

## Consultation paths

When the host provides a native child advisor, use it only if it is available,
read-only, inherits the task context, and supports the selected model and
effort. Otherwise the helper runs one CLI fallback:

```text
python "<skill directory>/advisor.py" --repo <active-repository> < request.json
```

The fallback invokes one ephemeral `codex exec` with `--sandbox read-only` and
`--ask-for-approval never`. It sends the focused request, including the
current task context, on stdin. The active repository is its working
directory.

If a native child fails after selection, the helper returns a recoverable
failure rather than silently starting a second consultation. The same rule
applies to empty or invalid advice. The main session verifies advice before
acting and summarizes the question, advice, decision, and uncertainty to the
user.

## Request and result

The request must include a goal, current hypothesis, and precise question. It
can also include attempted approaches, failures, constraints,
`parent_assumptions`, `task_context`, `model`, and `reasoning_effort`.

A successful result contains:

```json
{
  "status": "ok",
  "advice": {
    "evidence": [],
    "risks_or_alternatives": [],
    "next_steps": [],
    "uncertainty": ""
  }
}
```

The advisor reads only what it needs. It does not create a transcript,
persistent report, or repository change.

## Check

```text
python -m unittest discover -s tests -v
```
