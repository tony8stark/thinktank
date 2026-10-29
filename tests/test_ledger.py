import json, os, subprocess, sys, tempfile, pathlib
HERE = pathlib.Path(__file__).resolve().parent.parent

def test_ledger():
    d = tempfile.mkdtemp(); out = pathlib.Path(d, "out"); out.mkdir()
    (out / "astra-1.md").write_text("x")
    json.dump({"head": "astra-1", "channel": "codex", "model": "gpt-6-astra", "status": "ok", "latency_s": 40,
               "prompt_tok": 100, "completion_tok": 50, "reasoning_tok": 20, "cost_usd": None, "chars": 1},
              open(out / "astra-1.meta.json", "w"))
    (out / "opus-1.md").write_text("PANEL: opus answering\nfine")   # Agent head, no meta yet
    ledger = pathlib.Path(d, "ledger.jsonl")
    r = subprocess.run([sys.executable, HERE / "scripts/ledger.py", d, "--topic", "t", "--agreed", "2", "--likely", "1",
                        "--single", "3", "--single-confirmed", "1"],
                       env={**os.environ, "THINKTANK_LEDGER": str(ledger)}, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    m = json.load(open(out / "opus-1.meta.json"))
    assert m["channel"] == "agent" and m["prompt_tok"] is None and m["status"] == "ok"
    line = json.loads(ledger.read_text().strip())
    assert line["heads"] == ["astra-1", "opus-1"]
    assert line["tokens"]["codex"]["completion_tok"] == 50 and line["single_confirmed"] == 1
    assert "codex" in r.stdout and "agent" in r.stdout


def run_ledger(d, env):
    out = pathlib.Path(d, "out"); out.mkdir(parents=True, exist_ok=True)
    (out / "opus-1.md").write_text("PANEL: opus answering\nfine")
    clean = {k: v for k, v in os.environ.items() if k not in ("THINKTANK_LEDGER", "CLAUDE_CONFIG_DIR")}
    return subprocess.run([sys.executable, HERE / "scripts/ledger.py", d, "--topic", "t"], env={**clean, **env},
                          capture_output=True, text=True)


def test_ledger_follows_claude_config_dir(tmp_path):
    cfg = tmp_path / "claude-config"
    r = run_ledger(str(tmp_path / "run"), {"CLAUDE_CONFIG_DIR": str(cfg), "HOME": str(tmp_path / "home")})
    assert r.returncode == 0, r.stderr
    assert (cfg / "thinktank/ledger.jsonl").exists() and not (tmp_path / "home/.claude").exists()
    explicit = tmp_path / "explicit.jsonl"
    r = run_ledger(str(tmp_path / "run2"), {"CLAUDE_CONFIG_DIR": str(cfg), "THINKTANK_LEDGER": str(explicit)})
    assert r.returncode == 0 and explicit.exists()


def test_factcheck_state_follows_claude_config_dir(tmp_path):
    code = "import factcheck; print(factcheck.LEDGER); print(factcheck.AGY_DIR)"
    clean = {k: v for k, v in os.environ.items() if k not in ("FACTCHECK_LEDGER", "CLAUDE_CONFIG_DIR")}
    r = subprocess.run([sys.executable, "-c", code], cwd=HERE / "scripts", capture_output=True, text=True,
                       env={**clean, "CLAUDE_CONFIG_DIR": str(tmp_path), "THINKTANK_HEADS": str(tmp_path / "none.json")})
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == [str(tmp_path / "thinktank/factcheck-ledger.jsonl"), str(tmp_path / "thinktank/factcheck-agy")]
