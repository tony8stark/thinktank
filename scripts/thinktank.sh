#!/bin/bash
# thinktank.sh <run-dir> <brief.md> [--heads "sol:3,gemini:1"] [--timeout SEC]
# Default codex head is sol (gpt-6.1-sol); astra is opt-in only.
# Launches codex (sol, astra) and agy (gemini, gemini-flash) heads in parallel on ONE brief.
# Claude-family heads (opus, sonnet, fable) are launched by Claude via the Agent tool, not here.
# Writes out/<head>.md and out/<head>.meta.json; prints "THINKTANK DONE ok=N error=M"; exit 0 iff M=0.
# thinktank.sh --available prints the channels this machine can run, one per line (agent is always there).
set -u
if [ "${1:-}" = "--available" ]; then
  command -v codex >/dev/null && echo codex
  command -v agy >/dev/null && echo agy
  echo agent; exit 0
fi
RUN="$1"; BRIEF="$2"; shift 2
HEADS="sol:3"; TIMEOUT=900
while [ $# -gt 0 ]; do case "$1" in
  --heads) HEADS="$2"; shift 2;; --timeout) TIMEOUT="$2"; shift 2;;
  *) echo "unknown arg $1" >&2; exit 2;; esac; done

model_of(){ case "$1" in
  sol) echo "codex gpt-6.1-sol low";; astra) echo "codex gpt-6-astra low";; gemini) echo "agy gemini-3.8-flash-high -";;
  gemini-flash) echo "agy gemini-3.8-flash-high -";; opus|sonnet|fable) echo "agent $1 -";;
  *) return 1;; esac; }

# validate the spec before touching the filesystem, so a typo fails before anything is launched
JOBS=()
IFS=, read -ra PARTS <<< "$HEADS"
for p in "${PARTS[@]}"; do
  name="${p%%:*}"; n="${p#*:}"; [ "$n" = "$p" ] && n=1
  model_of "$name" >/dev/null || { echo "FATAL: unknown head '$name' (sol|astra|gemini|gemini-flash|opus|sonnet|fable)" >&2; exit 2; }
  for i in $(seq 1 "$n"); do JOBS+=("$name-$i"); done
done
# gemini = Gemini 3.8 Flash high; gemini-flash is the same model.
# agy model ids can leave the catalogue without notice. Check the live catalogue before launching anything and
# never fall back to another model: an answer filed under the wrong model is worse than a missing head. Exit 3 = a requested agy model is not offered right now.
AGY_MODELS=""
for j in "${JOBS[@]}"; do
  read -r ch model _ <<< "$(model_of "${j%-*}")"; [ "$ch" = "agy" ] || continue
  [ -n "$AGY_MODELS" ] || AGY_MODELS="$(perl -e 'alarm 60; exec @ARGV' agy models 2>/dev/null | cut -f1)"
  [ -n "$AGY_MODELS" ] || { echo "WARN: 'agy models' returned nothing, cannot pre-check $model" >&2; break; }
  echo "$AGY_MODELS" | grep -qx -- "$model" || {
    echo "FATAL: head '${j%-*}' needs agy model '$model', which agy does not offer now. Offered: $(echo "$AGY_MODELS" | grep -v ' ' | tr '\n' ' ')" >&2
    echo "Nothing was launched. Do not swap in another model silently: re-run without this head and report it as missing." >&2
    exit 3; }
done
[ -s "$BRIEF" ] || { echo "FATAL: brief $BRIEF missing or empty" >&2; exit 2; }
mkdir -p "$RUN/out" "$RUN/log" "$RUN/tmp"; RUN="$(cd "$RUN" && pwd)"
BRIEF="$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")"
[ "$BRIEF" = "$RUN/brief.md" ] || cp "$BRIEF" "$RUN/brief.md"

