---
name: factcheck
description: Fact-check a text claim by claim before publication. Several cheap web-enabled heads (GPT-6.1 Sol low via codex, Opus via the Agent tool) check every claim against primary sources, a script fetches every cited page and verifies each quote, the orchestrator adjudicates by evidence (not by vote), and the result is a corrected text with sources, plus an optional dossier for a final review by a stronger model (for example GPT Pro). Use for travel articles (entry rules, prices, hours, safety), tax/legal/form content, and any text whose claims move people's money or legal standing. Not for opinion, code, translation or single facts you can check yourself in one search. Arguments: tier quick|standard|money, then the file or text.
---

# FactCheck

Why this exists: a text with factual errors costs readers money, filings or a trip. This stage checks every claim cheaply and in parallel against primary sources and returns a corrected text. If the user also runs a final review with a stronger model (for example GPT Pro, which is slow), the dossier from step 6 lets that review converge in one round. That final review is optional.

What the design rests on:
- Repeats of strong heads beat panels of different ones; low effort plus repeats beats one high-effort run, except on whole-document review, where only the top effort level pays.
- Orchestrator verification is the active ingredient. A head's "correct value" is a claim like any other and needs its own quote.
- Gemini heads tend to paraphrase quotes (which then fail verification), so Gemini is not in the default panels. `astra` is an opt-in head name; the defaults use Sol.

Plugin root: the directory two levels above this SKILL.md (`${CLAUDE_PLUGIN_ROOT}` when set). Below, `$FC` stands for `python3 "<root>/scripts/factcheck.py"` with the absolute root written out and quoted (install paths can contain spaces; shell state does not persist between Bash calls).

## 1. When to fire

Fire when the user asks to fact-check, verify claims, or prepare text for a final review, or before you publish (or hand a reviewer model) any text with factual claims about tax, law, forms, deadlines, fees, prices, entry rules, safety, or history the site states as fact. Announce in one line: `FactCheck: <tier>, <n> claims, heads <spec> + <agent heads>`.

Do not fire for opinion or marketing copy, code, translation, or a single fact (just check it).

## 2. Pick the tier

First run `$FC available`: it prints the channels this machine has (`codex`, `agy`, `opencode`, `openrouter`, always `agent`). That is detection, not proof of a sign-in or a valid key: a head that fails on auth is dropped like any failed head. The table assumes `codex`. Without codex but with `openrouter`, use `or-gpt` in place of `sol` (same counts) and, for standard and money, add `or-deepseek:1` as a second family; they run inside `run` with OpenRouter's web search and cost money per call. An `opencode` head works the same way if the user added one with `"web": true`. `prep` refuses a head whose `web` is false. With `agy` but no codex and no OpenRouter: `gemini:2` plus the Agent heads of the tier (run `$FC setup` first). If no runner head is usable, use the Claude-only mode below.

| tier | content | runner `--heads` | Agent heads | extras |
|------|---------|-------------------|-------------|--------|
| quick | history, geography, descriptions, low-stakes travel trivia | `sol:2` | none | none |
| standard | practical travel info (prices, hours, transport, visas and entry, safety), general legal info | `sol:3` | opus x1 | independent extraction |
| money | tax, forms, filing, deadlines, penalties, fees, eligibility, contracts; anything that moves money or legal standing | `sol:4` | opus x2 | independent extraction, rehearsal |

Rules: at most 2 Agent heads (Anthropic capacity; two Opus heads cost no extra wall clock because they run alongside codex). Per-claim checking stays at low effort: it is a single-answer task, and higher effort does not help on those; what helps is repeats and search instructions. `gemini` (agy, Gemini 3.8 Flash high, with search_web + read_url only), `luna` and `astra` exist as heads but are opt-in: add them only when the user asks, or as a second opinion on claims whose sources this machine cannot read. `fable` only when the user asks.

**Claude only** (only `agent` printed, or no usable runner head): `--heads` takes Agent heads only: quick `opus:2`, standard `opus:3`, money `opus:4` (more than two Agent heads is fine here, they are the repeats). Each Agent head checks every claim. Skip `extract` (step 3.2) and `rehearse` (step 6.4), which need codex; extract the claims yourself with extra care for implicit claims and absolutes. `$FC run` has nothing to launch and prints `ok=0 error=0`; skip it. Say "single model family" in the report.

## 3. Prepare the run

