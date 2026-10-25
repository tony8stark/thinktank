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
