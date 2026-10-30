"""Choosing models: offline catalogue, registry edits, metadata and weekly issue checks."""
import http.server, importlib.util, io, json, os, pathlib, shutil, subprocess, sys, threading
import pytest
from test_heads import clean_path, fake_bin

ROOT = pathlib.Path(__file__).resolve().parent.parent
HEAD = {"channel": "openrouter", "model": "z/model"}


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    scripts = tmp_path / "scripts"; scripts.mkdir()
    shutil.copy(ROOT / "scripts/heads.py", scripts / "heads.py")
    shipped = scripts / "heads.json"
    shipped.write_text(json.dumps({"heads": {"default": HEAD}}))
    user = tmp_path / "config/heads.json"
    monkeypatch.setenv("THINKTANK_HEADS", str(user))
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    class Handler(http.server.BaseHTTPRequestHandler):
        status = 200
        body = json.dumps({"data": [{"id": "z/model"}]}).encode()
        paths = []
        response = {"id": "req-test", "model": "z/real", "choices": [{"message": {"content": "PONG"}}],
                    "usage": {"prompt_tokens": 100, "completion_tokens": 3}}
        def do_GET(self):
            self.paths.append(self.path)
            self.send_response(self.status); self.end_headers(); self.wfile.write(self.body)
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"]))
            self.send_response(self.status); self.end_headers(); self.wfile.write(json.dumps(self.response).encode())
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_port}/v1/chat/completions"
    monkeypatch.setenv("OPENROUTER_URL", url)
    class P:
        handler = Handler
        base = url.removesuffix("/chat/completions")
        @staticmethod
        def run(*args, **env):
            return subprocess.run([sys.executable, str(scripts / "heads.py"), *args],
                                  capture_output=True, text=True, timeout=20, env={**os.environ, **env})
        @staticmethod
        def catalogue(entries): Handler.body = json.dumps({"data": entries}).encode()
        @staticmethod
        def defaults(entries): shipped.write_text(json.dumps({"heads": entries}))
        @staticmethod
        def users(entries, **extra):
            user.parent.mkdir(exist_ok=True); user.write_text(json.dumps({**extra, "heads": entries}))
        @staticmethod
        def load():
            spec = importlib.util.spec_from_file_location("model_heads", scripts / "heads.py")
            m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m
    P.user = user
    yield P
    srv.shutdown(); srv.server_close()


def failure(r, code):
    assert r.returncode == code and r.stdout == "" and len(r.stderr.splitlines()) == 1
    assert "Traceback" not in r.stderr


def test_models_filter_sort_prices_missing_and_no_matches(plugin):
    plugin.catalogue([{"id": "z/model", "name": "Great ALPHA", "context_length": 4096,
                       "pricing": {"prompt": "0.00000008", "completion": "0.0000036"}},
                      {"id": "a/model", "name": "Alpha"}, {"id": "~alias", "name": "Alpha model"},
                      {"id": "other", "name": "Alpha"}])
    r = plugin.run("models", "aLpHa", "MODEL")
    assert r.returncode == 0 and r.stdout.splitlines() == ["a/model\t-\t-\t-\t-", "z/model\t4096\t0.08\t3.6\t-", "~alias\t-\t-\t-\t-"]
    assert plugin.handler.paths == ["/v1/models"]
    assert plugin.run("models", "absent").stdout == "" and plugin.run("models", "absent").returncode == 0


@pytest.mark.parametrize("status,body", [(500, b"oops"), (200, b"garbage"), (200, b"[]"), (200, b"{}"),
    (200, b'{"data":{}}'), (200, b'{"data":[null]}'), (200, b'{"data":[{"id":1}]}')])
def test_catalogue_failures(plugin, status, body):
    plugin.handler.status, plugin.handler.body = status, body
    failure(plugin.run("models"), 3)


@pytest.mark.parametrize("url", ["http://127.0.0.1:9/v1/chat/completions", "http://example.com/chat/completions",
    "https://user:pass@example.com/chat/completions", "https://@example.com/chat/completions", "file:///tmp/models", "http://localhost:bad/chat/completions"])
