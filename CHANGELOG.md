# Changelog

## 1.1.1 (2026-10-29)

- Security: OpenCode 2.x ignores `OPENCODE_PERMISSION`, so on 2.x an OpenCode head ran with OpenCode's default tools (file writes and shell commands in its scratch directory) instead of none. OpenCode heads now detect the version: 1.x keeps `OPENCODE_PERMISSION`; 2.x gets deny-all permission rules (plus `webfetch` and `websearch` for factcheck) through `OPENCODE_CONFIG_CONTENT` on a private `--standalone` server, because the shared background service does not read per-run config. Any other version is refused. Verified live on OpenCode 2.1.4. Update if you added an OpenCode head.

## 1.1.0 (2026-10-28)

- MIT license, contribution guide, this changelog.
- Works with only Claude Code installed: the skills detect available channels and size panels from them.
- Head registry `scripts/heads.json` with user overrides in `~/.config/thinktank/heads.json`, and two new channels: OpenRouter (API key) and OpenCode (CLI).
- Safer runs: agy and OpenCode heads run in isolated directories with restricted permissions, killed runs leave no head processes behind, cited URLs are limited to public http(s) pages, stale answers are never reused.
- FactCheck: stricter quote verification for money claims, honest dossier wording, Linux support for headless Chrome and the clipboard.
- README: How it works diagrams, update instructions.

## 1.0.0 (2026-10-23)

- First public version: ThinkTank and FactCheck skills.
