"""Offline FactCheck regressions, fake CLIs and isolated run directories."""
import importlib.util, io, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "factcheck.py")


@pytest.fixture
def fc(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("THINKTANK_HEADS", str(tmp_path / "registry.json"))
    monkeypatch.setenv("FACTCHECK_LEDGER", str(tmp_path / "ledger.jsonl"))
    bindir = tmp_path / "bin"; bindir.mkdir()
    monkeypatch.setenv("PATH", str(bindir))
    spec = importlib.util.spec_from_file_location("fc_hardening", SCRIPT)
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)
    monkeypatch.setattr(m.headreg, "USER_REGISTRY", str(tmp_path / "registry.json"))
    monkeypatch.setattr(m, "resolve_host", lambda host: ["93.184.216.34"], raising=False)
    return m


def fake(tmp_path, name, body):
    p = tmp_path / "bin" / name
    p.write_text("#!" + sys.executable + "\n" + body + "\n"); p.chmod(0o755)
    return p


def run_dir(tmp_path, fc, spec="sol:2", claims=None):
    run = tmp_path / "run"; run.mkdir(exist_ok=True)
    for d in ("out", "log", "tmp", "briefs"): (run / d).mkdir(exist_ok=True)
    (run / "source.md").write_text("[¶1] The fee is seven dollars.\n\n")
    (run / "source.orig").write_text("Original text")
    (run / "context.md").write_text("Test context")
    claims = claims or [{"id": "C1", "span": "The fee is seven dollars.", "claim": "fee", "stakes": "standard"}]
    fc.jdump(claims, run / "claims.json")
    heads = fc.parse_spec(spec)
    fc.jdump({"heads": heads, "claims": [c["id"] for c in claims],
              "jobs": [dict(h, job=h["head"]) for h in heads]}, run / "heads.json")
    for h in heads: (run / "briefs" / (h["head"] + ".md")).write_text("brief")
    return run, heads


def answer(run, head, items, **extra):
    (run / "out" / (head + ".md")).write_text(json.dumps(dict(claims=items, **extra)))


def item(cid="C1", url="https://example.gov/rules", quote="The official fee is seven dollars today", **kw):
    return dict(id=cid, verdict="supported", evidence=[dict(url=url, quote=quote, kind="official")], **kw)


def collect(fc, run, fetch_status="blocked", text="", no_fetch=False):
    fc.fetch = lambda u, c: ({"status": fetch_status}, text)
    fc.cmd_collect([str(run)] + (["--no-fetch"] if no_fetch else []))
    return fc.jload(run / "merged.json")


@pytest.mark.parametrize("channel", ["codex", "agy", "opencode", "openrouter"])
def test_job_exceptions_keep_summary(fc, tmp_path, monkeypatch, capsys, channel):
    run, hs = run_dir(tmp_path, fc, "sol:1")
    job = dict(hs[0], job="sol-1", channel=channel)
    fc.jdump({"jobs": [job]}, run / "heads.json")
    if channel == "openrouter":
        def fail(*a, **kw): raise ValueError("malformed channel response")
        monkeypatch.setitem(fc.headreg.CALLS, channel, fail)
    with pytest.raises(SystemExit) as exc: fc.cmd_run([str(run)])
    assert exc.value.code == 1
    assert "FACTCHECK DONE ok=0 error=1" in capsys.readouterr().out
    assert fc.jload(run / "out/sol-1.meta.json")["status"] == "error"
    assert (run / "log/sol-1.log").read_text().strip()


def test_codex_stale_answer_and_delegation(fc, tmp_path, monkeypatch):
    run, hs = run_dir(tmp_path, fc, "sol:1")
    scratch = run / "tmp/sol-1"; scratch.mkdir()
    (scratch / "answer.md").write_text("old answer")
    fake(tmp_path, "codex", "pass")
    calls = []; original = fc.headreg.run_in_group
    def group(*a, **kw): calls.append(a); return original(*a, **kw)
    monkeypatch.setattr(fc.headreg, "run_in_group", group)
    assert fc.run_head(str(run), dict(hs[0], job="sol-1"), 2).startswith("error")
    assert calls and not (run / "out/sol-1.md").exists()
    assert not (scratch / "answer.md").exists()