def test_catalogue_unreachable_or_unsafe(plugin, url):
    failure(plugin.run("models", OPENROUTER_URL=url), 3)


def test_check_models_clean_suffix_alias_user_and_other_channels(plugin):
    plugin.defaults({"default": HEAD, "fast": dict(HEAD, model="z/model:nitro"), "alias": dict(HEAD, model="~latest"),
                     "codex": {"channel": "codex", "model": "not-listed"}})
    plugin.users({"mine": dict(HEAD, model="user/model")})
    plugin.catalogue([{"id": x} for x in ("z/model", "~latest", "user/model")])
    r = plugin.run("check-models")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    plugin.catalogue([{"id": "z/model"}, {"id": "~latest"}, {"id": "user/model", "expiration_date": "2026-11-11"}])
    r = plugin.run("check-models")
    assert r.returncode == 1 and r.stdout == "mine\tuser/model\texpires 2026-11-11\n"
    plugin.catalogue([])
    assert "default\tz/model\tmissing" in plugin.run("check-models").stdout
    failure(plugin.run("check-models", OPENROUTER_URL="http://127.0.0.1:9/chat/completions"), 3)


def test_exact_suffix_entry_precedes_base(plugin):
    plugin.defaults({"default": dict(HEAD, model="z/model:free")})
    plugin.catalogue([{"id": "z/model"}, {"id": "z/model:free", "expiration_date": "2026-11-11"}])
    assert "expires 2026-11-11" in plugin.run("check-models").stdout


def test_set_create_replace_preserve_mode_order_and_model(plugin):
    r = plugin.run("set", "mine", json.dumps(HEAD))
    assert r.returncode == 0 and "mine" in r.stdout and str(plugin.user) in r.stdout
    assert plugin.user.stat().st_mode & 0o777 == 0o600
    plugin.users({"first": HEAD, "mine": HEAD, "last": HEAD}, other={"keep": [1, 2]})
    plugin.user.chmod(0o640)
    assert plugin.run("set", "mine", json.dumps(dict(HEAD, model="new/model"))).returncode == 0
    data = json.loads(plugin.user.read_text())
    assert list(data["heads"]) == ["first", "mine", "last"] and data["other"] == {"keep": [1, 2]}
    assert plugin.user.stat().st_mode & 0o777 == 0o640 and plugin.user.read_text().endswith("\n")
    assert plugin.run("model", "mine").stdout == "openrouter new/model -\n"
    assert plugin.run("set", "added", json.dumps(HEAD)).returncode == 0
    assert list(json.loads(plugin.user.read_text())["heads"]) == ["first", "mine", "last", "added"]


def test_set_symlink_survives(plugin, tmp_path):
    target = tmp_path / "real.json"; target.write_text('{"heads": {}}'); target.chmod(0o644)
    plugin.user.parent.mkdir(); plugin.user.symlink_to(target)
    assert plugin.run("set", "mine", json.dumps(HEAD)).returncode == 0
    assert plugin.user.is_symlink() and "mine" in json.loads(target.read_text())["heads"]
    assert target.stat().st_mode & 0o777 == 0o644


@pytest.mark.parametrize("name,value", [("mine", "{"), ("mine", "[]"), ("mine", "true"), ("mine", '{"channel":"bad","model":"m"}'),
    ("mine", '{"channel":"codex","model":""}'), ("BAD", "null"), ("mine", '{"channel":"command","model":"m"}')])
def test_set_invalid_untouched(plugin, name, value):
    plugin.users({"keep": HEAD}); before = plugin.user.read_bytes()
    failure(plugin.run("set", name, value), 2)
    assert plugin.user.read_bytes() == before


@pytest.mark.parametrize("value", ["null", json.dumps(HEAD), '{"channel":"command","model":"m","command":["/bin/echo"]}'])
def test_set_command_replace_remove_refused(plugin, value):
    plugin.users({"cmd": {"channel": "command", "model": "m", "command": ["/bin/echo"]}})
    before = plugin.user.read_bytes()
    r = plugin.run("set", "cmd", value); failure(r, 2)
    assert "command heads run programs and are added by hand" in r.stderr and plugin.user.read_bytes() == before


