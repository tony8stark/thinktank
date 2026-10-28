"""Offline runner and registry regressions, with isolated HOME and fake CLIs."""
import io, json, os, signal, subprocess, sys, time, threading, http.server
import pytest
from test_heads import load, fake_bin, clean_path, RUNNER, SCRIPTS


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv('HOME', str(tmp_path))
    monkeypatch.setenv('THINKTANK_HEADS', str(tmp_path / 'heads.json'))
    monkeypatch.delenv('OPENROUTER_API_KEY', raising=False)
    return dict(os.environ, PATH=clean_path(tmp_path))


def run(tmp_path, env, spec='sol:1', text='brief', args=None):
    brief = tmp_path / 'input.md'; brief.write_text(text)
    return subprocess.run(['bash', RUNNER, str(tmp_path / 'run'), str(brief), '--heads', spec] + (args or []),
                          env=env, capture_output=True, text=True, timeout=20)


def meta(tmp_path, head):
    return json.loads((tmp_path / f'run/out/{head}-1.meta.json').read_text())


def cli(env, *args):
    return subprocess.run([sys.executable, os.path.join(SCRIPTS, 'heads.py'), *args], env=env,
                          capture_output=True, text=True)


@pytest.mark.parametrize('args', [[], ['one']])
def test_usage(env, args):
    p = subprocess.run(['bash', RUNNER, *args], env=env, capture_output=True, text=True)
    assert p.returncode == 2 and len(p.stderr.splitlines()) == 1 and 'usage' in p.stderr.lower()


@pytest.mark.parametrize('spec', ['', ',', '\n', 'sol:1\nnope:1', 'sol:0', 'sol:-1', 'sol:no', 'sol:01', 'sol:1,sol:1', 'sol:1,', ',sol:1'])
def test_invalid_spec(tmp_path, env, spec):
    p = run(tmp_path, env, spec)
    assert p.returncode == 2 and not (tmp_path / 'run').exists()
    if spec == 'sol:1,sol:1': assert 'sol:2' in p.stderr


@pytest.mark.parametrize('entry', [{'channel': 'oops', 'model': 'm'}, {'channel': 'codex', 'model': ''},
    {'channel': 'codex', 'model': 'm', 'effort': 1}, {'channel': 'codex', 'model': 'm', 'web': 'yes'}, []])
def test_registry_invalid_entry(tmp_path, env, entry):
    (tmp_path / 'heads.json').write_text(json.dumps({'heads': {'bad': entry}}))
    for args in [('list',), ('model', 'bad'), ('call', 'bad', 'b', 'o', 'l')]:
        p = cli(env, *args)
        assert p.returncode == 2 and 'heads.json' in p.stderr and 'bad' in p.stderr and 'Traceback' not in p.stderr
    assert run(tmp_path, env, 'bad:1').returncode == 2
    assert not (tmp_path / 'run').exists()


@pytest.mark.parametrize('data', ['{', '{"heads": []}', '{"heads":{"../bad":null}}'])
def test_broken_registry_available(tmp_path, env, data):
    (tmp_path / 'heads.json').write_text(data)
    assert cli(env, 'list').returncode == 2
    p = cli(env, 'available')
    assert p.returncode == 0 and 'agent' in p.stdout and 'warning' in p.stderr.lower()


def test_retry_and_different_brief(tmp_path, env):
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'codex', 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo fresh > "$2"; shift; done'))
    assert run(tmp_path, env).returncode == 0
    assert 'skip sol-1' in run(tmp_path, env).stdout
    p = run(tmp_path, env, text='different')
    assert p.returncode == 2 and 'run dir already holds a different brief; use a new run dir' in p.stderr
    assert (tmp_path / 'run/brief.md').read_text() == 'brief'
    (tmp_path / 'run/out/sol-1.md').unlink()
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'codex', 'exit 0'))
    assert run(tmp_path, env).returncode == 1
    assert meta(tmp_path, 'sol')['status'] == 'error'
    assert not (tmp_path / 'run/tmp/sol-1/answer.md').exists()