@pytest.mark.parametrize("diagnostic,expected", [("", "ok"), ("auto-denied", "error")])
def test_agy_response_is_not_diagnostic(fc, tmp_path, diagnostic, expected):
    run, hs = run_dir(tmp_path, fc, "gemini:1")
    fake(tmp_path, "agy", "import json,os\nprint(json.dumps({'response':'auto-denied in quoted model answer', 'usage':{}}))\nprint(" + repr(diagnostic) + ")")
    assert fc.run_head(str(run), dict(hs[0], job="gemini-1"), 2).startswith(expected)
    assert os.path.isdir(fc.AGY_DIR)


@pytest.mark.parametrize("channel,spec", [("agy", "gemini"), ("opencode", "oc")])
def test_argv_guard_is_bytes(fc, tmp_path, channel, spec):
    fc.HEADS[spec] = (channel, "model", "-"); fc.WEB.add(spec)
    run, hs = run_dir(tmp_path, fc, spec)
    (run / "briefs" / (spec + "-1.md")).write_text("é" * 50001)
    assert fc.run_head(str(run), dict(hs[0], job=spec + "-1"), 1).startswith("error")
    assert "brief too large for argv: 100002 bytes" in (run / "log" / (spec + "-1.log")).read_text()


def test_agy_timeout_kills_descendant(fc, tmp_path, monkeypatch):
    run, hs = run_dir(tmp_path, fc, "gemini:1")
    late = tmp_path / "late"
    fake(tmp_path, "agy", "import subprocess,time\nsubprocess.Popen([" + repr(sys.executable) + ", '-c', "
         + repr("import time;time.sleep(1);open(" + repr(str(late)) + ",'w').write('late')") + "])\ntime.sleep(60)")
    seen = []; original = fc.headreg.run_in_group
    def group(cmd, timeout, **kw): seen.append(timeout); return original(cmd, .2, **kw)
    monkeypatch.setattr(fc.headreg, "run_in_group", group)
    assert fc.run_head(str(run), dict(hs[0], job="gemini-1"), 1).startswith("error")
    time.sleep(1.1)
    assert seen and not late.exists()
    assert "TIMEOUT" in (run / "log/gemini-1.log").read_text()


def test_setup_and_prep_repair_project(fc, tmp_path, capsys):
    fc.cmd_setup([])
    assert "brew install poppler or apt install poppler-utils" in capsys.readouterr().out
    run, hs = run_dir(tmp_path, fc, "gemini:1")
    with pytest.raises(SystemExit): fc.cmd_prep([str(run), "--heads", "gemini"])
    assert "agy not set up" in capsys.readouterr().err
    projects = tmp_path / ".gemini/config/projects"; projects.mkdir(parents=True)
    (projects / "project.json").write_text(json.dumps({"name": "factcheck", "permissionGrants": {"allow": ["*"]}}))
    fc.cmd_prep([str(run), "--heads", "gemini"])
    data = fc.jload(projects / "project.json")
    assert data["permissionGrants"]["permissionGrants"]["allow"] == ["read_url(*)", "search_web(*)"]
    fc.cmd_setup([])
    assert "agy project factcheck: present" in capsys.readouterr().out


def test_prep_keeps_unchanged_answers_and_prints_model(fc, tmp_path, capsys):
    run, hs = run_dir(tmp_path, fc, "opus")
    fc.HEADS["opus"] = ("agent", "custom-model", "-")
    fc.cmd_prep([str(run), "--heads", "opus"])
    assert "opus-1 (model custom-model)" in capsys.readouterr().out
    out = run / "out/opus-1.md"; meta = run / "out/opus-1.meta.json"
    out.write_text("old"); meta.write_text('{}')
    fc.cmd_prep([str(run), "--heads", "opus"])
    assert out.exists() and meta.exists()
    (run / "context.md").write_text("changed")
    fc.cmd_prep([str(run), "--heads", "opus"])
    assert not out.exists() and not meta.exists()