@pytest.mark.parametrize("body", ["garbage", "[]", "{}", '{"heads": []}'])
def test_set_broken_existing_untouched(plugin, body):
    plugin.user.parent.mkdir(); plugin.user.write_text(body)
    failure(plugin.run("set", "mine", json.dumps(HEAD)), 2)
    assert plugin.user.read_text() == body


def test_set_unreadable_existing(plugin):
    plugin.user.mkdir(parents=True)
    failure(plugin.run("set", "mine", json.dumps(HEAD)), 2)
    assert plugin.user.is_dir()


def test_set_null_default_user_and_unknown(plugin):
    assert plugin.run("set", "default", "null").returncode == 0
    assert json.loads(plugin.user.read_text())["heads"] == {"default": None}
    assert plugin.run("list").stdout == ""
    assert plugin.run("set", "mine", json.dumps(HEAD)).returncode == 0
    assert plugin.run("set", "mine", "null").returncode == 0
    assert "mine" not in json.loads(plugin.user.read_text())["heads"]
    before = plugin.user.read_bytes(); failure(plugin.run("set", "unknown", "null"), 2)
    assert plugin.user.read_bytes() == before


@pytest.mark.parametrize("panel,channel,reason", [(0, "codex", "integer 1..3"), (4, "codex", "integer 1..3"),
    (True, "codex", "integer 1..3"), ("1", "codex", "integer 1..3"), (1, "agent", "not for agent")])
def test_panel_validation(plugin, panel, channel, reason):
    h = dict(HEAD, channel=channel, panel=panel)
    r = plugin.run("set", "mine", json.dumps(h)); failure(r, 2)
    assert reason in r.stderr
    plugin.users({"mine": h})
    failure(plugin.run("list"), 2)


def test_panel_available_order_and_list(plugin, tmp_path):
    plugin.defaults({"z": dict(HEAD, panel=2), "ordinary": HEAD, "a": {"channel": "codex", "model": "m", "panel": 1},
                     "absent": {"channel": "agy", "model": "m", "panel": 3}})
    plugin.users({"last": dict(HEAD, panel=1)})
    path = clean_path(tmp_path, fake_bin(tmp_path, "codex", "exit 0"))
    assert plugin.run("panel", PATH=path, OPENROUTER_API_KEY="test").stdout == "z:2\na:1\nlast:1\n"
    assert plugin.run("panel", PATH=path, OPENROUTER_API_KEY="").stdout == "a:1\n"
    rows = plugin.run("list", PATH=path, OPENROUTER_API_KEY="test").stdout.splitlines()
    assert rows[0].endswith("\tpanel=2")
    assert rows[1] == "ordinary\topenrouter\tz/model\t-\t?\tweb=no\tavailable"


@pytest.mark.parametrize("body,want", [('{"error":{"message":"bad","code":400},"user_id":"user_abc"}', "bad"),
    ('{"error":"bad"}', "bad"), ('{"error":1}', None), ('{"error":{"message":4}}', None), ('{"message":"bad"}', None),
    ('[]', None), ('garbage', None)])
def test_provider_error(plugin, body, want):
    assert plugin.load().provider_error(body) == want


@pytest.mark.parametrize("channel", ["openrouter", "local"])
def test_provider_log_excludes_account_and_redacts_local(plugin, tmp_path, monkeypatch, channel):
    plugin.handler.status = 400
    plugin.handler.response = {"error": {"message": "invalid model", "code": 400}, "user_id": "user_abc"}
    m = plugin.load(); log = io.StringIO()
    h = dict(HEAD, backend="openai", base_url=plugin.base, api_key_env="MODEL_TEST_KEY")
    monkeypatch.setenv("MODEL_TEST_KEY", "secret-test")
    call = m.call_openrouter if channel == "openrouter" else m.call_local
    assert call(h, "hi", tmp_path / "answer", log, 5)[0] == "error"
    assert log.getvalue() == "HTTP 400: invalid model\n" and "user_abc" not in log.getvalue()
    plugin.handler.response = {"error": "secret-test" + "x" * 1000}
    log = io.StringIO(); call(h, "hi", tmp_path / "answer", log, 5)
    assert len(log.getvalue().strip()) == len("HTTP 400: ") + (500 if channel == "openrouter" else 300)
    if channel == "local": assert "secret-test" not in log.getvalue() and "[redacted]" in log.getvalue()