def agy_env(tmp_path, env, diagnostic=''):
    (tmp_path / '.gemini/config/projects').mkdir(parents=True)
    body = '''if [ "$1" = models ]; then echo gemini-3.8-flash-high; exit; fi
python3 - "$@" <<'INNER'
import json,os,sys
print(json.dumps({'response': 'auto-denied ' + os.getcwd() + ' ' + ' '.join(sys.argv[1:])}))
INNER
''' + diagnostic
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'agy', body))
    return env


def test_agy_project_cwd_and_response(tmp_path, env):
    agy_env(tmp_path, env)
    p = run(tmp_path, env, 'gemini:1')
    assert p.returncode == 0, p.stdout + p.stderr
    answer = (tmp_path / 'run/out/gemini-1.md').read_text()
    assert '--project thinktank' in answer and str(tmp_path / 'run/tmp/gemini-1') in answer


@pytest.mark.parametrize('diagnostic', ['echo auto-denied', 'echo "no output produced"'])
def test_agy_diagnostics_fail(tmp_path, env, diagnostic):
    agy_env(tmp_path, env, diagnostic)
    assert run(tmp_path, env, 'gemini:1').returncode == 1
    assert not (tmp_path / 'run/out/gemini-1.md').exists()


def test_agy_missing_setup_other_heads_run(tmp_path, env):
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'agy', 'if [ "$1" = models ]; then echo gemini-3.8-flash-high; else echo SHOULD-NOT-RUN; fi'),
                             fake_bin(tmp_path, 'codex', 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo ok > "$2"; shift; done'))
    assert run(tmp_path, env, 'gemini:1,sol:1').returncode == 1
    assert 'agy not set up' in (tmp_path / 'run/log/gemini-1.log').read_text()
    assert meta(tmp_path, 'sol')['status'] == 'ok'


@pytest.mark.parametrize('head,channel', [('gemini', 'agy'), ('oc', 'opencode')])
def test_argv_size(tmp_path, env, head, channel):
    if channel == 'agy': agy_env(tmp_path, env)
    else: (tmp_path / 'heads.json').write_text(json.dumps({'heads': {'oc': {'channel': channel, 'model': 'm'}}}))
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'codex', 'while [ $# -gt 0 ]; do [ "$1" = -o ] && echo ok > "$2"; shift; done'), env['PATH'])
    assert run(tmp_path, env, head + ':1,sol:1', 'x' * 100001).returncode == 1
    assert meta(tmp_path, 'sol')['status'] == 'ok' 
    assert 'brief too large for argv: 100001 bytes' in (tmp_path / f'run/log/{head}-1.log').read_text()


def test_agy_project_helper(tmp_path, env):
    m = load(env); folder = str(tmp_path / 'scope')
    assert m.ensure_agy_project('thinktank', [], folder)[0] == 'error'
    projects = tmp_path / '.gemini/config/projects'; projects.mkdir(parents=True)
    assert m.ensure_agy_project('thinktank', [], folder)[0] == 'created'
    path = next(projects.glob('*.json')); d = json.loads(path.read_text())
    assert d['permissionGrants']['permissionGrants']['allow'] == [] and os.path.isdir(folder)
    d['permissionGrants']['permissionGrants']['allow'] = ['*']; path.write_text(json.dumps(d))
    assert m.ensure_agy_project('thinktank', [], folder)[0] == 'repaired'
    assert m.ensure_agy_project('thinktank', [], folder)[0] == 'present'
    d = json.loads(path.read_text()); d['projectResources'] = {}; path.write_text(json.dumps(d))
    assert m.ensure_agy_project('thinktank', [], folder)[0] == 'repaired'
    assert cli(env, 'agy-project', 'thinktank', folder, '--grant', 'read_url(*)').returncode == 0


@pytest.mark.parametrize('args', [[], ['sol'], ['sol','b','o','l','--timeout','bad'], ['sol','b','o','l','--timeout']])
def test_call_usage(env, args):
    p = cli(env, 'call', *args)
    assert p.returncode == 2 and 'usage:' in p.stderr and 'Traceback' not in p.stderr


def alive(pid):
    for _ in range(100):
        try: os.kill(pid, 0)
        except ProcessLookupError: return False
        if sys.platform.startswith('linux'):
            try:
                with open(f'/proc/{pid}/stat') as f:
                    if f.read().rsplit(')', 1)[1].split()[0] == 'Z': return False
            except FileNotFoundError: return False
        time.sleep(.02)
    return True