@pytest.mark.parametrize("head", [None, ("agent", "model", "-")])
def test_extract_needs_codex_head(fc, tmp_path, head, capsys):
    if head is None: fc.HEADS.pop("sol", None)
    else: fc.HEADS["sol"] = head
    with pytest.raises(SystemExit): fc.cmd_extract([str(tmp_path)])
    assert "extract needs codex; skip it, see the skill" in capsys.readouterr().err


def test_extract_needs_cli(fc, tmp_path, capsys):
    with pytest.raises(SystemExit): fc.cmd_extract([str(tmp_path)])
    assert "extract needs codex; skip it, see the skill" in capsys.readouterr().err


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://example.com/a", "http://127.0.0.1/a", "http://[::1]/", "https://example.com/x"])
def test_unsafe_urls_never_fetch_or_render(fc, tmp_path, monkeypatch, url):
    monkeypatch.setattr(fc, "resolve_host", lambda h: ["127.0.0.1"])
    def forbidden(*a, **kw): pytest.fail("unsafe request or render")
    monkeypatch.setattr(fc.urllib.request, "build_opener", forbidden)
    monkeypatch.setattr(fc.subprocess, "run", forbidden)
    meta, text = fc.fetch(url, str(tmp_path))
    assert meta["status"] == "blocked/unsafe-url" and not text
    assert fc.render(url, str(tmp_path)) == ""


@pytest.mark.parametrize("address", ["10.0.0.1", "169.254.1.1", "224.0.0.1", "240.0.0.1", "::", "fc00::1", "fe80::1", "ff02::1"])
def test_unsafe_resolved_addresses(fc, address, monkeypatch):
    monkeypatch.setattr(fc, "resolve_host", lambda h: ["93.184.216.34", address])
    with pytest.raises(ValueError, match="unsafe"): fc.safe_url("https://example.com/")


def test_redirect_handler_checks_every_target(fc, monkeypatch):
    monkeypatch.setattr(fc, "resolve_host", lambda h: ["127.0.0.1"] if h == "internal.test" else ["93.184.216.34"])
    handler = fc.SafeRedirect()
    req = fc.urllib.request.Request("https://example.com/")
    nextreq = handler.redirect_request(req, None, 302, "Found", {}, "https://other.example/a")
    with pytest.raises(ValueError, match="unsafe"):
        handler.redirect_request(nextreq, None, 302, "Found", {}, "http://internal.test/")


def test_chrome_discovery_and_parallel_profiles(fc, tmp_path, monkeypatch):
    monkeypatch.setenv("THINKTANK_CHROME", "/fake/custom-chrome")
    class Proxy:
        server_port = 12345
        def __init__(self, *a): pass
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def serve_forever(self, **kw): pass
        def shutdown(self): pass
    monkeypatch.setattr(fc.http.server, "ThreadingHTTPServer", Proxy)
    profiles = []
    def fake_run(cmd, **kw):
        assert cmd[0] == "/fake/custom-chrome" and kw["timeout"] == 60
        p = next(v.split("=", 1)[1] for v in cmd if v.startswith("--user-data-dir="))
        assert os.path.isdir(p); profiles.append(p)
        return SimpleNamespace(stdout="<p>Rendered text</p>")
    monkeypatch.setattr(fc.subprocess, "run", fake_run)
    with ThreadPoolExecutor(max_workers=8) as ex: results = list(ex.map(lambda i: fc.render("https://example.com/a", str(tmp_path)), range(8)))
    assert results == ["\nRendered text"] * 8
    assert len(set(profiles)) == 8 and all(not os.path.exists(p) for p in profiles)
    monkeypatch.delenv("THINKTANK_CHROME")
    monkeypatch.setattr(fc.os.path, "isfile", lambda p: False)
    monkeypatch.setattr(fc.shutil, "which", lambda name: "/fake/chromium" if name == "chromium" else None)
    assert fc.chrome_path() == "/fake/chromium"


