#!/usr/bin/env python3
"""heads.py: the head registry and the channels shared by thinktank.sh and factcheck.py.

  available                         channels this machine can run, one per line (agent is always there)
  list                              every head: name, channel, model, effort, family, web, available
  model <name>                      "channel model effort" for one head (exit 1 if unknown), used by thinktank.sh
  call <name> <brief> <out> <log> [--timeout S] [--web] [--effort E]
                                    run one openrouter or opencode head; prints "ptok ctok rtok cost"

Registry: heads.json next to this script, merged with ~/.config/thinktank/heads.json (or $THINKTANK_HEADS):
same shape, a user entry replaces the default of that name, a null entry removes it.
OpenRouter needs OPENROUTER_API_KEY in the environment. OpenCode needs the `opencode` CLI with a provider set up."""
import sys, os, json, shutil, subprocess, signal, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
USER_REGISTRY = os.environ.get("THINKTANK_HEADS") or os.path.expanduser("~/.config/thinktank/heads.json")
CLI_CHANNELS = ("codex", "agy", "opencode")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"


def registry():
    heads = json.load(open(os.path.join(HERE, "heads.json")))["heads"]
    if os.path.exists(USER_REGISTRY):
        for name, h in (json.load(open(USER_REGISTRY)).get("heads") or {}).items():
            if h is None: heads.pop(name, None)
            else: heads[name] = h
    return heads


def available():
    chans = [c for c in CLI_CHANNELS if shutil.which(c)]
    if os.environ.get("OPENROUTER_API_KEY"): chans.append("openrouter")
    return chans + ["agent"]


def call_openrouter(h, prompt, out, logf, timeout, web=False, effort=None):
    """One chat completion. Returns (status, ptok, ctok, rtok, cost)."""
    body = {"model": h["model"], "messages": [{"role": "user", "content": prompt}], "usage": {"include": True}}
    eff = effort or h.get("effort", "-")
    if eff and eff != "-": body["reasoning"] = {"effort": eff}
    if web: body["plugins"] = [{"id": "web"}]
    req = urllib.request.Request(OPENROUTER_URL, data=json.dumps(body).encode(), method="POST", headers={
        "Authorization": "Bearer " + os.environ.get("OPENROUTER_API_KEY", ""), "Content-Type": "application/json",
        "X-Title": "thinktank"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r: d = json.load(r)
    except urllib.error.HTTPError as e:
        logf.write(f"HTTP {e.code}: {e.read().decode(errors='replace')[:2000]}\n"); return "error", None, None, None, None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as e:
        logf.write(f"{type(e).__name__}: {e}\n"); return "error", None, None, None, None
    logf.write(json.dumps({k: v for k, v in d.items() if k != "choices"}) + "\n")
    try: text = d["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError): text = ""
    u = d.get("usage") or {}
    rtok = (u.get("completion_tokens_details") or {}).get("reasoning_tokens")
    if not text.strip(): return "error", u.get("prompt_tokens"), u.get("completion_tokens"), rtok, u.get("cost")
    with open(out, "w") as f: f.write(text.strip() + "\n")
    return "ok", u.get("prompt_tokens"), u.get("completion_tokens"), rtok, u.get("cost")


def call_opencode(h, prompt, out, logf, timeout, web=False, effort=None, cwd=None):
    """`opencode run` with every tool denied except web search and fetch (when web). Returns like call_openrouter."""
    perm = {"*": "deny"}
    if web: perm.update({"webfetch": "allow", "websearch": "allow"})
    env = dict(os.environ, OPENCODE_PERMISSION=json.dumps(perm))
    cmd = ["opencode", "run", "--model", h["model"], prompt]
    p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=logf, cwd=cwd, env=env,
                         text=True, start_new_session=True)
    try:
        text, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        for sig in (signal.SIGTERM, signal.SIGKILL):  # kill the whole group: a wrapper can leave its binary running
            try: os.killpg(p.pid, sig)
            except (ProcessLookupError, PermissionError): pass
            try: p.wait(timeout=5); break
            except subprocess.TimeoutExpired: pass
        logf.write(f"\nTIMEOUT after {timeout}s\n"); return "error", None, None, None, None
    if p.returncode != 0 or not (text or "").strip():
        logf.write(f"\nopencode exit {p.returncode}\n"); return "error", None, None, None, None
    with open(out, "w") as f: f.write(text.strip() + "\n")
    return "ok", None, None, None, None


CALLS = {"openrouter": call_openrouter, "opencode": call_opencode}


def main(a):
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    cmd = a[0]
    if cmd == "available":
        print("\n".join(available())); return 0
    if cmd == "list":
        av = set(available())
        for n, h in registry().items():
            print(f"{n}\t{h['channel']}\t{h['model']}\t{h.get('effort', '-')}\t{h.get('family', '?')}\t"
                  f"web={'yes' if h.get('web') else 'no'}\t{'available' if h['channel'] in av else 'missing'}")
        return 0
    if cmd == "model":
        h = registry().get(a[1]) if len(a) > 1 else None
        if not h: return 1
        print(h["channel"], h["model"], h.get("effort", "-")); return 0
    if cmd == "call":
        name, brief, out, log = a[1:5]
        timeout = int(a[a.index("--timeout") + 1]) if "--timeout" in a else 900
        effort = a[a.index("--effort") + 1] if "--effort" in a else None
        h = registry().get(name)
        if not h or h["channel"] not in CALLS: print(f"not an openrouter/opencode head: {name}", file=sys.stderr); return 2
        with open(log, "w") as lf:
            st, p, c, r, cost = CALLS[h["channel"]](h, open(brief).read(), out, lf, timeout, "--web" in a, effort)
        print(*["-" if v is None else v for v in (p, c, r, cost)])
        return 0 if st == "ok" else 1
    print(__doc__, file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
