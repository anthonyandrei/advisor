# Codex advisor

A passive Codex skill for one-shot, read-only consultations during difficult coding tasks.

## Run

Send a JSON request on stdin from the repository being consulted:

```text
python advisor.py --repo . < request.json
```

The request uses `goal`, `current_hypothesis`, `attempted_approaches`, `failures`, `constraints`, `question`, and optional `parent_assumptions`. The result is JSON. A successful result contains `advice.evidence`, `advice.risks_or_alternatives`, `advice.next_steps`, and `advice.uncertainty`.

The consultation is ephemeral and explicitly read-only. Command failures are returned as recoverable result statuses.

## Preferences

Advisor defaults live only in `.codex/advisor.toml` under the repository passed to `--repo`:

```toml
model = "o3"
reasoning_effort = "high"
```

Advisor reads that file once when each consultation starts. It never writes the global Codex configuration. Change one default with the narrow natural-language command:

```text
python advisor.py --repo . --set-preference "set model to o3"
python advisor.py --repo . --set-preference "set reasoning effort to high"
```

The accepted form is `set`, `change`, or `update` followed by `model` or `reasoning effort`, `to`, and one value. A request may also include `model` and `reasoning_effort` fields for one-off overrides. Those values take precedence for that consultation and are not saved.

The supported models are `gpt-5`, `gpt-5-mini`, `gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-max`, `o1`, `o3`, and `o4-mini`. Unsupported models or incompatible reasoning efforts return a clear error before Codex starts.

## Check

```text
python -m unittest discover -s tests -v
```