class Response(io.BytesIO):
    status = 200
    headers = {"Content-Type": "text/html"}
    def geturl(self): return "https://example.com/a"


def fake_http(fc, monkeypatch, response):
    monkeypatch.setattr(fc.urllib.request, "build_opener", lambda *a: SimpleNamespace(open=lambda *a, **kw: response))


def test_pdf_timeout(fc, tmp_path, monkeypatch):
    resp = Response(b"%PDF-fake"); resp.headers = {"Content-Type": "application/pdf"}
    fake_http(fc, monkeypatch, resp)
    monkeypatch.setattr(fc.shutil, "which", lambda n: n)
    def convert(cmd, **kw):
        assert kw["timeout"] == 60
        raise subprocess.TimeoutExpired(cmd, 60)
    monkeypatch.setattr(fc.subprocess, "run", convert)
    assert fc.fetch_direct("https://example.com/a", str(tmp_path), "pdf")[0]["status"] != "ok"


def test_body_deadline_and_limit(fc, tmp_path, monkeypatch):
    class Slow(Response):
        def read(self, n): return b"x" * min(n, 65536)
        def read1(self, n): return self.read(n)
    ticks = iter(range(0, 10000, 10))
    monkeypatch.setattr(fc.time, "monotonic", lambda: next(ticks))
    fake_http(fc, monkeypatch, Slow())
    meta, text = fc.fetch_direct("https://example.com/a", str(tmp_path), "slow")
    assert meta["status"] != "ok" and "deadline" in meta["error"]


def test_numbers_must_be_in_passage(fc):
    q = "For payments made in 2026, the reporting threshold is $600 for most business payments."
    pg = "For payments made in 2026, the reporting threshold is $2,000 for most business payments. " + "unrelated text " * 150 + "Other fee $600."
    assert fc.match_quote(q, fc.mnorm(pg))[0] < .6


@pytest.mark.parametrize("q1,q2,url2,cid2,want", [
    ("", "", "https://example.gov/rules", "C1", False),
    ("short quote", "short quote", "https://example.gov/rules", "C1", False),
    ("The official fee is seven dollars today", "The official fee is seven dollars today", "https://example.gov/other", "C1", False),
    ("The official fee is seven dollars today", "The official fee is seven dollars today", "https://example.gov/rules", "C2", False),
    ("The official fee is seven dollars today", "The official fee is seven dollars today", "http://www.example.gov/rules/", "C1", True),
])
def test_corroboration_scope(fc, tmp_path, q1, q2, url2, cid2, want):
    claims = [dict(id=c, span="text", claim="claim") for c in ("C1", "C2")]
    run, hs = run_dir(tmp_path, fc, claims=claims)
    answer(run, "sol-1", [item(quote=q1)])
    answer(run, "sol-2", [item(cid=cid2, quote=q2, url=url2)])
    m = collect(fc, run)
    ev = m["rows"][0]["items"]["sol-1"]["evidence"][0]
    assert (ev["_quote"] == "corroborated") == want


@pytest.mark.parametrize("status,text,strong", [("blocked", "", False), ("ok", "The official fee is seven dollars today", True),
    ("ok", "The official fee is seven dollars today", False)])
def test_money_needs_strong_match_and_audit_stays(fc, tmp_path, status, text, strong):
    run, hs = run_dir(tmp_path, fc, claims=[dict(id="C1", span="fee", claim="fee", stakes="money")])
    for h in hs:
        it = item()
        if not strong and status == "ok": it["evidence"][0]["kind"] = "news"
        answer(run, h["head"], [it])
    row = collect(fc, run, status, text)["rows"][0]
    assert row["auto_ok"] == strong
    assert row["review"]
    assert ("AUDIT" in row["flags"]) == strong
    if not strong: assert "WEAK_EVIDENCE" in row["flags"]


