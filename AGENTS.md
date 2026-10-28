# AGENTS.md

Facts for any coding agent (Claude Code, codex, others) working on this repository. Human contributors: see CONTRIBUTING.md.

## What this is
A Claude Code plugin with two skills, `thinktank` and `factcheck` (`skills/*/SKILL.md`). The skills are instructions the orchestrating Claude session follows; the scripts in `scripts/` do the deterministic parts. A sentence in a SKILL.md that no longer matches a script is a bug.

## Layout
- `.claude-plugin/plugin.json` (plugin manifest, `version`), `.claude-plugin/marketplace.json` (this repo is its own marketplace).
- `scripts/heads.json` head registry; `scripts/heads.py` registry loader and validation, channel detection, OpenRouter and OpenCode channels, `run_in_group` (process-group timeouts), `ensure_agy_project` (restricted agy projects).
- `scripts/thinktank.sh` ThinkTank runner (bash, must run on macOS /bin/bash 3.2); `scripts/ledger.py`.
- `scripts/factcheck.py` FactCheck commands (setup, init, extract, prep, run, collect, packet, rehearse, pro).
- `tests/` pytest suite, offline: fake CLIs on PATH, a local fake OpenRouter server, temp HOME. `tests/test_runner.sh` needs the real codex and is not in CI.

## Rules
- Python standard library only; no new dependencies.
- Heads get tools only through their channel's permission mechanism (agy projects, OPENCODE_PERMISSION, codex read-only sandbox), never only through prompt wording.
- Never write API keys, personal paths or provider responses beyond id/model/usage to logs.
- Run `uv run --with pytest pytest -q tests` before committing; CI runs it on Linux and macOS.
- Behavior change: update README.md and the affected SKILL.md in the same commit.
- Release: bump `version` in plugin.json and add a CHANGELOG.md entry; installed copies update only on a version change. `claude plugin validate .` must pass.
