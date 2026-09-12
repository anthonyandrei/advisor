# Codex advisor

A passive Codex skill for one-shot, read-only consultations during difficult coding tasks.

## Run

Send a JSON request on stdin from the repository being consulted:

```text
python advisor.py --repo . < request.json
```

The request uses `goal`, `current_hypothesis`, `attempted_approaches`, `failures`, `constraints`, `question`, and optional `parent_assumptions`. The result is JSON. A successful result contains `advice.evidence`, `advice.risks_or_alternatives`, `advice.next_steps`, and `advice.uncertainty`.

The consultation is ephemeral and explicitly read-only. Command failures are returned as recoverable result statuses.

## Check

```text
python -m unittest discover -s tests -v
```
