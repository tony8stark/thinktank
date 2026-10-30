"""heads.py update-check: one line when a newer version is published, silence otherwise, at most one request a day.
No network: a copy of the plugin talks to a local HTTP server, the cache lives in a temp XDG_CACHE_HOME."""
import http.server, importlib.util, json, os, pathlib, shutil, subprocess, sys, threading, time
import pytest

SCRIPTS = pathlib.Path(__file__).resolve().parent.parent / "scripts"


class Fake(http.server.BaseHTTPRequestHandler):
    hits = 0; status = 200; body = b""; trickle = False

    def do_GET(self):
        type(self).hits += 1
        self.send_response(self.status); self.end_headers()
        while self.trickle:  # bytes keep arriving, so a socket timeout never fires; the client leaving ends this with an OSError
            self.wfile.write(b" "); self.wfile.flush(); time.sleep(0.2)
        self.wfile.write(self.body)

    def log_message(self, *a): pass


@pytest.fixture
def plugin(tmp_path, monkeypatch):
    for v in ("THINKTANK_NO_UPDATE_CHECK", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"):
        monkeypatch.delenv(v, raising=False)
    (tmp_path / "scripts").mkdir(); (tmp_path / ".claude-plugin").mkdir()
    shutil.copy(SCRIPTS / "heads.py", tmp_path / "scripts/heads.py")
    Fake.hits, Fake.status, Fake.body, Fake.trickle = 0, 200, b"", False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fake)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/plugin.json"

    class P:
        cache = tmp_path / "cache/thinktank/update-check.json"
        @staticmethod
        def installed(v): (tmp_path / ".claude-plugin/plugin.json").write_text(json.dumps({"version": v}))
        @staticmethod
        def published(v): Fake.body = json.dumps({"version": v}).encode()
        @staticmethod
        def env(**extra): return {**os.environ, "XDG_CACHE_HOME": str(tmp_path / "cache"), "THINKTANK_UPDATE_URL": url, **extra}
        argv = [sys.executable, str(tmp_path / "scripts/heads.py"), "update-check"]
        @staticmethod
        def check(**extra): return subprocess.run(P.argv, capture_output=True, text=True, timeout=20, env=P.env(**extra))
    yield P
    srv.shutdown()


