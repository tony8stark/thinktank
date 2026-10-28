# Contributing

Pull requests are welcome: bug fixes, new channels, better panels, docs, tests.

## Before you start

- For a bigger change (a new channel, a change to how verdicts or quotes are judged), open an issue first so we can agree on the shape.
- Small fixes can go straight to a pull request.

## Setup

```bash
git clone https://github.com/<you>/thinktank && cd thinktank
uv run --with pytest pytest -q tests     # or: pip install pytest && pytest -q tests
```

The test suite needs no network and no API keys: OpenRouter is replaced by a local server, and codex, agy and opencode by fake scripts on `PATH`. `bash tests/test_runner.sh` is the one exception: it calls the real codex CLI and is not part of CI.

To try your copy inside Claude Code, run `claude --plugin-dir /path/to/your/clone` (or add your fork as a marketplace with `/plugin marketplace add <you>/thinktank`).

## What a good pull request has

- Tests for the change, in the same style as `tests/` (no network, fake CLIs). CI runs them on Linux and macOS.
- Docs that match: if behavior changes, update `README.md` and the affected `skills/*/SKILL.md` in the same pull request. The skills are instructions Claude follows, so a stale sentence there is a bug.
- Scripts stay dependency-free: Python standard library, and bash that also runs on macOS's `/bin/bash` 3.2.
- No secrets, personal paths or real API keys in code, tests or logs.

## Adding a channel or a head

Heads live in `scripts/heads.json`; a new head on an existing channel is a one-line change there. A new channel needs: detection in `heads.py available`, a call function in `heads.py` that returns `(status, prompt_tokens, completion_tokens, reasoning_tokens, cost)`, wiring in `thinktank.sh` and `factcheck.py run_head`, tests, and a row in the README table. Anything that gives a head tools (shell, files, web) must be restricted by the channel's own permission mechanism, not only by the prompt.

## Releases

Maintainers bump `version` in `.claude-plugin/plugin.json` and add a `CHANGELOG.md` entry for every change that users should receive: installed copies update only when the version changes.

## License

By contributing you agree that your contribution is licensed under the MIT License of this repository.