@pytest.mark.parametrize("bad", [dict(id="C1", verdict=[]), dict(id="C1", verdict="bogus"),
    dict(id="C1", verdict="supported", evidence={}), dict(id="C1", verdict="supported", evidence=[1]),
    dict(id="C1", verdict="supported"), dict(id="C1", verdict="supported", evidence=[dict(url=1, quote="q")]),
    dict(id="C1", verdict="supported", evidence=[dict(url="https://x", quote=[])])])
def test_collect_drops_bad_items_and_keeps_partial(fc, tmp_path, bad):
    run, hs = run_dir(tmp_path, fc, "sol:1")
    answer(run, "sol-1", [item(), bad])
    m = collect(fc, run)
    assert m["stats"]["sol-1"]["status"].startswith("partial")
    assert "partial" in (run / "matrix.md").read_text()
    assert m["dropped"]["sol-1"]


def test_empty_and_bad_new_claims(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc)
    answer(run, "sol-1", [], new_claims=[])
    answer(run, "sol-2", [item()], new_claims={"bad": "shape"})
    m = collect(fc, run, no_fetch=True)
    assert m["stats"]["sol-1"]["status"] == "empty"
    assert m["stats"]["sol-2"]["status"].startswith("partial")
    assert m["no_fetch"] is True
    event = json.loads(open(fc.LEDGER).readlines()[-1])
    assert event["event"] == "collect" and event["claims"] == 1


def test_packet_uses_recorded_facts_and_default_revision(fc, tmp_path, capsys):
    run, hs = run_dir(tmp_path, fc)
    for h in hs: answer(run, h["head"], [item()])
    collect(fc, run, no_fetch=True)
    fc.jdump([dict(id="C1", status="SUPPORTED")], run / "verdicts.json")
    (run / "revised.md").write_text("Revised text")
    fc.cmd_packet([str(run)])
    out = (run / "handoff.md").read_text()
    assert "Revised text" in out and "0 matched" in out and "2 unchecked" in out
    assert "pages were fetched" not in out and "all money/legal" not in out
    assert "1 claims sent to review, of which 0 money; 0 claims auto-ok" in out
    assert "revised.md" in capsys.readouterr().out


@pytest.mark.parametrize("text,want", [("[]", []), ('{"findings": []}', []), ("not json", None), ('{"other":[]}', None), ('{"findings":{}}', None)])
def test_findings_empty_vs_unparsable(fc, text, want):
    assert fc.parse_findings(text) == want


@pytest.mark.parametrize("text", ["not json", '{"findings":{}}'])
def test_pro_bad_output_fails_without_ledger(fc, tmp_path, text):
    run, hs = run_dir(tmp_path, fc)
    reply = tmp_path / "reply"; reply.write_text(text)
    with pytest.raises(SystemExit) as exc: fc.cmd_pro([str(run), "--reply", str(reply)])
    assert exc.value.code == 1 and not os.path.exists(fc.LEDGER)


def test_pro_later_claim_ref_and_empty_valid(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc)
    fc.jdump([dict(id="C1", status="UNRESOLVED")], run / "verdicts.json")
    reply = tmp_path / "reply"; reply.write_text('[{"ref":"see claim C1 here"}]')
    fc.cmd_pro([str(run), "--reply", str(reply)])
    event = json.loads(open(fc.LEDGER).readlines()[-1])
    assert event["on_flagged_claims"] == 1
    reply.write_text("[]"); fc.cmd_pro([str(run), "--reply", str(reply)])
    assert json.loads(open(fc.LEDGER).readlines()[-1])["pro_findings"] == 0


@pytest.mark.parametrize("body", ["raise SystemExit(1)", "import sys;open(sys.argv[sys.argv.index('-o')+1],'w').write('no json')"])
def test_rehearse_failure_is_error(fc, tmp_path, body):
    run, hs = run_dir(tmp_path, fc)
    (run / "handoff.md").write_text("handoff")
    fake(tmp_path, "codex", body)
    with pytest.raises(SystemExit) as exc: fc.cmd_rehearse([str(run)])
    assert exc.value.code == 1
    assert not (run / "rehearsal-sol.ultra-1.findings.json").exists()


