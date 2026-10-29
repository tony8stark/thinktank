"""heads.py: registry merge, channel detection, the openrouter and opencode channels, and thinktank.sh wiring.
No network and no real CLIs: openrouter talks to a local HTTP server, opencode and codex are fake scripts on PATH."""
import json, os, subprocess, sys, threading, http.server, importlib.util
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPTS = os.path.join(ROOT, "scripts")
RUNNER = os.path.join(SCRIPTS, "thinktank.sh")


def load(env):
    os.environ.update(env)
    spec = importlib.util.spec_from_file_location("heads", os.path.join(SCRIPTS, "heads.py"))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


def fake_bin(tmp_path, name, body):
    d = tmp_path / "bin"; d.mkdir(exist_ok=True)
    p = d / name; p.write_text("#!/bin/bash\n" + body + "\n"); p.chmod(0o755)
    return str(d)


def clean_path(tmp_path, *extra):
    """PATH with only the fakes plus the basics, so a real codex/agy/opencode on this machine never leaks in."""
    d = tmp_path / "sys"; d.mkdir(exist_ok=True)
    for b in ("python3", "bash", "perl", "env", "cat", "cut", "tr", "grep", "seq", "date", "mkdir", "cp", "rm",
              "dirname", "basename", "sed", "head", "find", "sleep"):
        src = subprocess.run(["/bin/bash", "-c", f"command -v {b}"], capture_output=True, text=True).stdout.strip()
        if src and not (d / b).exists(): os.symlink(src, d / b)
    return ":".join([*extra, str(d)])


def test_user_registry_overrides_adds_and_removes(tmp_path):
    user = tmp_path / "heads.json"
    user.write_text(json.dumps({"heads": {"sol": {"channel": "codex", "model": "x", "effort": "high"},
                                          "astra": None,
                                          "oc-claude": {"channel": "opencode", "model": "anthropic/claude", "web": True}}}))
    h = load({"THINKTANK_HEADS": str(user)}).registry()
    assert h["sol"]["model"] == "x" and "astra" not in h and h["oc-claude"]["channel"] == "opencode"
    assert h["opus"]["channel"] == "agent"


def test_available_follows_path_and_key(tmp_path):
    env = dict(os.environ, PATH=clean_path(tmp_path, fake_bin(tmp_path, "opencode", "exit 0")))
    env.pop("OPENROUTER_API_KEY", None)
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "heads.py"), "available"], env=env,
                         capture_output=True, text=True).stdout.split()
    assert out == ["opencode", "agent"]
    env["OPENROUTER_API_KEY"] = "test"
    out = subprocess.run([sys.executable, os.path.join(SCRIPTS, "heads.py"), "available"], env=env,
                         capture_output=True, text=True).stdout.split()
    assert out == ["opencode", "openrouter", "agent"]


class FakeOpenRouter(http.server.BaseHTTPRequestHandler):
    seen = []
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOpenRouter.seen.append((self.headers.get("Authorization"), body))
        if body["model"] == "bad/model":
            self.send_response(400); self.end_headers(); self.wfile.write(b'{"error":"no such model"}'); return
        resp = {"choices": [{"message": {"content": "PONG"}}],
                "usage": {"prompt_tokens": 11, "completion_tokens": 3, "cost": 0.0001,
                          "completion_tokens_details": {"reasoning_tokens": 2}}}
        self.send_response(200); self.send_header("Content-Type", "application/json"); self.end_headers()
        self.wfile.write(json.dumps(resp).encode())
    def log_message(self, *a): pass


def serve():
    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeOpenRouter)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"


