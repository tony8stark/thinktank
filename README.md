# thinktank (Claude Code plugin)

Two skills:

- `/thinktank:thinktank` fans one self-contained brief to several cheap LLM heads in parallel (codex, Gemini via agy, Opus via the Agent tool), merges findings by agreement, verifies single-head findings and reports tokens. For code reviews, audits, brainstorms, architecture questions and conclusions from data.
- `/thinktank:factcheck` checks a text claim by claim with web-enabled heads, verifies every cited quote against the live page, and writes a corrected text plus a dossier for a final GPT Pro pass.

## Install

```
/plugin marketplace add tony8stark/thinktank
/plugin install thinktank@thinktank
```

Optional reminder hook: copy `hooks/thinktank-check.sh` somewhere and register it as a `UserPromptSubmit` hook in `~/.claude/settings.json`.

## Requirements

- [codex CLI](https://github.com/openai/codex), signed in (the default heads are `gpt-6.1-sol` at low effort).
- Optional: `agy` (Antigravity CLI) for Gemini heads. Without it, drop `gemini` from the panel.
- `python3`; `pdftotext` for PDF quotes; Google Chrome for headless fetches of JS pages (factcheck).
- Model ids are hardcoded in `scripts/thinktank.sh` and `scripts/factcheck.py`: edit them if your accounts offer different models.

## Layout

- `skills/thinktank/` SKILL.md, brief template
- `skills/factcheck/` SKILL.md, checker and extraction briefs
- `scripts/thinktank.sh` parallel codex/agy runner (writes `out/<head>.md` + `.meta.json`), `scripts/ledger.py`
- `scripts/factcheck.py` (setup, init, extract, prep, run, collect, packet, rehearse, pro; see its docstring). `factcheck.py setup` creates a restricted agy project `factcheck` (read_url + search_web only).
- `hooks/thinktank-check.sh` UserPromptSubmit reminder.

Runs go to `~/.local/share/thinktank/runs/`, ledgers to `~/.claude/thinktank/` (override with `THINKTANK_LEDGER`, `FACTCHECK_LEDGER`).
