---
name: thinktank
description: Fan one self-contained brief to several cheap LLM heads in parallel (GPT-6.1 Sol low x3 via codex, Opus low via the Agent tool, Gemini via agy), merge the findings by agreement, verify single-head findings, report tokens. Use BEFORE answering a code review or audit, a brainstorm or architecture question, or a conclusion drawn from data the user will act on (GSC, metrics, anomaly diagnosis). Never for single-answer tasks (root cause, one number, one fix), code edits, prose, translation or mechanical steps. Arguments: "off" silences automatic runs for the session, "on" re-enables them.
---

# ThinkTank

Why it works: several low-effort passes cover more real findings than one top-effort pass; repeats of a strong head beat panels of different heads; a weak head dilutes coverage. This skill turns that into a routine.

Plugin root: the directory two levels above this SKILL.md (`${CLAUDE_PLUGIN_ROOT}` when set). Below, `$TT` means that root.

## 1. When to fire, when not

Fire when the task is one of:
- code review or audit of a diff, module, MR;
- brainstorm, design or architecture decision;
- a conclusion drawn from data the user will act on (GSC, analytics, metrics, "is this anomaly real").

Never fire for: a single-answer task (root cause, one number, one fix), a code edit, prose, translation, a mechanical step, or when the user said `/thinktank:thinktank off` this session (until `on`).

Announce in one line and continue: `ThinkTank: <n> heads (<spec>) on <topic>`. The `thinktank-check:` line that arrives with every prompt is a reminder to classify, not an instruction to run every time.

## 2. Size the panel

| task | heads spec | Agent heads |
|------|-----------|-------------|
| code review / audit | `sol:3,gemini:1` | opus x1 |
| brainstorm / architecture | `sol:2,gemini:1` | opus x1 |
| data conclusion / anomaly | `sol:3,gemini:1` (gemini = 3.8 Flash high, mandatory) | opus x1 |
| narrow question, small diff | `sol:3` | none |

Rules: at least 3 heads and 2 model families; at most 7 heads; at most 2 Agent-tool heads (Anthropic capacity; codex and agy carry the repeats); `sonnet` only as an extra repeat when Opus is unavailable. **Never fable or grok automatically.** Fable is reserved for the user's manual, important work: add `fable:1` (an Agent head with `model: "fable"`) only when the user asks for it. Opus stays at low effort: higher effort costs several times more without a reliable gain in a panel.

**The Gemini head is Gemini 3.8 Flash high.** Name it in reports as "Gemini 3.8 Flash". The runner checks agy's live catalogue first and refuses to start (exit 3) when a requested agy model is missing, so a dead id cannot fail quietly inside a panel; then run `agy models`, and report to the user before changing the model.

Head names the runner knows: `sol` (codex, gpt-6.1-sol, low: the default codex head), `astra` (codex, gpt-6-astra, low: opt-in only, when the user asks for it by name), `gemini` (agy, gemini-3.8-flash-high), `gemini-flash` (the same model, kept as a name for old specs). `opus`, `sonnet`, `fable` are Agent-tool heads you launch yourself.

## 3. Write the brief

```
RUN=~/.local/share/thinktank/runs/$(date +%Y%m%d-%H%M%S)-<slug>; mkdir -p $RUN
```
Copy `$TT/skills/thinktank/brief-template.md` to `$RUN/brief.md` and fill it. Rules learned the hard way:
- Questions and material only. **Never your own draft conclusions**: a mistake in your first pass would be copied into every head.
- Every number in the brief comes from a script or query you ran, and the brief states how each table was produced (source, filter, date range, aggregation), so heads can attack the method as well as the numbers.
- Frame as a correctness review ("is this right, what did we miss"), never adversarial ("break this"): codex refuses adversarial framing and burns the whole run.
- Keep the output contract: `evidence`, `confidence`, `falsified_by`, `needs_source`. Findings without evidence are low confidence by contract.
- The brief is the only thing heads see. No file paths, no "see the repo".

## 4. Run everything in one message

In ONE message, call both:

1. Bash, foreground, never Monitor or background:
   `bash $TT/scripts/thinktank.sh "$RUN" "$RUN/brief.md" --heads "<spec>"` (add `--timeout SEC` for long briefs; default 900).
2. The Agent tool once per Claude head, `model` set explicitly (`opus`, or `fable` only on request), prompt:
   `Read <RUN>/brief.md. Answer it fully. Use no tools except reading that one file and writing your answer to <RUN>/out/<head>.md (head id: opus-1). The first line of the file must be "PANEL: <model> answering". Reply with the single word DONE.`

Wait for both. The runner prints `THINKTANK DONE ok=N error=M`; a non-zero exit means some head failed. Re-running the same command retries only the failed heads.

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
python3 $TT/scripts/ledger.py "$RUN" --topic "<topic>" --agent-tokens opus-1=<subagent_tokens from the Agent result> --agreed N --likely N --single N --single-confirmed N
```
It writes meta for the Agent heads (pass the `subagent_tokens` figure each Agent result prints), appends one line to `~/.claude/thinktank/ledger.jsonl`, prints the cost line. Put that line at the end of the report, then show the report to the user.

## 7. Failures

- A head with timeout, empty output or `auto-denied` (agy) is `status: error`, excluded from agreement counts, named in the cost line.
- Fewer than 3 live heads: still produce the report, label it "degraded panel", state thresholds against the live count.
- Anthropic 500/529 on an Agent head: do not retry more than once; the codex and agy heads are the backbone.
- Never conclude from one run that one head is better than another (n=1 is noise).