def test_openrouter_call_sends_effort_web_and_key(tmp_path):
    srv, url = serve()
    try:
        m = load({"OPENROUTER_URL": url, "OPENROUTER_API_KEY": "k-test", "THINKTANK_HEADS": str(tmp_path / "none.json")})
        m.OPENROUTER_URL = url
        out = tmp_path / "a.md"
        with open(tmp_path / "a.log", "w") as lf:
            r = m.call_openrouter({"model": "openai/x", "effort": "low"}, "hi", str(out), lf, 10, web=True)
        assert r == ("ok", 11, 3, 2, 0.0001) and out.read_text() == "PONG\n"
        auth, body = FakeOpenRouter.seen[-1]
        assert auth == "Bearer k-test" and body["reasoning"] == {"effort": "low"} and body["plugins"] == [{"id": "web"}]
        with open(tmp_path / "b.log", "w") as lf:
            r = m.call_openrouter({"model": "bad/model", "effort": "-"}, "hi", str(tmp_path / "b.md"), lf, 10)
        assert r[0] == "error" and not (tmp_path / "b.md").exists() and "HTTP 400" in (tmp_path / "b.log").read_text()
        assert "reasoning" not in FakeOpenRouter.seen[-1][1]
    finally:
        srv.shutdown()


def fake_opencode(tmp_path, version):
    """A fake opencode that prints VERSION for --version and otherwise echoes its argv and permission env as JSON."""
    return fake_bin(tmp_path, "opencode", f"""[ "$1" = --version ] && {{ echo "{version}"; exit 0; }}
for a in "$@"; do [ "$a" = SLEEP ] && sleep 30; done
exec {sys.executable} -c 'import json,os,sys; print(json.dumps({{"argv": sys.argv[1:], "perm": os.environ.get("OPENCODE_PERMISSION"),
  "content": os.environ.get("OPENCODE_CONFIG_CONTENT"), "noproject": os.environ.get("OPENCODE_DISABLE_PROJECT_CONFIG")}}))' "$@" """)


def opencode_answer(m, tmp_path, web, prompt="hi", timeout=10):
    out, log = tmp_path / "a.md", tmp_path / "a.log"
    if out.exists(): out.unlink()
    with open(log, "w") as lf:
        r = m.call_opencode({"model": "ollama/gemma"}, prompt, str(out), lf, timeout, web=web, cwd=str(tmp_path))
    return r[0], (json.loads(out.read_text()) if out.exists() else None), log.read_text()


def test_opencode_v1_denies_tools_through_opencode_permission(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_opencode(tmp_path, "1.19.5") + ":" + os.environ["PATH"])
    m = load({"THINKTANK_HEADS": str(tmp_path / "none.json")})
    status, seen, _ = opencode_answer(m, tmp_path, web=True)
    assert status == "ok" and seen["argv"] == ["run", "--model", "ollama/gemma", "hi"]
    assert json.loads(seen["perm"]) == {"*": "deny", "webfetch": "allow", "websearch": "allow"}
    status, seen, _ = opencode_answer(m, tmp_path, web=False)
    assert json.loads(seen["perm"]) == {"*": "deny"}


def test_opencode_v2_denies_tools_through_config_on_a_private_server(tmp_path, monkeypatch):
    # live check 2026-10-29, OpenCode v2.1.4: OPENCODE_PERMISSION was ignored and the head wrote files and ran shell;
    # these rules on a --standalone server removed every tool even when the user's global config allowed all
    monkeypatch.setenv("PATH", fake_opencode(tmp_path, "opencode v2.1.4") + ":" + os.environ["PATH"])
    m = load({"THINKTANK_HEADS": str(tmp_path / "none.json")})
    status, seen, _ = opencode_answer(m, tmp_path, web=False)
    assert status == "ok" and seen["argv"] == ["run", "--standalone", "--model", "ollama/gemma", "hi"]
    assert json.loads(seen["content"]) == {"permissions": [{"action": "*", "resource": "*", "effect": "deny"}]}
    assert seen["noproject"] == "1"
    status, seen, _ = opencode_answer(m, tmp_path, web=True)
    assert [r["action"] for r in json.loads(seen["content"])["permissions"]] == ["*", "webfetch", "websearch"]
    assert [r["effect"] for r in json.loads(seen["content"])["permissions"]] == ["deny", "allow", "allow"]


