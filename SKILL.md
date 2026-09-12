---
name: advisor
description: Get a one-shot, read-only Codex consultation for a difficult coding question.
disable-model-invocation: true
---

# Advisor

Use this skill only when the user explicitly asks for an advisor consultation. It is passive and does not run automatically on each turn.

Build a request with the user's goal, current hypothesis, attempted approaches, observed failures, constraints, and precise question. Put any additional parent assumptions in `parent_assumptions`. The helper labels reported facts and unverified assumptions separately.

Pass that request as JSON on stdin to `advisor.py` from the active repository:

```text
python advisor.py --repo <active-repository> < request.json
```

Advisor defaults are project-local in `<active-repository>/.codex/advisor.toml`:

```toml
model = "o3"
reasoning_effort = "high"
```

Update one default with an exact, narrow command such as `python advisor.py --repo <active-repository> --set-preference "set model to o3"` or `"set reasoning effort to high"`. The command writes only that advisor file. It does not write the global Codex configuration.

For a single consultation, add `model` and/or `reasoning_effort` to the JSON request. They override the saved defaults for that call and are never persisted. The helper validates the model and its supported reasoning efforts before invoking Codex.

The helper runs one `codex exec --ephemeral` process with `--sandbox read-only` and `--ask-for-approval never`. It passes the focused brief on stdin, uses the active repository as `cwd`, and stores no transcript or patch.

Read the JSON result. On `status: "ok"`, use only its `advice` object:

```json
{
  "evidence": [],
  "risks_or_alternatives": [],
  "next_steps": [],
  "uncertainty": ""
}
```

For any other status, keep the parent task moving with the returned `error`, `raw_output`, and uncertainty. Do not retry automatically or turn the consultation into a write operation.