def test_registry_names_and_errors(fc, tmp_path, capsys):
    fc.HEADS["custom_head"] = ("codex", "m", "low"); fc.WEB.add("custom_head")
    assert fc.parse_spec("custom_head@high:2")[1]["head"] == "custom_head.high-2"
    p = tmp_path / "registry.json"; p.write_text('{"heads":[]}')
    proc = subprocess.run([sys.executable, SCRIPT, "available"], capture_output=True, text=True)
    assert proc.returncode == 2 and "heads must be an object" in proc.stderr and "Traceback" not in proc.stderr
    p.unlink()
    def bad(args): raise fc.headreg.RegistryError("broken registry")
    monkey = pytest.MonkeyPatch(); monkey.setattr(fc, "cmd_available", bad)
    try:
        assert fc.main(["available"]) == 2
        assert "broken registry" in capsys.readouterr().err
    finally: monkey.undo()


def test_failed_job_does_not_stop_successful_job(fc, tmp_path, capsys):
    run, hs = run_dir(tmp_path, fc, "sol,gemini")
    fake(tmp_path, "codex", "import sys\nopen(sys.argv[sys.argv.index('-o')+1],'w').write('fresh')")
    with pytest.raises(SystemExit) as exc: fc.cmd_run([str(run)])
    assert exc.value.code == 1
    assert "FACTCHECK DONE ok=1 error=1" in capsys.readouterr().out
    assert (run / "out/sol-1.md").read_text() == "fresh"


def test_missing_cwd_is_job_error(fc, tmp_path, monkeypatch):
    run, hs = run_dir(tmp_path, fc, "sol")
    fake(tmp_path, "codex", "pass")
    original = fc.run_codex
    def gone(cmd, stdin, logf, cwd, timeout):
        os.rmdir(cwd)
        return original(cmd, stdin, logf, cwd, timeout)
    monkeypatch.setattr(fc, "run_codex", gone)
    assert fc.run_head(str(run), dict(hs[0], job="sol-1"), 1).startswith("error")
    assert "FileNotFoundError" in (run / "log/sol-1.log").read_text()


@pytest.mark.parametrize("response", [[], {"response": 123}, {"response": "yes", "usage": []}])
def test_malformed_agy_response(fc, tmp_path, response):
    run, hs = run_dir(tmp_path, fc, "gemini")
    fake(tmp_path, "agy", "print(" + repr(json.dumps(response)) + ")")
    assert fc.run_head(str(run), dict(hs[0], job="gemini-1"), 1).startswith("error")
    assert fc.jload(run / "out/gemini-1.meta.json")["status"] == "error"


def test_opencode_success_and_failed_meta_retry(fc, tmp_path):
    fc.HEADS["oc"] = ("opencode", "provider/model", "-"); fc.WEB.add("oc")
    run, hs = run_dir(tmp_path, fc, "oc")
    (run / "out/oc-1.md").write_text("partial")
    fc.jdump({"status": "error"}, run / "out/oc-1.meta.json")
    fake(tmp_path, "opencode", "import os,sys\nif sys.argv[1:] == ['--version']: print('opencode v2.1.4'); sys.exit()\n"
         "print(os.getcwd())\nassert sys.argv[1:5] == ['run','--standalone','--model','provider/model']\n"
         "assert 'websearch' in os.environ['OPENCODE_CONFIG_CONTENT']")
    job = dict(hs[0], job="oc-1")
    assert fc.run_head(str(run), job, 2).startswith("ok")
    assert (run / "out/oc-1.md").read_text().strip() == str(run / "tmp/oc-1")
    assert fc.run_head(str(run), job, 2) == "skip oc-1"


