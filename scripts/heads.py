#!/usr/bin/env python3
"""heads.py: the head registry and the channels shared by thinktank.sh and factcheck.py.

  available                         channels this machine can run, one per line (agent is always there)
  list                              every head: name, channel, model, effort, family, web, available
  model <name>                      "channel model effort" for one head (exit 1 if unknown), used by thinktank.sh
  call <name> <brief> <out> <log> [--timeout S] [--web] [--effort E] [--cwd DIR]
                                    run one openrouter or opencode head; prints "ptok ctok rtok cost"
  agy-project <name> <folder> [--grant G ...]
                                    create or repair an agy project with exactly these grants

Registry: heads.json next to this script, merged with ~/.config/thinktank/heads.json (or $THINKTANK_HEADS):
same shape, a user entry replaces the default of that name, a null entry removes it.
OpenRouter needs OPENROUTER_API_KEY in the environment. OpenCode needs the `opencode` CLI with a provider set up."""
import sys, os, json, shutil, subprocess, signal, time, urllib.request, urllib.error
import argparse, re, tempfile, uuid, glob, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
USER_REGISTRY = os.environ.get("THINKTANK_HEADS") or os.path.expanduser("~/.config/thinktank/heads.json")
CLI_CHANNELS = ("codex", "agy", "opencode")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"


class RegistryError(ValueError):
    pass


def registry():
    heads = {}
    for path in (os.path.join(HERE, "heads.json"), USER_REGISTRY):
        if path == USER_REGISTRY and not os.path.exists(path): continue
        try:
            with open(path) as f: data = json.load(f)
        except (OSError, ValueError) as e:
            raise RegistryError(f"{path}: invalid registry: {e}") from None
        if not isinstance(data, dict) or not isinstance(data.get("heads"), dict):
            raise RegistryError(f"{path}: heads must be an object")
        for name, h in data["heads"].items():
            valid = re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
            if not valid:
                raise RegistryError(f"{path}: invalid head {name!r}: invalid name")
            if h is None and path == USER_REGISTRY:
                heads.pop(name, None); continue
            if (not isinstance(h, dict) or h.get("channel") not in (*CLI_CHANNELS, "openrouter", "agent")
                or not isinstance(h.get("model"), str) or not h["model"].strip()
                or ("effort" in h and not isinstance(h["effort"], str))
                or ("web" in h and not isinstance(h["web"], bool))):
                raise RegistryError(f"{path}: invalid head {name!r}: invalid channel, model, effort or web")
            heads[name] = h
    return heads


def ensure_agy_project(name, grants, folder):
    """Ensure a scoped agy project with exactly the requested allow list."""
    projects = os.path.expanduser("~/.gemini/config/projects")
    if not os.path.isdir(projects): return "error", "agy not set up: projects directory missing"
    folder = os.path.abspath(os.path.expanduser(folder))
    try:
        matches = []
        for path in sorted(glob.glob(os.path.join(projects, "*.json"))):
            with open(path) as f: d = json.load(f)
            if isinstance(d, dict) and d.get("name") == name: matches.append((path, d))
        os.makedirs(folder, exist_ok=True)
        status = "present" if matches else "created"
        if not matches:
            pid = str(uuid.uuid4())
            matches = [(os.path.join(projects, pid + ".json"), {"id": pid, "name": name})]
        resources = {"resources": [{"folderUri": "file://" + folder}]}
        permissions = {"permissionGrants": {"allow": list(grants)}}
        for path, d in matches:
            if d.get("projectResources") == resources and d.get("permissionGrants") == permissions: continue
            if status != "created": status = "repaired"
            d.update(projectResources=resources, permissionGrants=permissions)
            with open(path, "w") as f: json.dump(d, f, indent=2)
        return status, f"agy project {name}: {status}"
    except (OSError, ValueError) as e:
        return "error", f"agy project {name}: {e}"


def run_in_group(cmd, timeout, **popen_kwargs):
    """Run in a new session; return exit code or -1 on timeout, killing descendants on timeout or exception."""
    p = subprocess.Popen(cmd, start_new_session=True, **popen_kwargs)
    pgfile = os.environ.get("THINKTANK_PGID")
    pgfile = pgfile + ".child" if pgfile else None
    def kill_group():
        try: os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError): pass
        time.sleep(.2)
        try: os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        p.wait()
    try:
        if pgfile:
            with open(pgfile, "w") as f: f.write(str(p.pid) + "\n")
        p.communicate(timeout=timeout)
        return p.returncode
    except subprocess.TimeoutExpired:
        kill_group(); return -1
    except BaseException:
        kill_group(); raise
    finally:
        if pgfile:
            try: os.unlink(pgfile)
            except FileNotFoundError: pass


def available():
    chans = [c for c in CLI_CHANNELS if shutil.which(c)]
    if os.environ.get("OPENROUTER_API_KEY"): chans.append("openrouter")
    return chans + ["agent"]