with_timeout(){ # <sec> cmd...: runs cmd in its own process group and kills the whole group on timeout (exit 142).
  # codex is a node wrapper around a native binary: "alarm + exec" killed only the wrapper and left the binary running.
  perl -e 'my $t = shift; my $pid = fork; die "fork: $!" unless defined $pid;
    if (!$pid) { setpgrp(0, 0); exec @ARGV; exit 127 }
    $SIG{ALRM} = sub { kill "TERM", -$pid; sleep 3; kill "KILL", -$pid; exit 142 };
    alarm $t; waitpid $pid, 0; exit($? & 127 ? 128 + ($? & 127) : $? >> 8)' "$@"; }

write_meta(){ # head channel model status latency ptok ctok rtok cost
  python3 - "$RUN/out/$1.meta.json" "$@" <<'PY'
import sys, json, os
m, head, ch, model, status, lat, p, c, r, cost = sys.argv[1:11]
out = os.path.join(os.path.dirname(m), head + ".md")
def num(x, f=int):
    try: return f(x)
    except Exception: return None
json.dump({"head": head, "channel": ch, "model": model, "status": status, "latency_s": int(lat),
  "prompt_tok": num(p), "completion_tok": num(c), "reasoning_tok": num(r), "cost_usd": num(cost, float),
  "chars": os.path.getsize(out) if os.path.exists(out) else 0}, open(m, "w"), indent=1)
PY
}

codex_tokens(){ # <log> -> "ptok ctok rtok" from the session rollout
  local SID ROLL; SID=$(grep -m1 'session id:' "$1" | sed 's/.*session id: *//')
  [ -n "$SID" ] || { echo "  "; return; }
  ROLL=$(find "$HOME/.codex/sessions" -name "*$SID.jsonl" 2>/dev/null | head -1)
  [ -n "$ROLL" ] || { echo "  "; return; }
  python3 - "$ROLL" <<'PY'
import json, sys, re
last = None
for line in open(sys.argv[1]):
    m = re.search(r'"total_token_usage":(\{[^}]*\})', line)
    if m: last = json.loads(m.group(1))
print(*([last.get(k, "") for k in ("input_tokens", "output_tokens", "reasoning_output_tokens")] if last else ["", "", ""]))
PY
}

run_head(){
  local head="$1"; local name="${head%-*}"
  local ch model eff; read -r ch model eff <<< "$(model_of "$name")"
  local OUT="$RUN/out/$head.md" LOG="$RUN/log/$head.log" SCR="$RUN/tmp/$head"
  [ "$ch" = "agent" ] && return 0                      # Claude heads are the Agent tool's job
  [ -s "$OUT" ] && { echo "skip $head"; return 0; }
  rm -f "$OUT"; mkdir -p "$SCR"; local start; start=$(date +%s); local status=ok p="" c="" r="" cost="" rc
  case "$ch" in
    codex)
      ( cd "$SCR" && with_timeout "$TIMEOUT" codex exec --skip-git-repo-check -s read-only \
          -m "$model" -c model_reasoning_effort="\"$eff\"" -o "$SCR/answer.md" - < "$RUN/brief.md" > "$LOG" 2>&1 )
      rc=$?
      if [ $rc -eq 0 ] && [ -s "$SCR/answer.md" ]; then cp "$SCR/answer.md" "$OUT"; else status=error; fi
      read -r p c r <<< "$(codex_tokens "$LOG")" ;;
    agy)
      with_timeout "$TIMEOUT" agy -p "$(cat "$RUN/brief.md")" --model "$model" \
          --output-format json --print-timeout "${TIMEOUT}s" > "$LOG" 2>&1
      rc=$?
      read -r p c r <<< "$(python3 - "$LOG" "$OUT" <<'PY'
import sys, json
raw = open(sys.argv[1]).read(); ans = None; u = {}
for line in raw.splitlines()[::-1]:
    line = line.strip()
    if line.startswith("{"):
        try:
            d = json.loads(line); ans = d.get("response") or d.get("result"); u = d.get("usage", {}); break
        except Exception: pass
if ans and "auto-denied" not in raw: open(sys.argv[2], "w").write(ans.strip() + "\n")
print(u.get("input_tokens", ""), u.get("output_tokens", ""), u.get("thinking_tokens", ""))
PY
)"
      if [ $rc -ne 0 ] || [ ! -s "$OUT" ] || grep -q "auto-denied\|no output produced" "$LOG"; then status=error; rm -f "$OUT"; fi ;;
  esac
  local lat=$(( $(date +%s) - start ))
  write_meta "$head" "$ch" "$model" "$status" "$lat" "$p" "$c" "$r" "$cost"
  echo "$status $head ${lat}s"
}

for j in "${JOBS[@]}"; do run_head "$j" & done; wait
ok=0; err=0
for j in "${JOBS[@]}"; do
  name="${j%-*}"; read -r ch _ <<< "$(model_of "$name")"; [ "$ch" = "agent" ] && continue
  if [ "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["status"])' "$RUN/out/$j.meta.json" 2>/dev/null)" = "ok" ]; then ok=$((ok+1)); else err=$((err+1)); fi
done
echo "THINKTANK DONE ok=$ok error=$err"; [ $err -eq 0 ]
