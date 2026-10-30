#!/bin/bash
# thinktank.sh <run-dir> <brief.md> [--heads "sol:3,gemini:1"] [--timeout SEC]
# Default codex head is sol (gpt-6.1-sol); astra is opt-in only.
# Launches codex, agy, opencode, openrouter, local and command heads in parallel on ONE brief. Heads come from heads.json (heads.py).
# Claude-family heads (opus, sonnet, fable) are launched by Claude via the Agent tool, not here.
# Writes out/<head>.md and out/<head>.meta.json; prints "THINKTANK DONE ok=N error=M"; exit 0 iff M=0.
# thinktank.sh --available prints the channels this machine can run, one per line (agent is always there).
set -u
HEADS_PY="$(cd "$(dirname "$0")" && pwd)/heads.py"
[ "${1:-}" = "--available" ] && exec python3 "$HEADS_PY" available
usage(){ echo 'usage: thinktank.sh <run-dir> <brief.md> [--heads SPEC] [--timeout SEC]' >&2; exit 2; }
[ $# -ge 2 ] || usage
RUN="$1"; BRIEF="$2"; shift 2
HEADS="sol:3"; TIMEOUT=900
while [ $# -gt 0 ]; do case "$1" in
  --heads) [ $# -ge 2 ] || usage; HEADS="$2"; shift 2;; --timeout) [ $# -ge 2 ] || usage; TIMEOUT="$2"; shift 2;;
  *) echo "unknown arg $1" >&2; exit 2;; esac; done

model_of(){ python3 "$HEADS_PY" model "$1"; }

# validate the spec before touching the filesystem, so a typo fails before anything is launched
[[ "$TIMEOUT" =~ ^[1-9][0-9]*$ ]] || usage
[[ "$HEADS" != *$'\n'* ]] || usage
[ -n "$HEADS" ] && [[ "$HEADS" != ,* && "$HEADS" != *, && "$HEADS" != *,,* ]] || usage
JOBS=(); SEEN=","
IFS=, read -ra PARTS <<< "$HEADS"
[ ${#PARTS[@]} -gt 0 ] || usage
for p in "${PARTS[@]}"; do
  name="${p%%:*}"; n="${p#*:}"; [ "$n" = "$p" ] && n=1
  [[ "$n" =~ ^[1-9][0-9]*$ ]] || { echo "invalid count '$n'" >&2; exit 2; }
  case "$SEEN" in *",$name,"*) echo "duplicate head '$name'; use $name:2 for two copies" >&2; exit 2;; esac
  SEEN="$SEEN$name,"
  model_of "$name" >/dev/null || { echo "FATAL: unknown head '$name' (known: $(python3 "$HEADS_PY" list | cut -f1 | tr '\n' ' '))" >&2; exit 2; }
  read -r ch _ <<< "$(model_of "$name")"
  case "$ch" in codex|agy|opencode|openrouter|local|command|agent) ;; *) echo "unknown channel '$ch'" >&2; exit 2;; esac
  for ((i=1; i<=n; i++)); do JOBS+=("$name-$i"); done