def call_openrouter(h, prompt, out, logf, timeout, web=False, effort=None):
    """One chat completion. Returns (status, ptok, ctok, rtok, cost)."""
    try:
        url = urllib.parse.urlsplit(OPENROUTER_URL)
        safe = url.scheme == "https" or (url.scheme == "http" and url.hostname in ("127.0.0.1", "localhost"))
        if not safe or not url.hostname or url.username or url.password: raise ValueError("unsafe URL")
        url.port
    except ValueError:
        logf.write("refusing unsafe OPENROUTER_URL\n"); return "error", None, None, None, None
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
    try:
        if not isinstance(d, dict) or not isinstance(d.get("choices"), list): raise ValueError()
        text = d["choices"][0]["message"]["content"]
        u = d["usage"]
        if not isinstance(text, str) or not isinstance(u, dict): raise ValueError()
        details = u.get("completion_tokens_details")
        if details is None: details = {}
        if not isinstance(details, dict): raise ValueError()
        rtok = details.get("reasoning_tokens")
    except (KeyError, IndexError, TypeError, ValueError):
        logf.write("invalid OpenRouter response shape\n"); return "error", None, None, None, None
    logf.write(json.dumps({k: d[k] for k in ("id", "model", "provider", "usage") if k in d}) + "\n")
    if not text.strip(): return "error", u.get("prompt_tokens"), u.get("completion_tokens"), rtok, u.get("cost")
    with open(out, "w") as f: f.write(text.strip() + "\n")
    return "ok", u.get("prompt_tokens"), u.get("completion_tokens"), rtok, u.get("cost")


def opencode_major():
    """Major version of the opencode CLI on PATH, or None when it cannot be read."""
    try:
        r = subprocess.run(["opencode", "--version"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired): return None
    m = re.search(r"(\d+)\.\d+\.\d+", r.stdout)  # "1.19.5" (v1) or "opencode v2.1.4" (v2)
    return int(m.group(1)) if r.returncode == 0 and m else None


def opencode_command(model, prompt, web):
    """(argv, extra env) that deny every tool except web search and fetch (when web), or None for an unknown version.
    v1 reads OPENCODE_PERMISSION. v2 ignores it: it takes ordered rules (last match wins) from OPENCODE_CONFIG_CONTENT,
    and only a --standalone server sees them; the shared background service keeps its own config."""
    tools = ("webfetch", "websearch") if web else ()
    major = opencode_major()
    if major == 1:
        perm = {"*": "deny", **{t: "allow" for t in tools}}
        return ["opencode", "run", "--model", model, prompt], {"OPENCODE_PERMISSION": json.dumps(perm)}
    if major == 2:
        rules = [{"action": "*", "resource": "*", "effect": "deny"}]
        rules += [{"action": t, "resource": "*", "effect": "allow"} for t in tools]
        return (["opencode", "run", "--standalone", "--model", model, prompt],
                {"OPENCODE_CONFIG_CONTENT": json.dumps({"permissions": rules}), "OPENCODE_DISABLE_PROJECT_CONFIG": "1"})
    return None


def call_opencode(h, prompt, out, logf, timeout, web=False, effort=None, cwd=None):
    """`opencode run` with every tool denied except web search and fetch (when web). Returns like call_openrouter."""
    command = opencode_command(h["model"], prompt, web)
    if command is None:
        logf.write("refusing to run: unknown OpenCode version, so its tools cannot be restricted (supported: 1.x, 2.x)\n")
        return "error", None, None, None, None
    cmd, extra = command
    env = dict(os.environ, **extra)
    with tempfile.TemporaryDirectory(prefix="thinktank-opencode-") as scratch, tempfile.TemporaryFile(mode="w+") as output:
        rc = run_in_group(cmd, timeout, stdin=subprocess.DEVNULL, stdout=output, stderr=logf,
                          cwd=cwd or scratch, env=env, text=True)
        output.seek(0); text = output.read()
    if rc == -1:
        logf.write(f"\nTIMEOUT after {timeout}s\n"); return "error", None, None, None, None
    if rc != 0 or not text.strip():
        logf.write(f"\nopencode exit {rc}\n"); return "error", None, None, None, None
    with open(out, "w") as f: f.write(text.strip() + "\n")
    return "ok", None, None, None, None


CALLS = {"openrouter": call_openrouter, "opencode": call_opencode}


def _main(a):
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    cmd = a[0]
    if cmd == "available":
        try: registry()
        except RegistryError as e: print(f"warning: {e}", file=sys.stderr)
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
    if cmd == "agy-project":
        parser = argparse.ArgumentParser(prog="heads.py agy-project")
        parser.add_argument("name"); parser.add_argument("folder")
        parser.add_argument("--grant", action="append", default=[])
        args = parser.parse_args(a[1:])
        status, message = ensure_agy_project(args.name, args.grant, args.folder)
        print(message); return 1 if status == "error" else 0
    if cmd == "call":
        parser = argparse.ArgumentParser(prog="heads.py call")
        for field in ("name", "brief", "out", "log"): parser.add_argument(field)
        parser.add_argument("--timeout", type=int, default=900)
        parser.add_argument("--effort"); parser.add_argument("--web", action="store_true")
        parser.add_argument("--cwd")
        args = parser.parse_args(a[1:])
        if args.timeout <= 0: parser.error("--timeout must be positive")
        h = registry().get(args.name)
        if not h or h["channel"] not in CALLS: print(f"not an openrouter/opencode head: {args.name}", file=sys.stderr); return 2
        kw = {"cwd": args.cwd} if h["channel"] == "opencode" else {}
        with open(args.log, "w") as lf, open(args.brief) as bf:
            st, p, c, r, cost = CALLS[h["channel"]](h, bf.read(), args.out, lf, args.timeout, args.web, args.effort, **kw)
        print(*["-" if v is None else v for v in (p, c, r, cost)])
        return 0 if st == "ok" else 1
    print(__doc__, file=sys.stderr); return 2


def main(a):
    try: return _main(a)
    except RegistryError as e:
        print(e, file=sys.stderr); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