def test_fetch_redirect_rejection_stays_unchecked(fc, tmp_path, monkeypatch):
    class Opener:
        def open(self, req, **kw):
            fc.SafeRedirect().redirect_request(req, None, 302, "Found", {}, "http://127.0.0.1/private")
            pytest.fail("redirect should have been refused")
    monkeypatch.setattr(fc.urllib.request, "build_opener", lambda *a: Opener())
    monkeypatch.setattr(fc, "render", lambda *a: pytest.fail("unsafe redirect rendered"))
    assert fc.fetch("https://example.com/a", str(tmp_path))[0]["status"] == "blocked/unsafe-url"


@pytest.mark.parametrize("tunnel,path", [(False, "http://127.0.0.1/private"), (True, "[::1]:443")])
def test_render_proxy_refuses_private_redirects(fc, monkeypatch, tunnel, path):
    proxy = object.__new__(fc.RenderProxy); proxy.path = path
    seen = []; proxy.send_error = lambda *args: seen.append(args)
    monkeypatch.setattr(fc.socket, "create_connection", lambda *a, **kw: pytest.fail("unsafe socket opened"))
    proxy.forward(tunnel)
    assert seen == [(403, "blocked/unsafe-url")]


@pytest.mark.parametrize("tunnel,path", [(False, "http://example.com/a?b=1"), (True, "example.com:443")])
def test_render_proxy_pins_public_address(fc, monkeypatch, tunnel, path):
    proxy = object.__new__(fc.RenderProxy); proxy.path = path
    proxy.headers = {"Host": "example.com", "Proxy-Connection": "keep-alive"}
    seen = []; data = []
    upstream = SimpleNamespace(sendall=data.append)
    class Connection:
        def __enter__(self): return upstream
        def __exit__(self, *a): pass
    def connect(address, **kw): seen.append(address); return Connection()
    monkeypatch.setattr(fc.socket, "create_connection", connect)
    proxy.relay = lambda sock: seen.append(sock)
    proxy.send_response = lambda code: seen.append(code)
    proxy.end_headers = lambda: None
    proxy.forward(tunnel)
    assert seen[0] == ("93.184.216.34", 443 if tunnel else 80)
    assert seen[-1] is upstream
    if not tunnel: assert data == [b"GET /a?b=1 HTTP/1.1\r\nHost: example.com\r\nConnection: close\r\n\r\n"]


def test_http_byte_cap(fc, tmp_path, monkeypatch):
    class Large(Response):
        total = 0
        def read1(self, n):
            self.total += n
            return b"x" * n
    resp = Large()
    fake_http(fc, monkeypatch, resp)
    meta, text = fc.fetch_direct("https://example.com/a", str(tmp_path), "large")
    assert resp.total == 15_000_000 and len(text) == 15_000_000 and meta["status"] == "ok"


def test_fuzzy_official_quote_does_not_auto_ok_money(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc, claims=[dict(id="C1", span="fee", claim="fee", stakes="money")])
    quote = "The official fee is seven dollars today for all people who apply in person"
    for h in hs: answer(run, h["head"], [item(quote=quote)])
    row = collect(fc, run, "ok", "The official fee is seven dollars today for all applicants who apply in person")["rows"][0]
    assert all(it["evidence"][0]["_quote"] == "fuzzy" for it in row["items"].values())
    assert not row["auto_ok"] and "WEAK_EVIDENCE" in row["flags"]


def test_partial_chunk_status_and_empty_new_claims(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc, "sol")
    h = hs[0]
    fc.jdump({"heads": hs, "claims": ["C1"], "jobs": [dict(h, job="sol-1.c1"), dict(h, job="sol-1.c2")]}, run / "heads.json")
    answer(run, "sol-1.c1", [item()])
    m = collect(fc, run, no_fetch=True)
    assert m["stats"]["sol-1"]["status"] == "partial sol-1.c2:missing"
    assert "partial sol-1.c2:missing" in (run / "matrix.md").read_text()