done
[ ${#JOBS[@]} -gt 0 ] || usage
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
if [ -e "$RUN/brief.md" ]; then
  python3 - "$RUN/brief.md" "$BRIEF" <<'PYBRIEF'
import sys
sys.exit(0 if open(sys.argv[1], "rb").read() == open(sys.argv[2], "rb").read() else 1)
PYBRIEF
  [ $? -eq 0 ] || { echo "run dir already holds a different brief; use a new run dir" >&2; exit 2; }
fi
mkdir -p "$RUN/out" "$RUN/log" "$RUN/tmp"; RUN="$(cd "$RUN" && pwd)"
BRIEF="$(cd "$(dirname "$BRIEF")" && pwd)/$(basename "$BRIEF")"
[ "$BRIEF" = "$RUN/brief.md" ] || cp "$BRIEF" "$RUN/brief.md"

BRIEF_BYTES=$(python3 -c 'import os,sys; print(os.path.getsize(sys.argv[1]))' "$RUN/brief.md")
AGY_READY=0; AGY_MESSAGE=""
for j in "${JOBS[@]}"; do
  read -r ch _ <<< "$(model_of "${j%-*}")"
  [ "$ch" = "agy" ] || continue
  AGY_MESSAGE=$(python3 "$HEADS_PY" agy-project thinktank "$HOME/.local/share/thinktank/agy-thinktank" 2>&1)
  [ $? -ne 0 ] || AGY_READY=1
  break
done

cleanup(){
  trap '' INT TERM
  local f pid; local groups=()
  for f in "$RUN"/tmp/*/pgid*; do
    [ -f "$f" ] || continue
    read -r pid < "$f"
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || continue
    groups+=("$pid")
  done
  [ ${#groups[@]} -gt 0 ] || return 0
  for pid in "${groups[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || :; done
  sleep 1
  for pid in "${groups[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || :; done
}
interrupted=0
trap 'interrupted=1; exit 130' INT
trap 'interrupted=1; exit 143' TERM
trap '[ "$interrupted" -eq 0 ] || cleanup' EXIT

with_timeout(){ # <sec> cmd...: record the group before allowing the child to launch.
  perl -e 'use POSIX qw(setpgid); my $t = shift; pipe(my $r, my $w) or die "pipe: $!";
    my $pid = fork; die "fork: $!" unless defined $pid;
    if (!$pid) { close $w; setpgid(0, 0); <$r>; close $r; exec @ARGV; exit 127 }
    close $r; setpgid($pid, $pid);
    open(my $f, ">", $ENV{THINKTANK_PGID}) or die "pgid: $!"; print $f "$pid\n"; close $f;
    print $w "go\n"; close $w;
    $SIG{ALRM} = sub { my @groups = ($pid);
      if (open(my $c, "<", $ENV{THINKTANK_PGID} . ".child")) {
        my $child = <$c>; close $c; push @groups, $child if $child =~ /^[1-9][0-9]*\s*$/;
      }
      kill "TERM", map { -$_ } @groups; sleep 1; kill "KILL", map { -$_ } @groups; exit 142 };
    alarm $t; waitpid $pid, 0; my $rc = $?; unlink $ENV{THINKTANK_PGID};
    exit($rc & 127 ? 128 + ($rc & 127) : $rc >> 8)' "$@"; }

write_meta(){ # head channel model status latency ptok ctok rtok cost [resolved-model]
  python3 - "$RUN/out/$1.meta.json" "$@" <<'PY'
import sys, json, os
m, head, ch, model, status, lat, p, c, r, cost = sys.argv[1:11]
out = os.path.join(os.path.dirname(m), head + ".md")
def num(x, f=int):
    try: return f(x)
    except Exception: return None
data = {"head": head, "channel": ch, "model": model, "status": status, "latency_s": int(lat),
  "prompt_tok": num(p), "completion_tok": num(c), "reasoning_tok": num(r), "cost_usd": num(cost, float),
  "chars": os.path.getsize(out) if os.path.exists(out) else 0}
if ch == "openrouter": data["resolved_model"] = sys.argv[11] if len(sys.argv) > 11 and sys.argv[11] else None
json.dump(data, open(m, "w"), indent=1)
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
  if [ -s "$OUT" ] && python3 - "$RUN/out/$head.meta.json" <<'PYDONE'
import json, sys
try:
    with open(sys.argv[1]) as f: done = json.load(f).get("status") == "ok"
except (OSError, ValueError, AttributeError): done = False
sys.exit(0 if done else 1)
PYDONE
  then echo "skip $head"; return 0; fi
  rm -f "$OUT"; mkdir -p "$SCR"; local start; start=$(date +%s); local status=ok p="" c="" r="" cost="" rc
  rm -f "$SCR"/pgid*
  export THINKTANK_PGID="$SCR/pgid"
  if { [ "$ch" = "agy" ] || [ "$ch" = "opencode" ]; } && [ "$BRIEF_BYTES" -gt 100000 ]; then
    echo "brief too large for argv: $BRIEF_BYTES bytes" > "$LOG"; status=error
  else
  case "$ch" in
    codex)
      rm -f "$SCR/answer.md"
      ( cd "$SCR" && with_timeout "$TIMEOUT" codex exec --skip-git-repo-check -s read-only \
          -m "$model" -c model_reasoning_effort="\"$eff\"" -o "$SCR/answer.md" - < "$RUN/brief.md" > "$LOG" 2>&1 )
      rc=$?
      if [ $rc -eq 0 ] && [ -s "$SCR/answer.md" ]; then cp "$SCR/answer.md" "$OUT"; else status=error; fi
      read -r p c r <<< "$(codex_tokens "$LOG")" ;;
    openrouter|opencode|local|command)
      local tokens QUEUE
      if [ "$ch" = local ]; then
        QUEUE=$((TIMEOUT * 3))
        tokens=$(with_timeout "$((TIMEOUT + QUEUE + 60))" python3 "$HEADS_PY" call "$name" "$RUN/brief.md" "$OUT" "$LOG" --timeout "$TIMEOUT" --cwd "$SCR" --queue-timeout "$QUEUE")
      else
        tokens=$(with_timeout "$TIMEOUT" python3 "$HEADS_PY" call "$name" "$RUN/brief.md" "$OUT" "$LOG" --timeout "$TIMEOUT" --cwd "$SCR")
      fi
      rc=$?
      read -r p c r cost <<< "$tokens"
      if [ "$rc" -ne 0 ] || [ ! -s "$OUT" ]; then status=error; rm -f "$OUT"; fi ;;
    agy)
      if [ "$AGY_READY" -ne 1 ]; then
        echo "cannot ensure agy project thinktank: $AGY_MESSAGE" > "$LOG"; status=error
      else
      with_timeout "$((TIMEOUT + 60))" python3 "$HEADS_PY" agy-run --timeout "$TIMEOUT" --log "$LOG" --cwd "$SCR" -- \
          agy --project thinktank -p "$(cat "$RUN/brief.md")" --model "$model" --output-format json --print-timeout "${TIMEOUT}s"
      rc=$?
      read -r p c r <<< "$(python3 - "$LOG" "$OUT" <<'PY'
import sys, json
raw = open(sys.argv[1]).read(); ans = None; u = {}; diagnostics = []
for line in raw.splitlines():
    try: d = json.loads(line)
    except ValueError:
        diagnostics.append(line); continue
    if isinstance(d, dict):
        response = d.get("response") or d.get("result")
        if isinstance(response, str): ans = response; u = d.get("usage") or {}
        diagnostics.append(json.dumps({k: v for k, v in d.items() if k not in ("response", "result")}))
    else: diagnostics.append(line)
failed = any(s in "\n".join(diagnostics) for s in ("auto-denied", "no output produced"))
if ans and not failed: open(sys.argv[2], "w").write(ans.strip() + "\n")
if not isinstance(u, dict): u = {}
print(u.get("input_tokens", ""), u.get("output_tokens", ""), u.get("thinking_tokens", ""))
PY
)"
      if [ $rc -ne 0 ] || [ ! -s "$OUT" ]; then status=error; rm -f "$OUT"; fi
      fi ;;
    *) echo "unknown channel '$ch'" > "$LOG"; status=error ;;
  esac
  fi
  rm -f "$SCR"/pgid*
  local lat=$(( $(date +%s) - start ))
  local resolved=""
  [ "$ch" != openrouter ] || [ "$status" != ok ] || resolved=$(python3 "$HEADS_PY" resolved-model "$LOG")
  write_meta "$head" "$ch" "$model" "$status" "$lat" "$p" "$c" "$r" "$cost" "$resolved"
  echo "$status $head ${lat}s"
}

for j in "${JOBS[@]}"; do run_head "$j" & done; wait
ok=0; err=0
for j in "${JOBS[@]}"; do
  name="${j%-*}"; read -r ch _ <<< "$(model_of "$name")"; [ "$ch" = "agent" ] && continue
  if [ "$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["status"])' "$RUN/out/$j.meta.json" 2>/dev/null)" = "ok" ]; then ok=$((ok+1)); else err=$((err+1)); fi
done
echo "THINKTANK DONE ok=$ok error=$err"; [ $err -eq 0 ]