def test_newer_version_prints_one_line_with_both_versions(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    r = plugin.check()
    assert r.returncode == 0 and len(r.stdout.splitlines()) == 1
    assert "thinktank 1.4.0 is available (running: 1.3.1)" in r.stdout and "claude plugin update thinktank@thinktank" in r.stdout


@pytest.mark.parametrize("installed,published", [("1.4.0", "1.4.0"), ("1.4.0", "1.3.9"), ("1.10.0", "1.9.0")])
def test_same_or_older_published_version_is_silent(plugin, installed, published):
    plugin.installed(installed); plugin.published(published)
    r = plugin.check()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""


def test_second_run_within_a_day_uses_the_cache_and_still_reports(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    assert plugin.check().stdout and plugin.check().stdout
    assert Fake.hits == 1


def test_updating_the_plugin_silences_the_cached_answer(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    assert plugin.check().stdout
    plugin.installed("1.4.0")
    assert plugin.check().stdout == "" and Fake.hits == 1


def test_day_old_cache_is_refreshed(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    plugin.check()
    plugin.cache.write_text(json.dumps({"checked": time.time() - 86401, "latest": "1.4.0"}))
    plugin.published("1.5.0")
    assert "1.5.0 is available" in plugin.check().stdout and Fake.hits == 2


def test_cache_from_the_future_is_refreshed(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    plugin.check()
    plugin.cache.write_text(json.dumps({"checked": time.time() + 86400 * 365, "latest": "1.4.0"}))
    plugin.check()
    assert Fake.hits == 2


@pytest.mark.parametrize("status,body", [(500, b"oops"), (200, b"not json"), (200, b"[]"), (200, b"{}"), (200, b'{"version": "2.0.0-beta"}'), (200, b'{"version": 2}')])
def test_bad_response_is_silent_and_not_retried_the_same_day(plugin, status, body):
    plugin.installed("1.3.1"); Fake.status, Fake.body = status, body
    for _ in range(2):
        r = plugin.check()
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert Fake.hits == 1


def test_unreachable_server_is_silent_and_recorded(plugin):
    plugin.installed("1.3.1")
    r = plugin.check(THINKTANK_UPDATE_URL="http://127.0.0.1:9/x")
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert json.loads(plugin.cache.read_text())["latest"] is None


def test_trickling_response_is_abandoned_at_the_deadline_and_not_retried(plugin):
    plugin.installed("1.3.1"); Fake.trickle = True
    started = time.time()
    r = plugin.check()
    assert time.time() - started < 8 and r.returncode == 0 and r.stdout == "" and r.stderr == ""
    Fake.trickle = False; plugin.published("1.4.0")
    assert plugin.check().stdout == "" and Fake.hits == 1


def test_parallel_runs_send_one_request(plugin):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    runs = [subprocess.Popen(plugin.argv, env=plugin.env(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(8)]
    outs = [p.communicate(timeout=20) for p in runs]
    assert Fake.hits == 1 and all(p.returncode == 0 for p in runs) and all(err == "" for _, err in outs)


def test_failed_refresh_keeps_the_known_version(plugin):
    plugin.installed("1.3.1")
    plugin.cache.parent.mkdir(parents=True)
    plugin.cache.write_text(json.dumps({"checked": time.time() - 86401, "latest": "1.4.0"}))
    Fake.status = 500
    assert "1.4.0 is available" in plugin.check().stdout


@pytest.mark.parametrize("text", ["not json", "[]", '{"latest": "9.9.9"}', '{"checked": "x", "latest": "9.9.9"}'])
def test_broken_cache_is_replaced(plugin, text):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    plugin.cache.parent.mkdir(parents=True); plugin.cache.write_text(text)
    assert "1.4.0 is available" in plugin.check().stdout and Fake.hits == 1
    assert json.loads(plugin.cache.read_text())["latest"] == "1.4.0"


@pytest.mark.parametrize("var", ["THINKTANK_NO_UPDATE_CHECK", "DISABLE_UPDATES", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"])
@pytest.mark.parametrize("value", ["1", "0"])
def test_opt_out_sends_nothing(plugin, var, value):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    r = plugin.check(**{var: value})
    assert r.returncode == 0 and r.stdout == "" and Fake.hits == 0


def test_desktop_app_session_still_gets_the_notice(plugin):
    """The Claude desktop app sets DISABLE_AUTOUPDATER=1 on every session; it stops background updates, not this notice."""
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    assert "1.4.0 is available" in plugin.check(DISABLE_AUTOUPDATER="1", CLAUDE_CODE_ENTRYPOINT="claude-desktop").stdout


@pytest.mark.parametrize("text", [None, "not json", "{}", '{"version": "dev"}', '{"version": "1.' + "9" * 5000 + '.0"}'])
def test_unreadable_running_version_sends_nothing(plugin, tmp_path, text):
    plugin.published("1.4.0")
    if text is not None: (tmp_path / ".claude-plugin/plugin.json").write_text(text)
    r = plugin.check()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == "" and Fake.hits == 0


def test_huge_published_or_cached_numbers_are_silent(plugin):
    plugin.installed("1.3.1"); Fake.body = b'{"version": "1.' + b"9" * 5000 + b'.0"}'
    r = plugin.check()
    assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    plugin.cache.write_text('{"checked": 1' + "0" * 400 + ', "latest": "1.4.0"}')
    r = plugin.check()
    assert r.returncode == 0 and r.stderr == ""


def test_cache_that_cannot_be_written_means_no_request(plugin, tmp_path):
    plugin.installed("1.3.1"); plugin.published("1.4.0")
    (tmp_path / "cache").write_text("a file where the cache directory should be")
    for _ in range(2):
        r = plugin.check()
        assert r.returncode == 0 and r.stdout == "" and r.stderr == ""
    assert Fake.hits == 0


def test_shipped_version_and_url_match_the_manifest(monkeypatch):
    monkeypatch.delenv("THINKTANK_UPDATE_URL", raising=False)
    spec = importlib.util.spec_from_file_location("heads", SCRIPTS / "heads.py")
    heads = importlib.util.module_from_spec(spec); spec.loader.exec_module(heads)
    manifest = json.loads((SCRIPTS.parent / ".claude-plugin/plugin.json").read_text())
    assert heads.parse_version(manifest["version"])
    repo = manifest["repository"].split("github.com/")[1]
    assert heads.UPDATE_URL == f"https://raw.githubusercontent.com/{repo}/main/.claude-plugin/plugin.json"
