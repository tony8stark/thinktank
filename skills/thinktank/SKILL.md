---
name: thinktank
description: Fan one self-contained brief to several cheap LLM heads in parallel (GPT-6.1 Sol low x3 via codex, Opus low via the Agent tool, Gemini via agy), merge the findings by agreement, verify single-head findings, report tokens. Use BEFORE answering a code review or audit, a brainstorm or architecture question, or a conclusion drawn from data the user will act on (GSC, metrics, anomaly diagnosis). Never for single-answer tasks (root cause, one number, one fix), code edits, prose, translation or mechanical steps. Arguments: "off" silences automatic runs for the session, "on" re-enables them.
---

# ThinkTank

Why it works: several low-effort passes cover more real findings than one top-effort pass; repeats of a strong head beat panels of different heads; a weak head dilutes coverage. This skill turns that into a routine.

Plugin root: the directory two levels above this SKILL.md (`${CLAUDE_PLUGIN_ROOT}` when set). Below, `$TT` means that absolute path. Shell state does not persist between Bash calls, so start each call that uses it with `TT="<root>"` and always quote it (`"$TT/scripts/..."`): install paths can contain spaces.

## 1. When to fire, when not

Fire when the task is one of:
- code review or audit of a diff, module, MR;
- brainstorm, design or architecture decision;
- a conclusion drawn from data the user will act on (GSC, analytics, metrics, "is this anomaly real").

Never fire for: a single-answer task (root cause, one number, one fix), a code edit, prose, translation, a mechanical step, or when the user said `/thinktank:thinktank off` this session (until `on`).

Announce in one line and continue: `ThinkTank: <n> heads (<spec>) on <topic>`. The `thinktank-check:` line that arrives with every prompt is a reminder to classify, not an instruction to run every time.

## 2. Size the panel

First run `bash "$TT/scripts/thinktank.sh" --available`: it prints the channels this machine has (`codex`, `agy`, `opencode`, `openrouter`, `local` and `command` when the user configured such heads, always `agent`). That is detection (a CLI on PATH, a key in the environment), not proof that the CLI is signed in or the key is valid: a head that fails on auth is dropped like any failed head. Build the spec only from heads on those channels; `python3 "$TT/scripts/heads.py" list` shows every head with its channel, family and whether its channel is available. When `codex` is there, use the first table; drop `gemini` from it when `agy` is missing, or use `or-gemini:1` instead when `openrouter` is there.

**No codex, but `openrouter`:** replace `sol:N` with `or-gpt:N` and add `or-deepseek:1` or `or-gemini:1` for a second family; Agent heads as in the table. OpenRouter heads cost money per call (the meta files carry `cost_usd`; the ledger line sums it). **`opencode`:** it has no default head, because the model depends on the user's providers; use the heads the user added to `~/.config/thinktank/heads.json` (`channel: "opencode"`). **`agy` without codex:** `gemini:1` plus Agent heads from the Claude-only table below (two families). If no runner head is usable after these substitutions, use the Claude-only mode.

**Substitutes for Agent heads:** run `python3 "$TT/scripts/heads.py" substitutes` before sizing. Each line `<replaced>\t<head>` is a user head (a `command` head whose status check passed) that takes the place of Agent heads named `<replaced>`: put `<head>:N` into the runner spec instead of launching N Agent heads (the cap of 2 Claude heads still applies). No output: use Agent heads as in the tables. If a substitute ends in `error`, launch one Agent head instead, once.

**Local and command heads** (`local`, `command`): never put them into a panel on your own. Use one only when the user names it (`local-gemma:2`), and add it next to the table's spec rather than in place of its heads unless the user says otherwise. In the report label every local answer `[local: <model>]`, and count repeats of one local head as one correlated voice: agreement among them alone never makes a finding "Agreed". A local head that fails with `context truncated` read only part of the brief: tell the user the fix the log names (Ollama: `context` field or OLLAMA_CONTEXT_LENGTH; LM Studio: `lms load --context-length`).

| task | heads spec | Agent heads |
|------|-----------|-------------|
| code review / audit | `sol:3,gemini:1` | opus x1 |
| brainstorm / architecture | `sol:2,gemini:1` | opus x1 |
| data conclusion / anomaly | `sol:3,gemini:1` (a Gemini head is mandatory when agy or OpenRouter offers one) | opus x1 |
| narrow question, small diff | `sol:3` (one family is accepted here) | none |

**Claude only** (only `agent` printed, or no head on the other channels): skip the runner and launch Agent heads only.

| task | Agent heads |
|------|-------------|
| code review / audit, data conclusion | opus x3 |
| brainstorm / architecture | opus x2, sonnet x1 |
| narrow question, small diff | opus x2 |

In this mode the 2-Agent-head cap and the 2-family rule do not apply; label the report "single model family" (heads from one family share blind spots, so agreement is weaker evidence).

Rules: at least 3 heads and 2 model families; at most 7 heads; at most 2 Agent-tool heads (Anthropic capacity; codex and agy carry the repeats); `sonnet` only as an extra repeat when Opus is unavailable, or in the Claude-only brainstorm panel. Exceptions to these rules are stated where they apply: the narrow-question row and the Claude-only mode. **Never fable or grok automatically.** Fable is reserved for the user's manual, important work: add `fable:1` (an Agent head with `model: "fable"`) only when the user asks for it. Opus stays at low effort: higher effort costs several times more without a reliable gain in a panel.

