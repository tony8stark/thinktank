# Changelog

## 1.1.0 (2026-10-28)

- MIT license, contribution guide, this changelog.
- Works with only Claude Code installed: the skills detect available channels and size panels from them.
- Head registry `scripts/heads.json` with user overrides in `~/.config/thinktank/heads.json`, and two new channels: OpenRouter (API key) and OpenCode (CLI).
- Safer runs: agy and OpenCode heads run in isolated directories with restricted permissions, killed runs leave no head processes behind, cited URLs are limited to public http(s) pages, stale answers are never reused.
- FactCheck: stricter quote verification for money claims, honest dossier wording, Linux support for headless Chrome and the clipboard.
- README: How it works diagrams, update instructions.

## 1.0.0 (2026-10-23)

- First public version: ThinkTank and FactCheck skills.
