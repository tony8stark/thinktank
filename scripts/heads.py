#!/usr/bin/env python3
"""heads.py: the head registry and the channels shared by thinktank.sh and factcheck.py.

  available                         channels this machine can run, one per line (agent is always there)
  list                              every head: name, channel, model, effort, family, web, available
  model <name>                      "channel model effort" for one head (exit 1 if unknown), used by thinktank.sh
  call <name> <brief> <out> <log> [--timeout S] [--web] [--effort E] [--cwd DIR] [--queue-timeout S]
                                    run one openrouter, opencode, local or command head; prints "ptok ctok rtok cost"
  substitutes                       available command replacements: replaced name, head name
  agy-run --timeout S --log FILE --cwd DIR -- command...
                                    run agy with a post-answer hang guard
  agy-project <name> <folder> [--grant G ...]
                                    create or repair an agy project with exactly these grants
  update-check                      one line when a newer plugin version is out, else nothing; always exits 0

Registry: heads.json next to this script, merged with ~/.config/thinktank/heads.json (or $THINKTANK_HEADS):
same shape, a user entry replaces the default of that name, a null entry removes it.
OpenRouter needs OPENROUTER_API_KEY in the environment. OpenCode needs the `opencode` CLI with a provider set up."""
import sys, os, json, shutil, subprocess, signal, time, urllib.request, urllib.error
import argparse, re, tempfile, uuid, glob, urllib.parse
import ipaddress, math, hashlib, fcntl, threading, queue