**The Gemini head is Gemini 3.8 Flash high.** Name it in reports as "Gemini 3.8 Flash". The runner checks agy's live catalogue first and refuses to start (exit 3) when a requested agy model is missing, so a dead id cannot fail quietly inside a panel; then run `agy models`, and report to the user before changing the model.

Head names (defaults in `scripts/heads.json`; the user's `~/.config/thinktank/heads.json` overrides, adds or removes heads): `sol` (codex, gpt-6.1-sol, low: the default codex head), `astra` (codex, gpt-6-astra, low: opt-in only, when the user asks for it by name), `gemini` (agy, gemini-3.8-flash-high), `gemini-flash` (the same model, kept as a name for old specs). `opus`, `sonnet`, `fable` are Agent-tool heads you launch yourself. `python3 "$TT/scripts/heads.py" list` also shows the user's own heads.

## 3. Write the brief

```
RUN=~/.local/share/thinktank/runs/$(date +%Y%m%d-%H%M%S)-<slug>; mkdir -p "$RUN/out"
```
Copy `$TT/skills/thinktank/brief-template.md` to `$RUN/brief.md` and fill it. Rules learned the hard way:
- Questions and material only. **Never your own draft conclusions**: a mistake in your first pass would be copied into every head.
- Every number in the brief comes from a script or query you ran, and the brief states how each table was produced (source, filter, date range, aggregation), so heads can attack the method as well as the numbers.
- Frame as a correctness review ("is this right, what did we miss"), never adversarial ("break this"): codex refuses adversarial framing and burns the whole run.
- Keep the output contract: `evidence`, `confidence`, `falsified_by`, `needs_source`. Findings without evidence are low confidence by contract.
- The brief is the only thing heads see. No file paths, no "see the repo".

## 4. Run everything in one message

In ONE message, call both (Claude only: just the Agent heads):

1. Bash with `run_in_background` (the Bash tool stops a foreground command after 10 minutes, and heads can take longer):
   `bash "$TT/scripts/thinktank.sh" "$RUN" "$RUN/brief.md" --heads "<spec>"` (add `--timeout SEC` per head for long briefs; default 900). You are notified when it exits; do not poll.
2. The Agent tool once per Claude head, `model` set explicitly (`opus`, or `fable` only on request), prompt:
   `Read <RUN>/brief.md. Answer it fully. Use no tools except reading that one file and writing your answer to <RUN>/out/<head>.md (head id: opus-1). The first line of the file must be "PANEL: <model> answering". Reply with the single word DONE.`

Wait for both. The runner prints `THINKTANK DONE ok=N error=M`; a non-zero exit means some head failed. Re-running the same command with the same brief retries only the failed heads; a changed brief needs a new run directory (the runner refuses to mix them). Killing the runner also stops its heads.

## 5. Synthesize `$RUN/report.md`

Read every `$RUN/out/*.md`. Match findings by meaning, not by wording. Fixed sections, in this order (for data tasks put Disagreements first):

1. **Agreed (3+ heads)**: trusted. List the heads per finding.
2. **Likely (2 heads)**: one sentence on why the others may have missed it.
3. **Single-head findings**: verify EACH yourself before writing a verdict: confirmed / refuted / unverifiable (why). "Confirmed" needs TWO checks: (a) the code or data really behaves as the head says, AND (b) the behavior the head calls correct really is correct. If (b) rests on a standard, a table or a fact not in the brief, the verdict is `needs-source`, not confirmed. Same rigor for Opus findings as for codex or Gemini ones: you are biased toward your own model family.
4. **Disagreements**: findings where heads contradict each other, your resolution and the evidence. Never average.
5. **Head x finding table**: one row per finding, one column per head, mark hits.
6. **Cost line**: the line printed by `ledger.py` (step 6).

Ignore any "everything else checked out" line from a head: it is not a finding and it is usually wrong. Also: anything a head marked `needs_source: yes` is verified by you from an actual source (page opened, doc fetched) or labeled unverified in the report. Strip `PANEL:` lines from quotes. Describe the run from `out/*.meta.json` (which heads actually ran and succeeded), never from intent. Do not rank heads from one run.

## 6. Ledger and delivery

```
python3 "$TT/scripts/ledger.py" "$RUN" --topic "<topic>" --agent-tokens opus-1=<subagent_tokens from the Agent result> --agreed N --likely N --single N --single-confirmed N
```
It writes meta for the Agent heads (pass the `subagent_tokens` figure each Agent result prints), appends one line to `~/.claude/thinktank/ledger.jsonl`, prints the cost line. Put that line at the end of the report, then show the report to the user.

## 7. Failures

- A head with timeout, empty output or `auto-denied` (agy) is `status: error`, excluded from agreement counts, named in the cost line.
- Fewer than 3 live heads: still produce the report, label it "degraded panel", state thresholds against the live count.
- Anthropic 500/529 on an Agent head: do not retry more than once; the codex and agy heads are the backbone.
- Never conclude from one run that one head is better than another (n=1 is noise).