@pytest.mark.parametrize("version", ["opencode v3.0.0", "dev build", ""])
def test_opencode_unknown_version_is_refused(tmp_path, monkeypatch, version):
    monkeypatch.setenv("PATH", fake_opencode(tmp_path, version) + ":" + os.environ["PATH"])
    m = load({"THINKTANK_HEADS": str(tmp_path / "none.json")})
    status, seen, log = opencode_answer(m, tmp_path, web=False)
    assert status == "error" and seen is None and "unknown OpenCode version" in log


def test_opencode_call_times_out(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", fake_opencode(tmp_path, "opencode v2.1.4") + ":" + os.environ["PATH"])
    m = load({"THINKTANK_HEADS": str(tmp_path / "none.json")})
    status, seen, log = opencode_answer(m, tmp_path, web=False, prompt="SLEEP", timeout=1)
    assert status == "error" and seen is None and "TIMEOUT" in log


def test_runner_mixes_codex_and_openrouter_heads(tmp_path):
    srv, url = serve()
    try:
        codex = fake_bin(tmp_path, "codex", 'while [ $# -gt 0 ]; do [ "$1" = "-o" ] && { echo CODEX-ANSWER > "$2"; }; shift; done')
        env = dict(os.environ, PATH=clean_path(tmp_path, codex), OPENROUTER_API_KEY="k", OPENROUTER_URL=url,
                   THINKTANK_HEADS=str(tmp_path / "none.json"))
        brief = tmp_path / "brief.md"; brief.write_text("say pong\n")
        r = subprocess.run(["bash", RUNNER, str(tmp_path / "run"), str(brief), "--heads", "sol:1,or-gemini:1,opus:1"],
                           env=env, capture_output=True, text=True, timeout=60)
        assert "THINKTANK DONE ok=2 error=0" in r.stdout, r.stdout + r.stderr
        meta = json.load(open(tmp_path / "run/out/or-gemini-1.meta.json"))
        assert meta["status"] == "ok" and meta["channel"] == "openrouter" and meta["prompt_tok"] == 11
        assert meta["cost_usd"] == 0.0001
        assert (tmp_path / "run/out/sol-1.md").read_text().strip() == "CODEX-ANSWER"
        r = subprocess.run(["bash", RUNNER, str(tmp_path / "r2"), str(brief), "--heads", "nope:1"], env=env,
                           capture_output=True, text=True)
        assert r.returncode == 2 and "known:" in r.stderr and "or-gemini" in r.stderr
    finally:
        srv.shutdown()


def test_runner_available_without_any_cli(tmp_path):
    env = dict(os.environ, PATH=clean_path(tmp_path)); env.pop("OPENROUTER_API_KEY", None)
    r = subprocess.run(["bash", RUNNER, "--available"], env=env, capture_output=True, text=True)
    assert r.stdout.split() == ["agent"]


def test_factcheck_runs_an_openrouter_head_with_web(tmp_path):
    srv, url = serve()
    try:
        env = dict(os.environ, PATH=clean_path(tmp_path), OPENROUTER_API_KEY="k", OPENROUTER_URL=url,
                   THINKTANK_HEADS=str(tmp_path / "none.json"))
        fc = [sys.executable, os.path.join(SCRIPTS, "factcheck.py")]
        src = tmp_path / "in.md"; src.write_text("Paris is the capital of France.\n")
        run = tmp_path / "run"
        subprocess.run(fc + ["init", str(run), str(src)], env=env, check=True, capture_output=True)
        (run / "claims.json").write_text(json.dumps([{"id": "C1", "para": "¶1", "stakes": "low",
            "span": "Paris is the capital of France", "claim": "Paris is the capital of France"}]))
        subprocess.run(fc + ["prep", str(run), "--heads", "or-gpt:1"], env=env, check=True, capture_output=True)
        assert "verbatim" in (run / "briefs/or-gpt-1.md").read_text()
        r = subprocess.run(fc + ["run", str(run)], env=env, capture_output=True, text=True, timeout=60)
        assert "FACTCHECK DONE ok=1 error=0" in r.stdout, r.stdout + r.stderr
        assert FakeOpenRouter.seen[-1][1]["plugins"] == [{"id": "web"}]
        meta = json.load(open(run / "out/or-gpt-1.meta.json"))
        assert meta["channel"] == "openrouter" and meta["cost_usd"] == 0.0001
    finally:
        srv.shutdown()


@pytest.fixture
def local_heads(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("THINKTANK_HEADS", str(tmp_path / "registry.json"))
    return load({})


def user_heads(m, entries):
    with open(m.USER_REGISTRY, "w") as f: json.dump({"heads": entries}, f)
    os.chmod(m.USER_REGISTRY, 0o600)


@pytest.mark.parametrize("backend", ["ollama", "openai"])
def test_local_registry_valid(local_heads, backend):
    h = dict(channel="local", backend=backend, model="gemma", base_url="https://inference.example/v1",
             api_key_env="LOCAL_KEY", context=4096, max_output=256, parallel=2, check_context=False, allow_lan_http=False)
    user_heads(local_heads, {"local-test": h})
    assert local_heads.registry()["local-test"] == h


@pytest.mark.parametrize("fields,reason", [
    ({"backend": "bad"}, "backend"), ({"backend": "openai"}, "base_url"),
    ({"base_url": 4}, "base_url"), ({"api_key_env": "bad-key"}, "api_key_env"),
    ({"context": True}, "context"), ({"context": 1023}, "context"),
    ({"max_output": True}, "max_output"), ({"max_output": 255}, "max_output"),
    ({"parallel": True}, "parallel"), ({"parallel": 0}, "parallel"), ({"parallel": 17}, "parallel"),
    ({"check_context": 1}, "check_context"), ({"allow_lan_http": 1}, "allow_lan_http"),
    ({"web": True}, "local heads have no web access"), ({"replaces": "sol"}, "replaces"),
])
def test_local_registry_invalid(local_heads, fields, reason):
    user_heads(local_heads, {"bad": dict(channel="local", model="m", backend="ollama", **{}) | fields})
    with pytest.raises(local_heads.RegistryError, match=reason): local_heads.registry()


@pytest.mark.parametrize("url,lan,ok", [
    ("https://example.com/v1", False, True), ("http://localhost", False, True),
    ("http://127.0.0.5", False, True), ("http://[::1]", False, True),
    ("http://192.168.1.20", False, False), ("http://192.168.1.20", True, True),
    ("http://10.0.0.1", True, True), ("http://172.16.0.1", True, True), ("http://[fc00::1]", True, True),
    ("http://169.254.169.254", True, False), ("http://[fe80::1]", True, False),
    ("http://0.0.0.0", True, False), ("http://[::]", True, False),
    ("http://example.com", True, False), ("https://user:pass@example.com", False, False),
    ("http://localhost?q=1", False, False), ("http://localhost#x", False, False),
    ("file:///tmp/x", False, False), ("http://localhost:bad", False, False),
])
def test_local_urls(local_heads, url, lan, ok):
    assert (local_heads.local_url_problem(url, lan) is None) == ok
    user_heads(local_heads, {"url": dict(channel="local", model="m", backend="ollama", base_url=url, allow_lan_http=lan)})
    if ok: assert "url" in local_heads.registry()
    else:
        with pytest.raises(local_heads.RegistryError): local_heads.registry()


def test_command_registry_security(local_heads, tmp_path, monkeypatch):
    h = dict(channel="command", model="m", command=[sys.executable], status=[sys.executable, "-c", "pass"], replaces="sol")
    user_heads(local_heads, {"cmd": h})
    assert local_heads.registry()["cmd"] == h
    os.chmod(local_heads.USER_REGISTRY, 0o620)
    with pytest.raises(local_heads.RegistryError, match="refusing command heads"): local_heads.registry()
    os.chmod(local_heads.USER_REGISTRY, 0o600)
    monkeypatch.setattr(local_heads.os, "getuid", lambda: -1)
    with pytest.raises(local_heads.RegistryError, match="refusing command heads"): local_heads.registry()


@pytest.mark.parametrize("field,value", [("command", []), ("command", [""]), ("command", ["relative"]),
    ("command", "bad"), ("status", ["relative"]), ("replaces", "Bad")])
def test_command_invalid(local_heads, field, value):
    h = dict(channel="command", model="m", command=[sys.executable]); h[field] = value
    user_heads(local_heads, {"cmd": h})
    with pytest.raises(local_heads.RegistryError, match=field): local_heads.registry()


def test_shipped_command_refused(local_heads, tmp_path, monkeypatch):
    shipped = tmp_path / "shipped"; shipped.mkdir()
    (shipped / "heads.json").write_text(json.dumps({"heads": {"bad": dict(channel="command", model="m", command=[sys.executable])}}))
    monkeypatch.setattr(local_heads, "HERE", str(shipped))
    with pytest.raises(local_heads.RegistryError, match="only allowed in the user registry"): local_heads.registry()


def test_new_available_no_network(local_heads, tmp_path, monkeypatch):
    monkeypatch.setattr(local_heads.shutil, "which", lambda n: n if n == "codex" else None)
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setattr(local_heads.urllib.request, "urlopen", lambda *a, **kw: pytest.fail("network called"))
    assert local_heads.available() == ["codex", "agent"]
    user_heads(local_heads, {"loc": dict(channel="local", model="m", backend="ollama"),
                             "cmd": dict(channel="command", model="m", command=[sys.executable])})
    assert local_heads.available() == ["codex", "local", "command", "agent"]
    user_heads(local_heads, {"cmd": dict(channel="command", model="m", command=[str(tmp_path / "missing")])})
    assert local_heads.available() == ["codex", "agent"]


@pytest.fixture
def local_server(request, monkeypatch):
    import time
    class Handler(http.server.BaseHTTPRequestHandler):
        seen = []; intervals = []; delay = 0; show_status = 200; redirect = False
        show = {"model_info": {"gemma.context_length": 32768}}
        response = None
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            self.seen.append((self.path, self.headers.get("Authorization"), body))
            if self.path.endswith("/api/show"):
                self.send_response(self.show_status); self.end_headers(); self.wfile.write(json.dumps(self.show).encode()); return
            if self.redirect:
                self.send_response(302); self.send_header("Location", "/target"); self.end_headers(); return
            start = time.monotonic(); time.sleep(self.delay)
            response = self.response
            if response is None:
                response = ({"message": {"content": " PONG ", "thinking": "private"}, "prompt_eval_count": 100,
                             "eval_count": 3, "done_reason": "stop"} if self.path.endswith("/api/chat") else
                            {"choices": [{"message": {"content": "PONG", "reasoning": "private"}, "finish_reason": "stop"}],
                             "usage": {"prompt_tokens": 100, "completion_tokens": 3,
                                       "completion_tokens_details": {"reasoning_tokens": 2}}})
            self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(response).encode())
            self.intervals.append((start, time.monotonic()))
        def log_message(self, *a): pass
    if getattr(request, "param", None) == "mock":
        import io, urllib.error
        from types import SimpleNamespace
        class Response(io.StringIO):
            status = 200
        def post(req, timeout):
            body = json.loads(req.data)
            path = req.selector
            Handler.seen.append((path, req.get_header("Authorization"), body))
            if path.endswith("/api/show"):
                if Handler.show_status != 200:
                    raise urllib.error.HTTPError(req.full_url, Handler.show_status, "test", {}, io.BytesIO(b"missing"))
                return Response(json.dumps(Handler.show))
            if Handler.redirect:
                raise urllib.error.HTTPError(req.full_url, 302, "redirect refused", {}, io.BytesIO(b""))
            start = time.monotonic(); time.sleep(Handler.delay)
            d = Handler.response
            if d is None:
                d = ({"message": {"content": "PONG", "thinking": "private"}, "prompt_eval_count": 100,
                      "eval_count": 3, "done_reason": "stop"} if path.endswith("/api/chat") else
                     {"choices": [{"message": {"content": "PONG", "reasoning": "private"}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 3,
                                "completion_tokens_details": {"reasoning_tokens": 2}}})
            Handler.intervals.append((start, time.monotonic()))
            return Response(json.dumps(d))
        import urllib.request
        monkeypatch.setattr(urllib.request, "build_opener", lambda *a: SimpleNamespace(open=post))
        yield Handler, "http://127.0.0.1:12345"
        return
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try: yield Handler, f"http://127.0.0.1:{srv.server_port}"
    finally: srv.shutdown(); srv.server_close()


def local_call(m, tmp_path, h, prompt="brief", **kw):
    import io
    out = tmp_path / "answer"; out.write_text("stale")
    log = io.StringIO()
    result = m.call_local(h, prompt, out, log, 5, **kw)
    return result, out, log.getvalue()


@pytest.mark.parametrize("backend", ["ollama", "openai"])
@pytest.mark.parametrize("auth", [False, True])
@pytest.mark.parametrize("local_server", ["http", "mock"], indirect=True)
def test_local_success(local_heads, local_server, tmp_path, monkeypatch, backend, auth):
    handler, base = local_server
    h = dict(model="gemma", backend=backend, base_url=base, max_output=256)
    if auth: h["api_key_env"] = "LOCAL_TEST_KEY"; monkeypatch.setenv("LOCAL_TEST_KEY", "test-only")
    result, out, log = local_call(local_heads, tmp_path, h, effort="low")
    assert result == ("ok", 100, 3, None if backend == "ollama" else 2, None)
    assert out.read_text() == "PONG\n" and "private" not in log and "PONG" not in log and "test-only" not in log
    _, authorization, body = handler.seen[-1]
    assert authorization == ("Bearer test-only" if auth else None)
    if backend == "ollama":
        assert body["options"] == {"num_ctx": 1024, "num_predict": 256} and body["think"] == "low"
    else:
        assert body["max_tokens"] == 256 and body["reasoning_effort"] == "low"
        assert not any(k in body for k in ("usage", "plugins", "reasoning"))


@pytest.mark.parametrize("backend", ["ollama", "openai"])
@pytest.mark.parametrize("problem,needle", [("truncated", "context truncated"), ("usage", "no prompt token count"),
    ("length", "answer cut before it finished"), ("empty", "empty local answer"), ("shape", "invalid local response shape"),
    ("truncated+length", "context truncated")])
@pytest.mark.parametrize("local_server", ["http", "mock"], indirect=True)
def test_local_bad_responses(local_heads, local_server, tmp_path, backend, problem, needle):
    handler, base = local_server
    content = "" if problem == "empty" else "answer"
    # live 2026-10-29: Ollama /v1 on a 4k window read 2,051 of 16,031 tokens and stopped on "length"
    tokens = 0 if problem.startswith("truncated") else None if problem == "usage" else 100
    reason = "length" if problem.endswith("length") else "stop"
    handler.response = ({"message": {"content": content, "thinking": "reasoning"}, "prompt_eval_count": tokens,
                         "eval_count": 3, "done_reason": reason} if backend == "ollama" else
                        {"choices": [{"message": {"content": content, "reasoning_content": "reasoning"}, "finish_reason": reason}],
                         "usage": {"prompt_tokens": tokens, "completion_tokens": 3}})
    if problem == "shape": handler.response = []
    h = dict(model="gemma", backend=backend, base_url=base, max_output=256)
    result, out, log = local_call(local_heads, tmp_path, h)
    assert result[0] == "error" and not out.exists() and needle in log
    if problem == "usage":
        result, out, log = local_call(local_heads, tmp_path, dict(h, check_context=False))
        assert result[0] == "ok" and out.exists()


@pytest.mark.parametrize("local_server", ["http", "mock"], indirect=True)
def test_local_preflight_and_redirect(local_heads, local_server, tmp_path, monkeypatch):
    handler, base = local_server
    h = dict(model="gemma", backend="ollama", base_url=base, max_output=256)
    handler.show_status = 404
    assert "model gemma not found" in local_call(local_heads, tmp_path, h)[2]
    handler.show_status = 200; handler.show = {"model_info": {"m.context_length": 256}}
    assert "brief needs at least" in local_call(local_heads, tmp_path, h)[2]
    assert not any(path == "/api/chat" for path, _, _ in handler.seen)
    handler.redirect = True
    result, out, log = local_call(local_heads, tmp_path, dict(h, backend="openai"))
    assert result[0] == "error" and "HTTP 302" in log and not out.exists()
    assert not any(path == "/target" for path, _, _ in handler.seen)
    monkeypatch.delenv("MISSING_LOCAL_KEY", raising=False)
    assert "api_key_env MISSING_LOCAL_KEY is not set" in local_call(local_heads, tmp_path, dict(h, api_key_env="MISSING_LOCAL_KEY"))[2]
    assert "local heads have no web access" in local_call(local_heads, tmp_path, h, web=True)[2]


@pytest.mark.parametrize("parallel", [1, 2])
@pytest.mark.parametrize("local_server", ["http", "mock"], indirect=True)
def test_local_endpoint_slots(local_heads, local_server, tmp_path, parallel):
    import io
    from concurrent.futures import ThreadPoolExecutor
    handler, base = local_server; handler.delay = .7
    h = dict(model="gemma", backend="openai", base_url=base, parallel=parallel)
    def call(i): return local_heads.call_local(h, "brief", tmp_path / str(i), io.StringIO(), 5)
    with ThreadPoolExecutor(2) as ex: results = list(ex.map(call, range(2)))
    assert all(r[0] == "ok" for r in results)
    a, b = sorted(handler.intervals)
    assert (b[0] < a[1]) == (parallel == 2)


@pytest.mark.parametrize("local_server", ["http", "mock"], indirect=True)
def test_local_queue_timeout(local_heads, local_server, tmp_path):
    import io, time
    from concurrent.futures import ThreadPoolExecutor
    handler, base = local_server; handler.delay = 1
    h = dict(model="gemma", backend="openai", base_url=base)
    with ThreadPoolExecutor(1) as ex:
        first = ex.submit(local_heads.call_local, h, "brief", tmp_path / "one", io.StringIO(), 5)
        deadline = time.monotonic() + 3
        while not handler.seen and time.monotonic() < deadline: time.sleep(.01)
        log = io.StringIO()
        assert local_heads.call_local(h, "brief", tmp_path / "two", log, 5, queue_timeout=.1)[0] == "error"
        assert "no free slot" in log.getvalue() and first.result()[0] == "ok"


def test_runner_mixes_local_command_codex(local_heads, local_server, tmp_path):
    from test_runner_hardening import command_script
    handler, base = local_server
    cmd = command_script(tmp_path, "import sys\na=sys.argv\nassert a[a.index('--mode')+1]=='thinktank'\nopen(a[a.index('--out')+1],'w').write(sys.stdin.read())\nprint('4 2 - -')")
    user_heads(local_heads, {"loc": dict(channel="local", model="gemma", backend="openai", base_url=base),
                             "cmd": dict(channel="command", model="m", command=[cmd])})
    codex = fake_bin(tmp_path, "codex", 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo ok > "$2"; shift; done')
    env = dict(os.environ, PATH=clean_path(tmp_path, codex))
    brief = tmp_path / 'brief'; brief.write_text('brief')
    r = subprocess.run(['bash', RUNNER, str(tmp_path / 'run'), str(brief), '--heads', 'sol,loc,cmd'],
                       env=env, capture_output=True, text=True, timeout=20)
    assert r.returncode == 0 and 'THINKTANK DONE ok=3 error=0' in r.stdout, r.stdout + r.stderr
    for name, channel in [('sol', 'codex'), ('loc', 'local'), ('cmd', 'command')]:
        assert json.loads((tmp_path / f'run/out/{name}-1.meta.json').read_text())['channel'] == channel


@pytest.mark.parametrize("effort,want", [("none", False), ("-", "omit"), ("high", "high")])
@pytest.mark.parametrize("local_server", ["mock"], indirect=True)
def test_local_context_and_effort(local_heads, local_server, tmp_path, effort, want):
    handler, base = local_server
    h = dict(model="gemma", backend="ollama", base_url=base, context=2048, max_output=256, effort=effort)
    result, out, log = local_call(local_heads, tmp_path, h, prompt="ї" * 100 + "a" * 100)
    assert result[0] == "ok"
    body = handler.seen[-1][2]
    assert body['options']['num_ctx'] == 1024
    if want == 'omit': assert 'think' not in body
    else: assert body['think'] == want


def test_params_validation(tmp_path):
    user = tmp_path / "heads.json"
    def reg(entry):
        user.write_text(json.dumps({"heads": {"x": entry}}))
        return load({"THINKTANK_HEADS": str(user)})
    or_head = {"channel": "openrouter", "model": "a/b", "params": {"provider": {"order": ["x"]}, "temperature": 0.2}}
    assert reg(or_head).registry()["x"]["params"]["temperature"] == 0.2
    for entry, reason in [(dict(or_head, params={"model": "other"}), "may not set model"),
                          (dict(or_head, params=[1]), "must be an object"),
                          ({"channel": "codex", "model": "m", "params": {}}, "only for openrouter and local"),
                          ({"channel": "local", "backend": "ollama", "model": "m", "params": {"num_ctx": 9}}, "may not set num_ctx"),
                          ({"channel": "local", "backend": "openai", "base_url": "http://127.0.0.1:1/v1", "model": "m",
                            "params": {"max_tokens": 9}}, "may not set max_tokens")]:
        with pytest.raises(Exception, match=reason): reg(entry).registry()


def test_openrouter_params_reach_the_request(tmp_path):
    srv, url = serve()
    try:
        m = load({"OPENROUTER_URL": url, "OPENROUTER_API_KEY": "k", "THINKTANK_HEADS": str(tmp_path / "none.json")})
        m.OPENROUTER_URL = url
        h = {"model": "openai/x", "effort": "-", "params": {"plugins": [{"id": "web", "engine": "native"}], "temperature": 0.1}}
        with open(tmp_path / "a.log", "w") as lf:
            assert m.call_openrouter(h, "hi", str(tmp_path / "a.md"), lf, 10, web=True)[0] == "ok"
        body = FakeOpenRouter.seen[-1][1]
        assert body["plugins"] == [{"id": "web", "engine": "native"}] and body["temperature"] == 0.1 and body["model"] == "openai/x"
    finally:
        srv.shutdown()


@pytest.mark.parametrize("backend", ["ollama", "openai"])
@pytest.mark.parametrize("local_server", ["mock"], indirect=True)
def test_local_params_reach_the_request(local_heads, local_server, tmp_path, backend):
    handler, base = local_server
    h = dict(model="gemma", backend=backend, base_url=base, max_output=256, params={"temperature": 0.3})
    assert local_call(local_heads, tmp_path, h)[0][0] == "ok"
    body = handler.seen[-1][2]
    if backend == "ollama": assert body["options"]["temperature"] == 0.3 and body["options"]["num_predict"] == 256
    else: assert body["temperature"] == 0.3 and body["max_tokens"] == 256