HERE = os.path.dirname(os.path.abspath(__file__))
USER_REGISTRY = os.environ.get("THINKTANK_HEADS") or os.path.expanduser("~/.config/thinktank/heads.json")
CLI_CHANNELS = ("codex", "agy", "opencode")
CHANNELS = (*CLI_CHANNELS, "openrouter", "local", "command", "agent")
OPENROUTER_URL = os.environ.get("OPENROUTER_URL") or "https://openrouter.ai/api/v1/chat/completions"
UPDATE_URL = os.environ.get("THINKTANK_UPDATE_URL") or "https://raw.githubusercontent.com/tony8stark/thinktank/main/.claude-plugin/plugin.json"
UPDATE_EVERY, UPDATE_DEADLINE = 86400, 3
UPDATE_OFF = ("THINKTANK_NO_UPDATE_CHECK", "DISABLE_UPDATES", "DISABLE_AUTOUPDATER", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC")


class RegistryError(ValueError):
    pass


def local_url_problem(url, allow_lan_http=False):
    try:
        if not isinstance(url, str): return "base_url must be a string"
        u = urllib.parse.urlsplit(url)
        if u.scheme not in ("http", "https") or not u.hostname: return "base_url needs http or https and a host"
        if u.username is not None or u.password is not None or "?" in url or "#" in url:
            return "base_url must have no userinfo, query or fragment"
        u.port
        if u.scheme == "https": return None
        if u.hostname == "localhost": return None
        ip = ipaddress.ip_address(u.hostname)
        if ip.is_loopback: return None
        private = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7")
        if allow_lan_http and any(ip in ipaddress.ip_network(n) for n in private): return None
    except ValueError:
        pass
    return "http base_url requires localhost or a literal loopback IP (allow_lan_http permits private IPs)"


def argv_problem(argv):
    if not isinstance(argv, list) or not argv or any(not isinstance(v, str) or not v.strip() for v in argv):
        return "must be a non-empty list of non-empty strings"
    if not os.path.isabs(os.path.expanduser(argv[0])): return "executable must be an absolute path"
    return None


# keys a head's "params" may not override: the runner owns them (prompt, model, streaming, the context window and
# the answer limit that the truncation and length checks rely on)
RESERVED_PARAMS = {"openrouter": {"model", "messages", "stream"},
                   "openai": {"model", "messages", "stream", "max_tokens"},
                   "ollama": {"num_ctx", "num_predict"}}


def validate_extra(h, path):
    ch = h["channel"]
    if "params" in h:
        kind = h.get("backend") if ch == "local" else ch
        if kind not in RESERVED_PARAMS: return "params is only for openrouter and local heads"
        if not isinstance(h["params"], dict): return "params must be an object"
        clash = sorted(RESERVED_PARAMS[kind] & set(h["params"]))
        if clash: return "params may not set " + ", ".join(clash)
    if "replaces" in h and ch != "command": return "replaces is only allowed on command heads"
    if ch == "command":
        if path != USER_REGISTRY: return "command heads are only allowed in the user registry"
        for k in ("command", "status"):
            if k == "status" and k not in h: continue
            problem = argv_problem(h.get(k))
            if problem: return k + " " + problem
        if "replaces" in h and (not isinstance(h["replaces"], str) or not re.fullmatch(r"[a-z0-9][a-z0-9_-]*", h["replaces"])):
            return "invalid replaces"
    if ch == "local":
        if h.get("backend") not in ("ollama", "openai"): return "backend must be ollama or openai"
        for k in ("allow_lan_http", "check_context"):
            if k in h and not isinstance(h[k], bool): return k + " must be a bool"
        if h.get("backend") == "openai" and "base_url" not in h: return "base_url is required for openai"
        problem = local_url_problem(h.get("base_url", "http://127.0.0.1:11434"), h.get("allow_lan_http", False))
        if problem: return problem
        if "api_key_env" in h and (not isinstance(h["api_key_env"], str) or not re.fullmatch(r"[A-Z_][A-Z0-9_]*", h["api_key_env"])):
            return "invalid api_key_env"
        for k, low, high in (("context", 1024, None), ("max_output", 256, None), ("parallel", 1, 16)):
            if k in h and (type(h[k]) is not int or h[k] < low or (high and h[k] > high)):
                return k + " must be an integer " + (f"{low}..{high}" if high else f">= {low}")
        if h.get("web"): return "local heads have no web access"
    return None


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
        if path == USER_REGISTRY and any(isinstance(h, dict) and h.get("channel") == "command" for h in data["heads"].values()):
            st = os.stat(path)
            if st.st_uid != os.getuid() or st.st_mode & 0o022:
                raise RegistryError(f"{path}: refusing command heads: the file must be owned by you and not writable by others")
        for name, h in data["heads"].items():
            valid = re.fullmatch(r"[a-z0-9][a-z0-9_-]*", name)
            if not valid:
                raise RegistryError(f"{path}: invalid head {name!r}: invalid name")
            if h is None and path == USER_REGISTRY:
                heads.pop(name, None); continue
            if path == USER_REGISTRY and isinstance(h, dict) and h.get("channel") not in CHANNELS:
                # written for a newer plugin version (2026-10-29: a pre-switch session's copy failed the whole registry
                # on one unknown channel and then knew no heads at all): skip that head, keep the others
                print(f"warning: {path}: head {name!r} uses channel {h.get('channel')!r}, which this thinktank version "
                      f"does not know (update the plugin); skipped", file=sys.stderr)
                continue
            if (not isinstance(h, dict) or h.get("channel") not in CHANNELS
                or not isinstance(h.get("model"), str) or not h["model"].strip()
                or ("effort" in h and not isinstance(h["effort"], str))
                or ("web" in h and not isinstance(h["web"], bool))):
                raise RegistryError(f"{path}: invalid head {name!r}: invalid channel, model, effort or web")
            problem = validate_extra(h, path)
            if problem: raise RegistryError(f"{path}: invalid head {name!r}: {problem}")
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
    try:
        hs = registry().values()
        if any(h["channel"] == "local" for h in hs): chans.append("local")
        if any(h["channel"] == "command" and os.path.isfile(os.path.expanduser(h["command"][0]))
               and os.access(os.path.expanduser(h["command"][0]), os.X_OK) for h in hs): chans.append("command")
    except RegistryError: pass
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
    body.update(h.get("params") or {})   # per-head request fields (provider routing, web engine, temperature, ...)
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


def run_agy(cmd, timeout, logf, cwd, grace=None):
    """Stop a CLI that has delivered its JSON answer but keeps its session alive."""
    if grace is None:
        try: grace = float(os.environ.get("THINKTANK_AGY_GRACE", "30"))
        except ValueError: grace = 30
    grace = max(0, grace)
    p = subprocess.Popen(cmd, start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=cwd)
    pgfile = os.environ.get("THINKTANK_PGID")
    pgfile = pgfile + ".child" if pgfile else None
    events = queue.Queue()
    def reader(stream, kind):
        try:
            for line in stream: events.put((kind, line))
        finally:
            stream.close(); events.put((kind, None))
    threads = [threading.Thread(target=reader, args=(stream, kind), daemon=True)
               for stream, kind in ((p.stdout, "out"), (p.stderr, "err"))]
    def kill_group():
        try: os.killpg(p.pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError): pass
        time.sleep(.2)
        try: os.killpg(p.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError): pass
        p.wait()
    raw = []; ended = set(); answer_deadline = None
    deadline = time.monotonic() + timeout
    stopped = None; stopped_at = None
    try:
        if pgfile:
            with open(pgfile, "w") as f: f.write(str(p.pid) + "\n")
        for t in threads: t.start()
        while len(ended) < 2 or p.poll() is None:
            now = time.monotonic()
            if stopped is not None:
                stopped_at = stopped_at or now
                # a descendant that left the process group may hold a pipe open: do not wait for it forever
                if now - stopped_at > 5: break
            if stopped is None and now >= deadline:
                kill_group(); stopped = -1
                logf.write(f"TIMEOUT after {timeout}s\n")
            elif stopped is None and answer_deadline is not None and now >= answer_deadline:
                if p.poll() is None:
                    kill_group(); stopped = 0
                    logf.write(f"agy printed its answer but did not exit; stopped after {grace:g}s\n")
                else:
                    stopped = p.returncode
                    # An exited wrapper may leave descendants holding its pipes open.
                    if len(ended) < 2: kill_group()
            try: kind, line = events.get(timeout=.05)
            except queue.Empty: continue
            if line is None: ended.add(kind); continue
            logf.write(line); logf.flush()
            if kind == "out":
                raw.append(line)
                try: d = json.loads(line)
                except ValueError: continue
                if answer_deadline is None and isinstance(d, dict) and any(
                        isinstance(d.get(k), str) and d[k].strip() for k in ("response", "result")):
                    answer_deadline = time.monotonic() + grace
        return (stopped if stopped is not None else p.wait()), "".join(raw)
    except BaseException:
        kill_group(); raise
    finally:
        if pgfile:
            try: os.unlink(pgfile)
            except FileNotFoundError: pass


class NoLocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "redirect refused", headers, fp)