def test_openrouter_no_error_message(plugin, tmp_path):
    plugin.handler.status = 400; plugin.handler.response = {"user_id": "user_abc"}
    log = io.StringIO(); plugin.load().call_openrouter(HEAD, "hi", tmp_path / "out", log, 5)
    assert log.getvalue() == "HTTP 400: (no error message)\n"


def test_resolved_model_last_match_and_missing(plugin, tmp_path):
    log = tmp_path / "log"
    log.write_text('noise\n{"id":"1","model":"first"}\n[]\n{"model":"ignored"}\n{"id":"2","model":"last"}\n{"id":"3","model":3}\n')
    assert plugin.load().resolved_model(log) == "last"
    assert plugin.run("resolved-model", str(log)).stdout == "last\n"
    log.write_text('noise\n')
    assert plugin.run("resolved-model", str(log)).stdout == ""
    assert plugin.load().resolved_model(tmp_path / "missing") is None
    assert plugin.run("resolved-model", str(tmp_path / "missing")).returncode == 0


def test_both_runners_resolved_model(plugin, tmp_path):
    plugin.users({"mine": dict(HEAD, web=True)})
    brief = tmp_path / "brief"; brief.write_text("say pong")
    path = clean_path(tmp_path, fake_bin(tmp_path, "codex", 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo ok > "$2"; shift; done'))
    env = dict(os.environ, PATH=path, OPENROUTER_API_KEY="test")
    run = tmp_path / "thinktank"
    r = subprocess.run(["/bin/bash", str(ROOT / "scripts/thinktank.sh"), str(run), str(brief), "--heads", "mine,sol"],
                       env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((run / "out/mine-1.meta.json").read_text())["resolved_model"] == "z/real"
    assert "resolved_model" not in json.loads((run / "out/sol-1.meta.json").read_text())
    fc = [sys.executable, str(ROOT / "scripts/factcheck.py")]; run = tmp_path / "factcheck"
    for args in (["init", str(run), str(brief)], ["prep", str(run), "--heads", "mine:1,sol:1"]):
        if args[0] == "prep":
            (run / "claims.json").write_text(json.dumps([{"id": "C1", "para": "p1", "claim": "test", "span": "test", "stakes": "low"}]))
        subprocess.run(fc + args, env=env, check=True, capture_output=True, text=True, timeout=20)
    r = subprocess.run(fc + ["run", str(run)], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stdout + r.stderr
    assert json.loads((run / "out/mine-1.meta.json").read_text())["resolved_model"] == "z/real"
    assert "resolved_model" not in json.loads((run / "out/sol-1.meta.json").read_text())


@pytest.mark.parametrize("status,existing,action", [(0, "", None), (0, "exact", "close"), (1, "", "create"), (1, "exact", "edit"), (1, "similar", "create"), (3, "", None)])
def test_weekly_check(tmp_path, status, existing, action):
    (tmp_path / "scripts").mkdir(); binpath = tmp_path / "bin"; binpath.mkdir()
    (tmp_path / "scripts/heads.py").write_text(f'import os,sys\nassert os.environ["THINKTANK_HEADS"] == "/nonexistent"\nassert sys.argv[1:] == ["check-models"]\nprint("default\\tz/model\\tmissing") if {status} == 1 else None\nsys.exit({status})\n')
    gh = binpath / "gh"
    gh.write_text(f'''#!{sys.executable}
import json, sys
args = sys.argv[1:]
if args[:2] == ["issue", "list"]:
    assert "--state" in args and "open" in args and "--search" in args and "--json" in args and "--jq" in args
    assert '.title == "Default OpenRouter model needs attention"' in args[args.index("--jq")+1]
    if {existing!r} == "exact": print(42)
else:
    json.dump({{"args": args, "body": sys.stdin.read() if args[1] != "close" else ""}}, open("gh.json", "w"))
'''); gh.chmod(0o755)
    r = subprocess.run(["/bin/bash", str(ROOT / ".github/models-check.sh")], cwd=tmp_path,
                       env=dict(os.environ, PATH=f"{binpath}:{os.environ['PATH']}"), capture_output=True, text=True, timeout=20)
    assert r.returncode == (3 if status == 3 else 0)
    log = tmp_path / "gh.json"
    if action:
        data = json.loads(log.read_text()); assert data["args"][:2] == ["issue", action]
        if action == "close":
            assert data["args"][2] == "42" and "--comment" in data["args"]
            return
        assert "default\tz/model\tmissing" in data["body"] and "scripts/heads.json" in data["body"]
        assert "bump the version" in data["body"] and "CHANGELOG" in data["body"]
        if action == "edit": assert data["args"][2] == "42"
        else: assert data["args"][data["args"].index("--title")+1] == "Default OpenRouter model needs attention"
    else:
        assert not log.exists()
        assert r.stderr if status == 3 else r.stdout


def test_catalogue_limit_timeout_and_dynamic_url(plugin, monkeypatch):
    m = plugin.load(); seen = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def read(self, limit):
            assert limit == 32 * 1024 * 1024
            return b" " * limit
    def fetch(url, timeout):
        seen.append(url); assert timeout == 15
        return Response()
    monkeypatch.setattr(m.urllib.request, "urlopen", fetch)
    m.OPENROUTER_URL = "https://example.com/api/chat/completions"
    with pytest.raises(m.CatalogueError): m.openrouter_catalogue()
    assert seen == ["https://example.com/api/models"]


def test_set_failed_replace_keeps_existing(plugin, monkeypatch):
    plugin.users({"keep": HEAD}); before = plugin.user.read_bytes()
    m = plugin.load()
    def fail(*a): raise OSError("replace failed")
    monkeypatch.setattr(m.os, "replace", fail)
    with pytest.raises(m.RegistryError, match="replace failed"): m.set_head("mine", json.dumps(HEAD))
    assert plugin.user.read_bytes() == before and not list(plugin.user.parent.glob(".heads-*"))


FULL = {"channel": "openrouter", "model": "z/old", "effort": "-", "family": "zed", "web": True, "params": {"temperature": 0.2}}


def test_set_merges_into_the_head_as_it_stands(plugin):
    plugin.users({"mine": FULL})
    assert plugin.run("set", "mine", '{"model": "z/new"}').returncode == 0
    assert json.loads(plugin.user.read_text())["heads"]["mine"] == dict(FULL, model="z/new")
    assert plugin.run("set", "mine", '{"panel": 1, "params": null}').returncode == 0
    want = dict(FULL, model="z/new", panel=1); del want["params"]
    assert json.loads(plugin.user.read_text())["heads"]["mine"] == want


def test_set_on_a_shipped_head_starts_from_the_default(plugin):
    plugin.defaults({"default": FULL})
    assert plugin.run("set", "default", '{"model": "z/new"}').returncode == 0
    assert json.loads(plugin.user.read_text())["heads"]["default"] == dict(FULL, model="z/new")


def test_set_with_a_new_channel_drops_the_old_channels_fields(plugin):
    plugin.users({"mine": FULL})
    assert plugin.run("set", "mine", '{"channel": "codex", "model": "gpt", "effort": "low"}').returncode == 0
    assert json.loads(plugin.user.read_text())["heads"]["mine"] == {"channel": "codex", "model": "gpt", "effort": "low"}


def test_set_partial_entry_for_a_new_head_fails(plugin):
    failure(plugin.run("set", "mine", '{"panel": 1}'), 2)
    assert not plugin.user.exists()


def test_set_refuses_a_registry_it_could_not_load_afterwards(plugin):
    plugin.users({"broken": {"channel": "openrouter"}})
    before = plugin.user.read_bytes()
    failure(plugin.run("set", "mine", json.dumps(HEAD)), 2)
    assert plugin.user.read_bytes() == before


def test_set_keeps_a_user_entry_for_a_newer_plugin(plugin):
    plugin.users({"future": {"channel": "quantum", "model": "m"}})
    assert plugin.run("set", "mine", json.dumps(HEAD)).returncode == 0
    assert json.loads(plugin.user.read_text())["heads"]["future"] == {"channel": "quantum", "model": "m"}


def test_set_will_not_launder_an_untrusted_command_file(plugin):
    plugin.users({"cmd": {"channel": "command", "model": "m", "command": ["/bin/echo"]}})
    plugin.user.chmod(0o664)
    before = plugin.user.read_bytes()
    r = plugin.run("set", "mine", json.dumps(HEAD))
    assert r.returncode == 2 and "refusing command heads" in r.stderr
    assert plugin.user.read_bytes() == before and plugin.user.stat().st_mode & 0o777 == 0o664


def test_parallel_sets_all_land(plugin):
    runs = [subprocess.Popen([sys.executable, str(plugin.user.parent.parent / "scripts/heads.py"), "set", f"h{i}", json.dumps(HEAD)],
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True) for i in range(8)]
    assert all(p.communicate(timeout=20)[1] == "" for p in runs)
    assert set(json.loads(plugin.user.read_text())["heads"]) == {f"h{i}" for i in range(8)}


def test_unhashable_backend_is_a_clean_error(plugin):
    failure(plugin.run("set", "mine", '{"channel": "local", "model": "m", "backend": [], "params": {}}'), 2)


def test_listed_variant_that_is_gone_is_missing_and_routing_suffix_is_not(plugin):
    plugin.catalogue([{"id": "z/model"}, {"id": "other/thing:free"}])
    plugin.users({"gone": {"channel": "openrouter", "model": "z/model:free"}, "fast": {"channel": "openrouter", "model": "z/model:nitro"}})
    r = plugin.run("check-models")
    assert r.returncode == 1 and r.stdout == "gone\tz/model:free\tmissing\n"


@pytest.mark.parametrize("body", ['{"user_id": "user_abc"}', "plain user_abc", '{"error": {"code": 1}, "user_id": "user_abc"}'])
def test_local_error_without_a_message_logs_no_body(plugin, tmp_path, body):
    m = plugin.load()
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers["Content-Length"])); self.send_response(500); self.end_headers(); self.wfile.write(body.encode())
        def log_message(self, *a): pass
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    log = tmp_path / "local.log"
    head = {"channel": "local", "backend": "openai", "base_url": f"http://127.0.0.1:{srv.server_port}/v1", "model": "m", "effort": "-"}
    with open(log, "w") as f: m.call_local(head, "hi", str(tmp_path / "a.md"), f, 10)
    srv.shutdown(); srv.server_close()
    assert "HTTP 500" in log.read_text() and "user_abc" not in log.read_text()


def test_deeply_nested_error_body_is_no_message(plugin):
    assert plugin.load().provider_error("[" * 200000) is None


def test_the_skills_test_command_works_as_written(plugin, tmp_path):
    """Section 8 step 3 of the ThinkTank skill, on the real runner, in an existing directory that already holds the brief."""
    run = tmp_path / "existing"; run.mkdir(); (run / "brief.md").write_text("Reply with the word PONG.\n")
    plugin.users({"mine": HEAD})
    r = subprocess.run(["/bin/bash", str(ROOT / "scripts/thinktank.sh"), str(run), str(run / "brief.md"), "--heads", "mine:1"],
                       capture_output=True, text=True, timeout=60, env=dict(os.environ, OPENROUTER_API_KEY="k"))
    assert "THINKTANK DONE ok=1 error=0" in r.stdout, r.stdout + r.stderr
    assert json.loads((run / "out/mine-1.meta.json").read_text())["resolved_model"] == "z/real"
