# Codex advisor

A passive Codex skill for one-shot, read-only consultations during difficult coding tasks.

## Install

Copy this repository directory to `${CODEX_HOME:-$HOME/.codex}/skills/advisor`. The directory is installable because it contains the skill manifest in `SKILL.md` and the bundled `advisor.py` helper.

Keep the helper beside `SKILL.md`, then invoke it by its installed path. The active repository is an explicit argument, so the command works from any caller directory:

```text
python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> < request.json
```

On Windows PowerShell:

```powershell
$skillDir = if ($env:CODEX_HOME) { Join-Path $env:CODEX_HOME "skills\advisor" } else { Join-Path $HOME ".codex\skills\advisor" }
python (Join-Path $skillDir "advisor.py") --repo <active-repository> < request.json
```

## Run

Send a JSON request on stdin. From an installed skill, use the helper path shown above:

```text
python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> < request.json
```

The request uses `goal`, `current_hypothesis`, `attempted_approaches`, `failures`, `constraints`, `question`, and optional `parent_assumptions`, `task_context`, `model`, and `reasoning_effort`. The result is JSON. A successful result contains `advice.evidence`, `advice.risks_or_alternatives`, `advice.next_steps`, and `advice.uncertainty`.

The consultation is ephemeral and explicitly read-only. Command failures are returned as recoverable result statuses. The parent agent checks the returned evidence, risks, next steps, and uncertainty before making any change.

## Native path and fallback

Hosts that can provide a native child-agent consultation may pass a `NativeProvider` to `consult`. The helper selects it only when the provider is available, inherits the parent's task context, guarantees read-only behavior, and supports the selected model and reasoning effort. The native result uses the same advice contract.

The command-line entry point cannot inject a native provider, so it uses the safe CLI fallback. The fallback invokes one ephemeral `codex exec` with `--sandbox read-only`, `--ask-for-approval never`, and the focused brief on stdin. If native requirements are missing, the helper falls back to that CLI path. If a selected native provider fails while running, the helper returns a recoverable failure rather than silently consulting twice.

## Preferences

Advisor defaults live only in `.codex/advisor.toml` under the repository passed to `--repo`:

```toml
model = "o3"
reasoning_effort = "high"
```

Advisor reads that file once when each consultation starts. It never writes the global Codex configuration. Change one default with the narrow natural-language command:

```text
python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> --set-preference "set model to o3"
python "${CODEX_HOME:-$HOME/.codex}/skills/advisor/advisor.py" --repo <active-repository> --set-preference "set reasoning effort to high"
```

The accepted form is `set`, `change`, or `update` followed by `model` or `reasoning effort`, `to`, and one value. A request may also include `model` and `reasoning_effort` fields for one-off overrides. Those values take precedence for that consultation and are not saved.

The supported models are `gpt-5`, `gpt-5-mini`, `gpt-5-codex`, `gpt-5.1-codex`, `gpt-5.1-codex-max`, `o1`, `o3`, and `o4-mini`. Unsupported models or incompatible reasoning efforts return a clear error before Codex starts. Consultation does not add cross-vendor adapters, automatic per-turn calls, global Codex configuration changes, persistent transcripts, or automatic application of advice.

## Check

```text
python -m unittest discover -s tests -v
```