def wait_file(path):
    for _ in range(100):
        if path.exists() and path.read_text().strip(): return int(path.read_text())
        time.sleep(.05)
    pytest.fail('child did not start')


def test_group_kills_ignoring_grandchild(tmp_path, env):
    m = load(env); pidfile = tmp_path / 'pid'
    code = "import subprocess,time; subprocess.Popen(['bash','-c', 'trap \"\" TERM; echo $$ > \"$1\"; exec sleep 60', 'bash', " + repr(str(pidfile)) + "]); time.sleep(60)"
    try:
        assert m.run_in_group([sys.executable, '-c', code], 1) == -1
        pid = wait_file(pidfile)
        assert not alive(pid)
    finally:
        if pidfile.exists():
            try: os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError: pass


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGINT])
@pytest.mark.parametrize("channel", ["codex", "opencode"])
def test_runner_term_kills_child(tmp_path, env, sig, channel):
    pidfile = tmp_path / 'pid'
    env['PIDFILE'] = str(pidfile)
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, channel, 'echo $$ > "$PIDFILE"; trap "" TERM; exec sleep 60'))
    (tmp_path / 'heads.json').write_text(json.dumps({'heads': {'sleeper': {'channel':channel, 'model':'m'}}}))
    brief = tmp_path / 'input.md'; brief.write_text('brief')
    p = subprocess.Popen(['bash', RUNNER, str(tmp_path / 'run'), str(brief), '--heads', 'sleeper:1'], env=env,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pid = wait_file(pidfile); p.send_signal(sig); p.wait(timeout=10)
        assert not alive(pid)
    finally:
        p.kill() if p.poll() is None else None
        if pidfile.exists():
            try: os.kill(int(pidfile.read_text()), signal.SIGKILL)
            except ProcessLookupError: pass


def test_opencode_cwd_and_error(tmp_path, env, monkeypatch):
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'opencode', 'pwd; exit "${FAIL:-0}"'))
    monkeypatch.setenv('PATH', env['PATH']); m = load(env)
    out = tmp_path / 'answer'
    with open(tmp_path / 'log', 'w') as log:
        assert m.call_opencode({'model':'m'}, 'hi', out, log, 5)[0] == 'ok'
    assert out.read_text().strip() != os.getcwd()
    (tmp_path / 'heads.json').write_text(json.dumps({'heads': {'oc': {'channel':'opencode','model':'m'}}}))
    env['FAIL'] = '1'
    assert run(tmp_path, env, 'oc:1').returncode == 1
    assert not (tmp_path / 'run/out/oc-1.md').exists()
    env['FAIL'] = '0'
    assert run(tmp_path, env, 'oc:1').returncode == 0
    assert (tmp_path / 'run/out/oc-1.md').read_text().strip() == str(tmp_path / 'run/tmp/oc-1')


@pytest.mark.parametrize('url', ['http://example.com', 'file:///tmp/foo', 'ftp://localhost', 'http://127.0.0.1.evil.test'])
def test_openrouter_url_refused(tmp_path, env, url, monkeypatch):
    m = load(env); m.OPENROUTER_URL = url
    def forbidden(*a, **kw): pytest.fail('request sent')
    monkeypatch.setattr(m.urllib.request, 'urlopen', forbidden)
    log = io.StringIO()
    assert m.call_openrouter({'model':'m'}, 'hi', tmp_path / 'out', log, 1)[0] == 'error'
    assert 'URL' in log.getvalue()


@pytest.mark.parametrize('response', [[], {}, {'choices':[],'usage':{}}, {'choices':[{'message':{'content':123}}],'usage':{}},
    {'choices':[{'message':{'content':'answer'}}],'usage':[]}, {'choices':[{'message':{'content':'answer'}}],'usage':{'completion_tokens_details':[]}}])
def test_bad_openrouter_response(tmp_path, env, response):
    m = load(env); m.OPENROUTER_URL = 'http://127.0.0.1:1234'
    from unittest.mock import patch
    with patch.object(m.urllib.request, 'urlopen', return_value=io.StringIO(json.dumps(response))):
        log = io.StringIO()
        assert m.call_openrouter({'model':'m'}, 'hi', tmp_path / 'out', log, 2)[0] == 'error'
        assert 'response' in log.getvalue().lower()