@pytest.mark.parametrize("revision", [None, "explicit"])
def test_packet_source_selection_and_counts(fc, tmp_path, revision, capsys):
    run, hs = run_dir(tmp_path, fc)
    for h in hs: answer(run, h["head"], [item()])
    collect(fc, run, "ok", "The official fee is seven dollars today")
    fc.jdump([dict(id="C1", status="SUPPORTED")], run / "verdicts.json")
    args = [str(run)]
    if revision:
        (run / "explicit.md").write_text("Explicit revision"); args += ["--revised", str(run / "explicit.md")]
    fc.cmd_packet(args)
    out = (run / "handoff.md").read_text()
    assert "2 matched" in out and "pages were fetched" in out
    assert "0 claims sent to review, of which 0 money; 1 claims auto-ok" in out
    assert ("Explicit revision" if revision else "Original text") in out
    assert ("explicit.md" if revision else "source.orig") in capsys.readouterr().out


def test_rehearse_valid_empty_findings(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc)
    (run / "handoff.md").write_text("handoff")
    fake(tmp_path, "codex", "import sys\nopen(sys.argv[sys.argv.index('-o')+1],'w').write('[]')")
    fc.cmd_rehearse([str(run)])
    assert fc.jload(run / "rehearsal-sol.ultra-1.findings.json") == []


def test_help_lists_every_runner_channel(fc):
    assert "launch every non-agent head (codex, agy, opencode, openrouter)" in fc.__doc__


def test_cached_unsafe_redirect_is_rejected(fc, tmp_path):
    url = "https://example.com/a"; key = fc.hashlib.sha1(url.encode()).hexdigest()[:16]
    fc.jdump({"status": "ok", "final_url": "http://127.0.0.1/private"}, tmp_path / (key + ".json"))
    (tmp_path / (key + ".txt")).write_text("stale unsafe page")
    meta, text = fc.fetch(url, str(tmp_path))
    assert meta["status"] == "blocked/unsafe-url" and not text


def test_new_claims_bad_id_dropped_and_all_quotes_counted(fc, tmp_path):
    run, hs = run_dir(tmp_path, fc, "sol")
    new = item(cid="N1"); new.update(span="extra", claim="extra")
    malformed = item(cid=[])
    answer(run, "sol-1", [item()], new_claims=[new, malformed])
    m = collect(fc, run, "ok", "The official fee is seven dollars today")
    assert m["quote_counts"]["match"] == 2
    assert m["dropped"]["sol-1"] == ["sol-1:new_claims[1]:id must be a string"]
    fc.jdump([dict(id="C1", status="SUPPORTED")], run / "verdicts.json")
    fc.cmd_packet([str(run)])
    assert "2 matched" in (run / "handoff.md").read_text()


def test_openrouter_shape_failure_is_job_error(fc, tmp_path, monkeypatch):
    run, hs = run_dir(tmp_path, fc, "or-gpt")
    monkeypatch.setattr(fc.headreg, "OPENROUTER_URL", "http://127.0.0.1:12345")
    monkeypatch.setattr(fc.urllib.request, "urlopen", lambda *a, **kw: io.StringIO('{"choices": []}'))
    assert fc.run_head(str(run), dict(hs[0], job="or-gpt-1"), 1).startswith("error")
    assert "invalid OpenRouter response shape" in (run / "log/or-gpt-1.log").read_text()


def test_original_dns_resolution_uses_all_addresses(fc, monkeypatch):
    monkeypatch.setattr(fc, "resolve_host", lambda host: [r[4][0] for r in fc.socket.getaddrinfo(host, None)])
    monkeypatch.setattr(fc.socket, "getaddrinfo", lambda *a, **kw: [(None, None, None, None, ("93.184.216.34", 0)),
                                                                  (None, None, None, None, ("192.168.1.1", 0))])
    with pytest.raises(ValueError, match="unsafe"): fc.safe_url("https://example.com/")


def test_exact_prefix_of_different_amount_cannot_match(fc):
    assert fc.match_quote("The official fee is $600", fc.mnorm("The official fee is $6000 today"))[0] < .6
