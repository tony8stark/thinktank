#!/bin/bash
set -u
HERE="$(cd "$(dirname "$0")/.." && pwd)"; R="$HERE/scripts/thinktank.sh"
T=$(mktemp -d /tmp/tt-test.XXXX); fail=0
ok(){ echo "ok   $1"; }; bad(){ echo "FAIL $1"; fail=1; }
# 1. bad head spec is rejected
"$R" "$T/r0" /dev/null --heads "bogus:1" >/dev/null 2>&1 && bad "bogus head accepted" || ok "bogus head rejected"
# 2. real sol head on a trivial brief writes answer + meta with tokens
printf 'Answer with the single word PONG and nothing else. Do not use tools.\n' > "$T/brief.md"
"$R" "$T/r1" "$T/brief.md" --heads "sol:1" > "$T/r1.log" 2>&1
grep -q "THINKTANK DONE ok=1 error=0" "$T/r1.log" && ok "done line" || bad "done line: $(tail -2 "$T/r1.log")"
grep -qi "pong" "$T/r1/out/sol-1.md" 2>/dev/null && ok "answer" || bad "answer content"
python3 - "$T/r1/out/sol-1.meta.json" <<'PY' && ok "meta tokens" || bad "meta tokens"
import json,sys; d=json.load(open(sys.argv[1]))
assert d["status"]=="ok" and d["channel"]=="codex" and d["completion_tok"] and d["prompt_tok"], d
PY
# 3. re-run skips the finished head
"$R" "$T/r1" "$T/brief.md" --heads "sol:1" 2>&1 | grep -q "skip sol-1" && ok "idempotent" || bad "idempotent"
# 4. timeout yields error status and non-zero exit
"$R" "$T/r2" "$T/brief.md" --heads "sol:1" --timeout 1 >/dev/null 2>&1 && bad "timeout exit 0" || ok "timeout exit non-zero"
grep -q '"status": "error"' "$T/r2/out/sol-1.meta.json" 2>/dev/null && ok "timeout meta" || bad "timeout meta"
[ -s "$T/r2/out/sol-1.md" ] && bad "timeout left output" || ok "timeout left no output"
rm -rf "$T"; exit $fail