```
RUN=~/.local/share/thinktank/runs/$(date +%Y%m%d-%H%M%S)-fc-<slug>; mkdir -p "$RUN/out"
```
1. Save the text verbatim to `$RUN/input.md` and run `$FC init $RUN $RUN/input.md --context "<audience; reader's passport or residence; jurisdiction; tax year or period; site>"`. The context is what stops a head from checking the right fact for the wrong country or year; never leave it empty for standard or money.
2. standard and money, codex only: start `$FC extract $RUN` with `run_in_background` (Sol, no web; short texts use one call, long texts use paragraph chunks targeting 1,200 words with at most six calls in parallel, keeping the original ¶ labels and merging in source order). The timeout scales per chunk (600 s up to 1,000 brief words, then 0.6 s per word, capped at an hour; an oversized paragraph stays intact), split answers are recovered from session messages, and a chunk that still fails aborts extraction with its chunk number and cause.
3. Write `$RUN/claims.json` yourself from `$RUN/source.md`:
   `[{"id":"C1","para":"¶2","stakes":"money|standard|low","scope":"jurisdiction, period, who","span":"exact substring of the text","claim":"one atomic, checkable proposition"}]`
   - One proposition per claim; split compound sentences. Resolve pronouns and implicit scope in `claim`, keep `span` verbatim.
   - Include implicit claims a reader acts on ("pay at the entrance" implies no advance booking) and absolutes (only, always, never, free, no need, visa-free): they are where omissions hide.
   - stakes=money for anything that can cost a reader money, a filing, a fine, a legal right or entry at a border.
4. When `extract` finishes, read `$RUN/extract/sol.json` and add every claim you missed (keep the union; a claim found by one extractor only is cheap to check and is where recall comes from).
5. `$FC prep $RUN --heads "<spec>"`. Fix every `WARN` (span not verbatim). For every `UNCOVERED` sentence either add a claim or decide it holds nothing checkable. Lists over 18 claims are split into balanced parallel chunks of at most 12 for the codex heads (`--chunk N` to override); Agent heads then share the list instead of each taking all of it, so on a long money text the two Opus heads each check half (every claim still gets its four codex checks). For a long standard text use `opus:2` for the same reason: one Opus head needs about 40 s per claim. Re-running `prep` after a run discards the answers of every job whose brief changed. A `gemini` head needs the restricted agy project: run `$FC setup` once before the first one (it creates or repairs the project).

## 4. Run the heads (one message)

In ONE message:
1. Bash with `run_in_background` (the Bash tool stops a foreground command after 10 minutes): `$FC run $RUN`. You are notified when it exits; do not poll. It prints `FACTCHECK DONE ok=N error=M`; re-running retries only failed jobs.
2. The Agent tool once per Agent head printed by `prep`: `subagent_type: "general-purpose"` with `model: "opus"` (the model `prep` prints), prompt:
   `You are fact-check head opus-1. Read <RUN>/briefs/opus-1.md and do exactly what it says, using only WebSearch and WebFetch for checking. Write your full answer to <RUN>/out/opus-1.md; the first line must be "PANEL: opus answering". Reply with the single word DONE.`
   Why not a dedicated low-effort agent: it called far more verdicts unverifiable than `general-purpose` Opus heads; do not create one.

Wait for both (the Agent result arrives as a notification). Then:
`$FC collect $RUN --agent-tokens opus-1=<subagent_tokens>[,opus-2=<n>]` and read `$RUN/matrix.md`.

## 5. Adjudicate (this is the step that makes the result trustworthy)

`collect` marks a claim auto-ok only when every head whose verdict is backed by a verified quote says supported, at least two such heads exist, and a primary or official source is among the quotes (low-stakes claims may rest on secondary sources). A money claim also needs a strong source whose quote matched the page closely; fuzzy or corroborated evidence alone flags it `WEAK_EVIDENCE`. Everything else, plus one random supported money claim (`AUDIT`), is listed under "Claims to adjudicate". For each:

- **Evidence decides, not votes.** A verdict marked `*` in the matrix has no quote found on its page: ignore it. Three heads quoting one page are one source.
- **Two checks per correction.** (a) The text really says this, in this scope. (b) The replacement value is stated in a verified quote from a primary or official source whose jurisdiction, year and category match the claim. `FIX_UNQUOTED:<head>` means (b) is missing: find the quote yourself, or compute it and say so (for example a weekday), or do not use that value.
- **Quotes this machine could not read** (`unchecked`, blocked or JS-only pages) on money claims: open the page yourself with the in-app browser (`get_page_text`) or WebFetch. `corroborated` means two heads independently returned the same quote (6+ words) from the same page for the same claim: acceptable for standard claims, open it yourself for money claims.
- **near the quote** lines show exception words (except, unless, effective, until...) around a matched quote: read them; a quote cut just before its exception is the classic misleading support.
- **Dates:** a newer page wins only within the same authority. A news report that a rule will change is not the rule changing; the official page wins until it changes. Tax figures belong to a tax year; check the year on the source, not just the number.
- **Pedantic qualifiers:** accept a `qualify` verdict only if the missing condition would change what a typical reader in the stated audience does.
- **New claims** proposed by heads: add them to `claims.json` as `N1, N2...` and adjudicate them the same way.
- **Still unresolved** after your own check: either run a deep round (new run: `$FC init $RUN2 $RUN/input.md --from $RUN`, copy the relevant claims into `$RUN2/claims.json`, `$FC prep $RUN2 --heads "sol:3" --deep`, run, collect) or mark it UNRESOLVED for GPT Pro. Never mark something SUPPORTED because nobody found a contradiction.

