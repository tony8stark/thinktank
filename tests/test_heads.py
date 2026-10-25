"""heads.py: registry merge, channel detection, the openrouter and opencode channels, and thinktank.sh wiring.
No network and no real CLIs: openrouter talks to a local HTTP server, opencode and codex are fake scripts on PATH."""
import json, os, subprocess, sys, threading, http.server, importlib.util

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


def test_opencode_call_denies_tools_and_times_out(tmp_path):
    # the fake echoes the permission env it was given, so the test sees exactly what opencode would
    d = fake_bin(tmp_path, "opencode", 'if [ "$4" = "SLEEP" ]; then sleep 30; fi; echo "model=$3 perm=$OPENCODE_PERMISSION"')
    os.environ["PATH"] = d + ":" + os.environ["PATH"]
    m = load({"THINKTANK_HEADS": str(tmp_path / "none.json")})
    out = tmp_path / "a.md"
    with open(tmp_path / "a.log", "w") as lf:
        r = m.call_opencode({"model": "anthropic/claude"}, "hi", str(out), lf, 10, web=True, cwd=str(tmp_path))
    assert r[0] == "ok"
    text = out.read_text()
    assert "model=anthropic/claude" in text and '"*": "deny"' in text and '"webfetch": "allow"' in text
    with open(tmp_path / "b.log", "w") as lf:
        r = m.call_opencode({"model": "m"}, "SLEEP", str(tmp_path / "b.md"), lf, 1, cwd=str(tmp_path))
    assert r[0] == "error" and "TIMEOUT" in (tmp_path / "b.log").read_text()


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
