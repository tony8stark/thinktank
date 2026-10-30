# Changelog

## 1.4.1 (2026-10-30)

- The update notice now works in the Claude desktop app. It was switched off by `DISABLE_AUTOUPDATER=1`, which the app sets on every session and which only stops background updates. That variable no longer turns the notice off; `THINKTANK_NO_UPDATE_CHECK`, `DISABLE_UPDATES` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` do, with any non-empty value.

## 1.4.0 (2026-10-30)

- ThinkTank and FactCheck end their report with one line when a newer plugin version is out. The plugin fetches its own `plugin.json` from GitHub at most once a day, sends nothing about you or your runs, gives up after 3 seconds and never stops a run. `THINKTANK_NO_UPDATE_CHECK=1` turns it off; `DISABLE_UPDATES=1`, `DISABLE_AUTOUPDATER=1` and `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1` do too.

## 1.3.1 (2026-10-29)

- A user head whose channel this version does not know is skipped with a warning instead of failing the whole registry. Before, one head written for a newer version (for example a `command` head read by an older copy) left the runner with no heads at all. A broken entry for a known channel still fails loudly.

## 1.3.0 (2026-10-29)

- FactCheck's Opus heads run as the plugin's own `thinktank:web-checker` agent, which has only WebSearch, WebFetch, Read and Write, instead of `general-purpose` (which can run shell commands) held back only by the prompt. Measured on a 19-claim brief against `general-purpose` Opus the same day: 15 of 19 verdicts matched the final ones in both, one unverifiable claim in both, 26 of 26 quotes verified on the page versus 21 of 21.

- OpenRouter and local heads take a `params` object merged into each request (OpenRouter provider routing, a web search engine, temperature; Ollama `options`). Keys the runner owns (model, prompt, streaming, context window, answer limit) are refused at registry load.

## 1.2.1 (2026-10-29)

- Ledgers and the FactCheck agy folder follow `CLAUDE_CONFIG_DIR` when Claude Code runs with a separate config directory (default unchanged: `~/.claude/thinktank/`; `THINKTANK_LEDGER` and `FACTCHECK_LEDGER` still win).
- Plugin icon and a marketplace description.

## 1.2.0 (2026-10-29)

- Local models: a `local` channel for Ollama (native API, context window set per request) and any OpenAI-compatible server (LM Studio and LM Studio Bionic, llama.cpp, vLLM, Jan). A head fails instead of answering from a silently truncated brief, requests to one server queue (timeouts start at admission), http stays on loopback unless a LAN IP is allowed per head. ThinkTank only; never added to a panel automatically.
- Command heads: any program you trust can be a head (protocol 1: brief on stdin, answer in `--out`, exit 3 = unavailable), from your own registry file only. `replaces` plus `status` let the skills swap it in for Agent-tool Opus heads while it is usable (`heads.py substitutes`).
- FactCheck passes the whole registry entry to the channel, so per-head settings reach it.
- agy: when agy prints its answer and then does not exit, the runners stop waiting after a grace period instead of discarding the answer at the timeout (seen once on 2026-10-29: answer after 161 s, process alive until 900 s).

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
