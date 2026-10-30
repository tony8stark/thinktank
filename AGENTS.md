# AGENTS.md

Facts for any coding agent (Claude Code, codex, others) working on this repository. Human contributors: see CONTRIBUTING.md.

## What this is
A Claude Code plugin with two skills, `thinktank` and `factcheck` (`skills/*/SKILL.md`), and one agent, `web-checker` (`agents/web-checker.md`, the FactCheck Opus head, limited to WebSearch, WebFetch, Read and Write). The skills are instructions the orchestrating Claude session follows; the scripts in `scripts/` do the deterministic parts. A sentence in a SKILL.md that no longer matches a script is a bug.

## Layout
- `.claude-plugin/plugin.json` (plugin manifest, `version`), `.claude-plugin/marketplace.json` (this repo is its own marketplace).
- `scripts/heads.json` head registry; `scripts/heads.py` registry loader and validation, channel detection, OpenRouter, OpenCode, local (Ollama native or OpenAI-compatible, endpoint lock, truncation check) and command channels, `run_in_group` (process-group timeouts), `run_agy` (stops waiting once agy printed its answer), `ensure_agy_project` (restricted agy projects), `models`, `check-models`, `set`, `panel`, `resolved-model` (choosing models), `update_notice` (`update-check`: the one line about a newer version, from GitHub at most once a day, cached in `~/.cache/thinktank/update-check.json`; tests point `THINKTANK_UPDATE_URL` at a local server).
- `scripts/thinktank.sh` ThinkTank runner (bash, must run on macOS /bin/bash 3.2); `scripts/ledger.py`.
- `scripts/factcheck.py` FactCheck commands (setup, init, extract, prep, run, collect, packet, rehearse, pro).
- `tests/` pytest suite, offline: fake CLIs on PATH, a local fake OpenRouter server, temp HOME. `tests/test_runner.sh` needs the real codex and is not in CI.
- `.github/workflows/tests.yml` runs the suite; `.github/workflows/release.yml` runs `.github/release.sh` on every push to `main`; `.github/workflows/models.yml` runs `.github/models-check.sh` weekly and keeps one issue open while a shipped OpenRouter id is gone or has an expiry date (GitHub pauses a schedule after 60 days without repo activity).

## Rules
- Python standard library only; no new dependencies.
- Heads get tools only through their channel's permission mechanism (agy projects, OpenCode 1.x OPENCODE_PERMISSION or 2.x permission rules in OPENCODE_CONFIG_CONTENT with --standalone, codex read-only sandbox), never only through prompt wording.
- Never write API keys, personal paths or provider responses beyond id/model/usage to logs.
- Local heads never get `web: true`; command heads come only from a user registry file the user owns, and `heads.py set` refuses them. Neither joins a panel unless the user names it or pins it with `"panel"`.
- Provider error bodies reach logs only as their error message (`provider_error`): they can carry account ids.
- `update-check` is the only request the plugin makes on its own: it sends nothing about the user or the run, runs under a 3-second deadline, turns any failure into no output, and honours `THINKTANK_NO_UPDATE_CHECK`, `DISABLE_UPDATES` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, but not `DISABLE_AUTOUPDATER`, which the Claude desktop app sets on every session. Check it from a real session too: the test fixture cannot show what the app puts in the environment.
- Run `uv run --with pytest pytest -q tests` before committing; CI runs it on Linux and macOS.
- Behavior change: update README.md and the affected SKILL.md in the same commit.
- Release: bump `version` in plugin.json and add a CHANGELOG.md entry headed `## <version> (<date>)`; installed copies update only on a version change. `claude plugin validate .` must pass. The push to `main` does the rest: `release.sh` tags the pushed commit and publishes a GitHub Release with that entry, once per version; a version without an entry fails the run. Never create tags or releases by hand.