def call_local(h, prompt, out, logf, timeout, web=False, effort=None, cwd=None, queue_timeout=None):
    """Native local inference with endpoint slots and conservative prompt truncation checks."""
    error = ("error", None, None, None, None)
    try: os.unlink(out)
    except FileNotFoundError: pass
    def fail(reason):
        logf.write(reason + "\n"); return error
    if web: return fail("local heads have no web access")
    base = h.get("base_url", "http://127.0.0.1:11434").rstrip("/")
    problem = local_url_problem(base, h.get("allow_lan_http", False))
    if problem: return fail(problem)
    u = urllib.parse.urlsplit(base)
    host = u.hostname
    port = u.port if u.port is not None else (443 if u.scheme == "https" else 80)
    endpoint = f"{'[' + host + ']' if ':' in host else host}:{port}"
    key = hashlib.sha1(f"{u.scheme}://{endpoint}".encode()).hexdigest()
    headers = {"Content-Type": "application/json"}
    keyname = h.get("api_key_env")
    secret = os.environ.get(keyname, "") if keyname else ""
    if keyname:
        if not secret: return fail(f"api_key_env {keyname} is not set")
        headers["Authorization"] = "Bearer " + secret
    lockdir = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thinktank", "locks")
    os.makedirs(lockdir, mode=0o700, exist_ok=True)
    start = time.monotonic(); slot = None
    wait = 3 * timeout if queue_timeout is None else queue_timeout
    try:
        while slot is None:
            for i in range(h.get("parallel", 1)):
                f = open(os.path.join(lockdir, f"{key}.{i}"), "a")
                try: fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError: f.close(); continue
                slot = f; break
            elapsed = time.monotonic() - start
            if slot is not None: break
            if elapsed >= wait: return fail(f"no free slot on {endpoint} after {wait:g}s")
            time.sleep(min(.5, wait - elapsed))
        if elapsed > 1: logf.write(f"queued {elapsed:.1f}s for {endpoint}\n")
        deadline = time.monotonic() + timeout
        opener = urllib.request.build_opener(NoLocalRedirect())
        def post(path, body):
            remaining = deadline - time.monotonic()
            if remaining <= 0: raise TimeoutError("request timed out")
            req = urllib.request.Request(base + path, data=json.dumps(body).encode(), headers=headers, method="POST")
            with opener.open(req, timeout=remaining) as r:
                if 300 <= r.status < 400: raise ValueError("redirect refused")
                return json.load(r)
        lower = math.ceil(sum(ord(c) < 128 for c in prompt) / 6 + sum(ord(c) >= 128 for c in prompt) / 4)
        upper = math.ceil(len(prompt) / 1.5)
        maximum = h.get("max_output", 8192)
        eff = effort or h.get("effort", "-")
        num_ctx = None
        if h["backend"] == "ollama":
            try: shown = post("/api/show", {"model": h["model"]})
            except urllib.error.HTTPError as e:
                if e.code == 404: return fail(f"model {h['model']} not found on {endpoint} (ollama pull {h['model']})")
                raise
            info = shown.get("model_info", {}) if isinstance(shown, dict) else {}
            trained = next((v for k, v in info.items() if k.endswith(".context_length") and type(v) is int), None) if isinstance(info, dict) else None
            caps = [v for v in (trained, h.get("context")) if v is not None]
            cap = min(caps) if caps else None
            if cap is not None and lower + maximum > cap:
                return fail(f"brief needs at least ~{lower} tokens plus {maximum} for the answer; the model allows {cap}")
            num_ctx = math.ceil((upper + maximum + 256) / 1024) * 1024
            if cap is not None: num_ctx = min(num_ctx, cap)
            body = {"model": h["model"], "stream": False, "messages": [{"role": "user", "content": prompt}],
                    "options": {**(h.get("params") or {}), "num_ctx": num_ctx, "num_predict": maximum}}
            if eff in ("low", "medium", "high"): body["think"] = eff
            elif eff == "none": body["think"] = False
            d = post("/api/chat", body)
        else:
            body = {"model": h["model"], "stream": False, "messages": [{"role": "user", "content": prompt}], "max_tokens": maximum}
            if eff != "-": body["reasoning_effort"] = eff
            body.update(h.get("params") or {})
            d = post("/chat/completions", body)
        try:
            if not isinstance(d, dict): raise ValueError()
            if h["backend"] == "ollama":
                content = d["message"]["content"]; reason = d.get("done_reason")
                p, c, r = d.get("prompt_eval_count"), d.get("eval_count"), None
            else:
                choice = d["choices"][0]; content = choice["message"]["content"]; reason = choice.get("finish_reason")
                usage = d.get("usage") or {}
                p, c = usage.get("prompt_tokens"), usage.get("completion_tokens")
                r = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
            if not isinstance(content, str): raise ValueError()
            p, c, r = [v if type(v) is int and v >= 0 else None for v in (p, c, r)]
        except (KeyError, IndexError, TypeError, ValueError, AttributeError): return fail("invalid local response shape")
        # Only scalar metadata is logged, never provider-controlled response text or reasoning.
        logf.write(json.dumps({"model": h["model"], "prompt_tokens": p, "completion_tokens": c,
                               "reasoning_tokens": r, "reason": reason if reason in ("stop", "length") else None,
                               "num_ctx": num_ctx}) + "\n")
        # truncation first: a server that dropped part of the brief often also runs out of room for the answer
        if h.get("check_context", True):
            if p is None: return fail("server reported no prompt token count, so a truncated brief cannot be detected (set check_context false to accept)")
            if p < lower: return fail(f"context truncated: the server read {p} tokens, the brief has at least ~{lower}; raise the server's context (Ollama: OLLAMA_CONTEXT_LENGTH or the head's context field; LM Studio: lms load --context-length)")
        if reason == "length": return fail(f"answer cut before it finished ({c} tokens; the limit is max_output {maximum} or the server's context window)")
        if not content.strip(): return fail("empty local answer")
        with open(out, "w") as f: f.write(content.strip() + "\n")
        return "ok", p, c, r, None
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        if secret: detail = detail.replace(secret, "[redacted]")
        detail = detail[:300]
        return fail(f"HTTP {e.code}: {detail}")
    except (urllib.error.URLError, TimeoutError, OSError):
        return fail(f"cannot reach {endpoint}: connection failed or timed out")
    except (ValueError, TypeError): return fail("invalid local response")
    finally:
        if slot is not None:
            fcntl.flock(slot, fcntl.LOCK_UN); slot.close()


