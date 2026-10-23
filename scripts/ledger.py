#!/usr/bin/env python3
"""ledger.py <run-dir> --topic T [--agreed N --likely N --single N --single-confirmed N]
--agent-tokens head=N[,head=N]  total tokens reported by the Agent tool per Claude head.
Fills in meta for Agent-tool heads, appends one JSON line to the ledger
(~/.claude/thinktank/ledger.jsonl, override with THINKTANK_LEDGER), prints the cost line."""
import sys, os, json, glob, time
run = os.path.abspath(sys.argv[1].rstrip("/")); a = sys.argv[2:]
def opt(n, d=None): return a[a.index(n) + 1] if n in a else d
agent_tok = {}
for kv in (opt("--agent-tokens", "") or "").split(","):
    if "=" in kv: k, v = kv.split("="); agent_tok[k] = int(v)
out = f"{run}/out"; heads = []
for md in sorted(glob.glob(f"{out}/*.md")):
    head = os.path.basename(md)[:-3]; meta = f"{out}/{head}.meta.json"
    if not os.path.exists(meta):
        json.dump({"head": head, "channel": "agent", "model": head.rsplit("-", 1)[0],
                   "status": "ok" if os.path.getsize(md) else "error", "latency_s": None,
                   "prompt_tok": None, "completion_tok": None, "reasoning_tok": None, "cost_usd": None,
                   "total_tok": agent_tok.get(head), "chars": os.path.getsize(md)}, open(meta, "w"), indent=1)
    heads.append(json.load(open(meta)))
for meta in glob.glob(f"{out}/*.meta.json"):          # failed heads have a meta but no .md
    h = json.load(open(meta))
    if h["head"] not in [x["head"] for x in heads]: heads.append(h)
heads.sort(key=lambda h: h["head"])
tok = {}
for h in heads:
    t = tok.setdefault(h["channel"], {"heads": 0, "ok": 0, "prompt_tok": 0, "completion_tok": 0,
                                      "reasoning_tok": 0, "cost_usd": 0.0, "latency_s": 0, "total_tok": 0})
    t["heads"] += 1; t["ok"] += h["status"] == "ok"
    for k in ("prompt_tok", "completion_tok", "reasoning_tok", "cost_usd", "total_tok"):
        if h.get(k) is not None: t[k] += h[k]
    if h.get("latency_s") is not None: t["latency_s"] = max(t["latency_s"], h["latency_s"])
line = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "run": run, "topic": opt("--topic", ""),
        "heads": [h["head"] for h in heads], "tokens": tok,
        "agreed": int(opt("--agreed", 0)), "likely": int(opt("--likely", 0)),
        "single": int(opt("--single", 0)), "single_confirmed": int(opt("--single-confirmed", 0))}
path = os.environ.get("THINKTANK_LEDGER") or os.path.expanduser("~/.claude/thinktank/ledger.jsonl")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "a") as f: f.write(json.dumps(line, ensure_ascii=False) + "\n")
parts = []
for ch, t in tok.items():
    if ch == "agent": parts.append(f"{ch}: {t['ok']}/{t['heads']} heads, " + (f"{t['total_tok']} total tokens (Agent tool count)" if t["total_tok"] else "tokens n/a"))
    else:
        parts.append(f"{ch}: {t['ok']}/{t['heads']} heads, {t['prompt_tok']} in / {t['completion_tok']} out "
                     f"({t['reasoning_tok']} reasoning), max latency {t['latency_s']}s"
                     + (f", ${t['cost_usd']:.3f}" if t["cost_usd"] else ""))
print("Cost: " + "; ".join(parts))