@pytest.mark.parametrize('args', [['--topic'], ['--agent-tokens'], ['--agent-tokens','sol=oops,opus=2']])
def test_ledger_flags(tmp_path, env, args):
    env['THINKTANK_LEDGER'] = str(tmp_path / 'ledger.jsonl')
    p = subprocess.run([sys.executable, os.path.join(SCRIPTS,'ledger.py'), str(tmp_path / 'run'), *args], env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    if args[-1] == 'sol=oops,opus=2': assert 'warning' in p.stderr.lower()


def test_call_nonzero_removes_partial_answer(tmp_path, env):
    import shlex
    body = '''if [ "$2" = call ]; then
  echo partial > "$5"; echo '1 2 3 4'; exit 7
fi
exec ''' + shlex.quote(sys.executable) + ' "$@"'
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'python3', body))
    p = run(tmp_path, env, 'or-gpt:1')
    assert p.returncode == 1 and meta(tmp_path, 'or-gpt')['status'] == 'error'
    assert not (tmp_path / 'run/out/or-gpt-1.md').exists()


def test_openrouter_http500_runner(tmp_path, env):
    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(500); self.end_headers(); self.wfile.write(b'failed')
        def log_message(self, *a): pass
    srv = http.server.HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        env['OPENROUTER_URL'] = f'http://127.0.0.1:{srv.server_port}'
        env['OPENROUTER_API_KEY'] = 'test-only'
        assert run(tmp_path, env, 'or-gpt:1').returncode == 1
        assert 'HTTP 500' in (tmp_path / 'run/log/or-gpt-1.log').read_text()
        assert not (tmp_path / 'run/out/or-gpt-1.md').exists()
    finally: srv.shutdown(); srv.server_close()


def test_openrouter_log_allowlist(tmp_path, env):
    from unittest.mock import patch
    m = load(env); m.OPENROUTER_URL = 'https://example.com/completions'
    response = {'id':'id', 'model':'m', 'provider':'p', 'usage':{'completion_tokens_details':None}, 'private':'excluded',
                'choices':[{'message':{'content':'answer'}}]}
    log = io.StringIO()
    with patch.object(m.urllib.request, 'urlopen', return_value=io.StringIO(json.dumps(response))):
        assert m.call_openrouter({'model':'m'}, 'hi', tmp_path / 'out', log, 2)[0] == 'ok'
    assert set(json.loads(log.getvalue())) == {'id','model','provider','usage'}


def test_failed_meta_is_retried(tmp_path, env):
    out = tmp_path / 'run/out'; out.mkdir(parents=True)
    (out / 'sol-1.md').write_text('partial')
    (out / 'sol-1.meta.json').write_text(json.dumps({'status':'error'}))
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'codex', 'exit 0'))
    assert run(tmp_path, env).returncode == 1
    assert not (out / 'sol-1.md').exists()


def test_group_exception_cleanup(tmp_path, env, monkeypatch):
    m = load(env)
    p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], start_new_session=True)
    def fail(**kwargs): raise RuntimeError('interrupted')
    monkeypatch.setattr(p, 'communicate', fail)
    monkeypatch.setattr(m.subprocess, 'Popen', lambda *a, **kw: p)
    with pytest.raises(RuntimeError, match='interrupted'): m.run_in_group(['unused'], 10)
    assert p.poll() is not None


def test_relative_runner_uses_absolute_registry_path(tmp_path, env):
    import shlex
    body = 'case "$1" in /*|-|-c) ;; *) echo "relative helper path" >&2; exit 9;; esac\nexec ' + shlex.quote(sys.executable) + ' "$@"'
    env['PATH'] = clean_path(tmp_path, fake_bin(tmp_path, 'python3', body))
    brief = tmp_path / 'brief.md'; brief.write_text('brief')
    p = subprocess.run(['bash', 'scripts/thinktank.sh', str(tmp_path / 'run'), str(brief), '--heads', 'opus:1'],
                       cwd=os.path.dirname(SCRIPTS), env=env, capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