def call_command(h, prompt, out, logf, timeout, web=False, effort=None, cwd=None):
    error = ("error", None, None, None, None)
    try: os.unlink(out)
    except FileNotFoundError: pass
    out = os.path.abspath(out)
    argv = [os.path.expanduser(v) for v in h["command"]] + ["head", "--protocol", "1", "--mode",
            "factcheck" if web else "thinktank", "--model", h["model"], "--effort", effort or h.get("effort", "-"), "--out", out]
    with tempfile.TemporaryDirectory(prefix="thinktank-command-") as scratch:
        with open(os.path.join(scratch, "brief"), "w+") as brief, tempfile.TemporaryFile(mode="w+") as stdout:
            brief.write(prompt); brief.seek(0)
            try:
                rc = run_in_group(argv, timeout, stdin=brief, stdout=stdout, stderr=logf, cwd=cwd or scratch, text=True)
            except OSError:
                logf.write("cannot start command head\n"); return error
            stdout.seek(0); lines = stdout.read().splitlines()
    if rc != 0:
        logf.write(f"TIMEOUT after {timeout}s\n" if rc == -1 else "command head unavailable (exit 3)\n" if rc == 3 else f"command exit {rc}\n")
        try: os.unlink(out)
        except FileNotFoundError: pass
        return error
    if not os.path.isfile(out) or not os.path.getsize(out):
        logf.write("command produced no answer\n"); return error
    last = next((line.strip() for line in reversed(lines) if line.strip()), "")
    fields = last.split(); tokens = (None, None, None, None)
    if len(fields) == 4:
        try:
            tokens = tuple(None if v == "-" else (float(v) if i == 3 else int(v)) for i, v in enumerate(fields))
            if any(v is not None and not math.isfinite(v) for v in tokens): raise ValueError()
            logf.write(" ".join(fields) + "\n")
        except ValueError: tokens = (None, None, None, None)
    return ("ok", *tokens)