Write `$RUN/verdicts.json`, one entry per claim id (including N-claims):
`{"id":"C4","status":"SUPPORTED|CORRECTED|QUALIFIED|REMOVED|UNRESOLVED","before":"(optional, defaults to span)","after":"replacement text","evidence":[{"url":"...","quote":"verbatim"}],"basis":"5/5 heads; quote verified on irs.gov","note":"..."}`

## 6. Rewrite, delta check, dossier

1. Apply only adjudicated changes, as minimal edits, and save `$RUN/revised.md`. Prefer adding scope ("for payments made in 2026", "for US passport holders") and an as-of date to paraphrasing a complex rule.
2. If the rewrite adds any fact that is not in a verdict's `after` text, check it in a delta round: `$FC init $RUN2 $RUN/revised.md --from $RUN`, claims.json with only the new or changed claims, prep/run/collect/adjudicate as above. If you write a dossier, package the final text: `packet $RUN2 --parent $RUN --revised $RUN/revised.md`.
3. Optional, only when the user plans a final review by a stronger model: `$FC packet $RUN --revised $RUN/revised.md` writes `$RUN/handoff.md`: scope, how it was produced, what GPT Pro should do, changes with evidence, unresolved items, the claim ledger and the revised text. It asks GPT Pro for all findings in one pass, keyed to claim ids, ending with a JSON list.
4. money tier, only if you wrote the dossier: `$FC rehearse $RUN` runs that exact dossier on Sol at ultra first. It is slow, so start it with `run_in_background` right after `packet` and show the user the adjudicated changes meanwhile. Its findings land in `$RUN/rehearsal-sol.ultra-1.findings.json` with quote statuses. Verify each like a single-head finding (both checks); expect two kinds: errors your own rewrite introduced (act on them) and completeness notes in GPT Pro's style (keep the ones that change what a reader of this text does). Apply what survives and re-run `packet` with `--revised`. Skip the rehearsal only if the user is in a hurry.

## 7. Deliver

Show the user: `revised.md`, the table of changes (before, after, source), the UNRESOLVED items and per-head stats from `matrix.md`. If you wrote the dossier, give its path and copy it to the clipboard when a clipboard tool exists (`pbcopy` on macOS, `wl-copy` or `xclip -selection clipboard` on Linux; otherwise just give the path). Describe the run from `out/*.meta.json` and `merged.json` (which heads ran and parsed), never from intent.

## 8. When GPT Pro answers (optional)

When the user brings back GPT Pro's reply, do the bookkeeping yourself (never ask the user to record anything):
1. Save it verbatim to `$RUN/pro-round-<N>.md` and run `$FC pro $RUN --round <N> --reply $RUN/pro-round-<N>.md`. It counts GPT Pro's findings against our verdicts, writes the line to `~/.claude/thinktank/factcheck-ledger.jsonl`, and appends every finding on a claim we called SUPPORTED, or outside the ledger, to `~/.claude/thinktank/factcheck-misses.jsonl` (the regression set that tells us what the stage misses).
2. GPT Pro is fallible too: adjudicate its findings with the same two checks before editing.
3. Apply, delta-check the changes, update `verdicts.json`, and `packet` again (with `--revised`) for round 2 with a disposition per GPT Pro finding (fixed, rejected with the quote that shows the text is right, partial).

## 9. Failures

- A head with timeout, empty or unparsable output is excluded and named in the report. Fewer than 2 parsed heads: say "degraded" and check every claim yourself.
- Anthropic 500/529 on an Agent head: retry once, then continue with the codex heads and say the Claude family is missing.
- codex "model is not supported" for gpt-6*: `npm i -g @openai/codex@latest && rm ~/.codex/models_cache.json`, then `$FC setup`.
- agy `auto-denied`: the `factcheck` agy project is missing; `$FC setup` recreates it (grants read_url and search_web only, scoped to `~/.claude/thinktank/factcheck-agy`).