def substitutes():
    for name, h in registry().items():
        if h["channel"] != "command" or not h.get("replaces"): continue
        if "status" in h:
            try:
                rc = run_in_group([os.path.expanduser(v) for v in h["status"]], 30,
                                  stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except OSError: continue
            if rc != 0: continue
        yield h["replaces"], name


def parse_version(v):
    m = re.fullmatch(r"(\d{1,9})\.(\d{1,9})\.(\d{1,9})", v) if isinstance(v, str) else None
    return tuple(int(x) for x in m.groups()) if m else None


def latest_version():
    """The newest version on main, which is what `claude plugin update` installs: from the cache while it is under a day
    old, else one request. The attempt is recorded before the request, so neither a parallel run nor a failure repeats
    it today; where it cannot be recorded (OSError) there is no request at all."""
    base = os.path.join(os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache"), "thinktank")
    cache = os.path.join(base, "update-check.json")

    def save(latest):
        tmp = f"{cache}.{os.getpid()}"
        with open(tmp, "w") as f: json.dump({"checked": time.time(), "latest": latest}, f)
        os.replace(tmp, cache)

    os.makedirs(base, exist_ok=True)
    with open(cache + ".lock", "a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            with open(cache) as f: seen = json.load(f)
            known, age = seen.get("latest"), time.time() - seen["checked"]
        except Exception: known, age = None, UPDATE_EVERY
        if 0 <= age < UPDATE_EVERY: return known
        save(known)
    try:
        with urllib.request.urlopen(UPDATE_URL, timeout=UPDATE_DEADLINE) as r: known = json.loads(r.read(65536))["version"]
    except Exception: return known
    save(known)
    return known


def update_notice():
    if any(os.environ.get(v, "") not in ("", "0") for v in UPDATE_OFF): return None
    with open(os.path.join(HERE, "..", ".claude-plugin", "plugin.json")) as f: running = json.load(f)["version"]
    have = parse_version(running)
    if not have: return None
    latest = latest_version()
    new = parse_version(latest)
    if not new or new <= have: return None
    return (f"thinktank {latest} is available (running: {running}). Update: claude plugin marketplace update thinktank "
            "&& claude plugin update thinktank@thinktank, then restart Claude Code; if auto-update already fetched it, "
            "/reload-plugins is enough. Changes: https://github.com/tony8stark/thinktank/releases")


def update_check():
    """update_notice under one wall-clock deadline: a socket timeout covers neither DNS nor a response that trickles in.
    A daemon thread is simply abandoned at exit. Anything unexpected counts as no notice."""
    found = []

    def work():
        try: found.append(update_notice())
        except Exception: pass

    t = threading.Thread(target=work, daemon=True)
    t.start(); t.join(UPDATE_DEADLINE)
    return found[0] if found else None


CALLS = {"openrouter": call_openrouter, "opencode": call_opencode, "local": call_local, "command": call_command}


def _main(a):
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    cmd = a[0]
    if cmd == "update-check":
        line = update_check()
        if line: print(line)
        return 0
    if cmd == "substitutes":
        for old, new in substitutes(): print(old + "\t" + new)
        return 0
    if cmd == "agy-run":
        parser = argparse.ArgumentParser(prog="heads.py agy-run")
        parser.add_argument("--timeout", type=int, required=True)
        parser.add_argument("--log", required=True); parser.add_argument("--cwd", required=True)
        parser.add_argument("argv", nargs=argparse.REMAINDER)
        args = parser.parse_args(a[1:])
        argv = args.argv[1:] if args.argv[:1] == ["--"] else args.argv
        if args.timeout <= 0 or not argv: parser.error("positive timeout and command required")
        with open(args.log, "w") as lf: rc, _ = run_agy(argv, args.timeout, lf, args.cwd)
        return 124 if rc == -1 else rc
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
        parser.add_argument("--queue-timeout", type=int)
        args = parser.parse_args(a[1:])
        if args.timeout <= 0: parser.error("--timeout must be positive")
        if args.queue_timeout is not None and args.queue_timeout <= 0: parser.error("--queue-timeout must be positive")
        h = registry().get(args.name)
        if not h or h["channel"] not in CALLS: print(f"not a head heads.py runs (openrouter, opencode, local, command): {args.name}", file=sys.stderr); return 2
        kw = {"cwd": args.cwd} if h["channel"] in ("opencode", "command") else {}
        if h["channel"] == "local": kw["queue_timeout"] = args.queue_timeout
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
