"""Unit tests for factcheck.py mechanics. Run: uv run --with pytest pytest tests/"""
import importlib.util, json, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(os.path.dirname(HERE), "scripts", "factcheck.py")
spec = importlib.util.spec_from_file_location("factcheck", SCRIPT)
fc = importlib.util.module_from_spec(spec); spec.loader.exec_module(fc)


def test_parse_spec_ids_and_efforts():
    heads = fc.parse_spec("astra:2,sol@ultra:1,opus")
    assert [h["head"] for h in heads] == ["astra-1", "astra-2", "sol.ultra-1", "opus-1"]
    assert heads[2]["effort"] == "ultra" and heads[0]["effort"] == "low" and heads[3]["channel"] == "agent"


def test_parse_spec_rejects_unknown_and_agy_effort():
    for bad in ("bogus:1", "gemini@high:1"):
        r = subprocess.run([sys.executable, "-c", f"import importlib.util as u;s=u.spec_from_file_location('f','{SCRIPT}');"
                            f"m=u.module_from_spec(s);s.loader.exec_module(m);m.parse_spec('{bad}')"], capture_output=True)
        assert r.returncode == 2


def test_parse_answer_fenced_and_trailing_comma():
    txt = 'PANEL: opus answering\n```json\n{"claims": [{"id": "C1", "verdict": "supported",}],}\n```'
    assert fc.parse_answer(txt)["claims"][0]["id"] == "C1"
    assert fc.parse_answer("no json here") is None


def test_match_quote_exact_table_and_numbers():
    page = fc.mnorm("General Rule (§ 6721(a)(1))\n$340\n$4,191,500\nMore text follows here.")
    assert fc.match_quote("General Rule (§ 6721(a)(1)) | $340 | $4,191,500", page)[0] == 1.0
    page2 = fc.mnorm("For payments made in 2026, the reporting threshold is $2,000 for most business payments.")
    assert fc.match_quote("For payments made in 2026, the reporting threshold is $2,000.", page2)[0] >= 0.9
    # same words, wrong number: must not pass as fuzzy
    s, _ = fc.match_quote("For payments made in 2026, the reporting threshold is $600 for most business payments.", page2)
    assert s < 0.6


def test_domain_and_nearby_exceptions():
    assert fc.domain("https://www.irs.gov/x") == "irs.gov"
    assert fc.domain("https://www.gov.uk/foo") == "www.gov.uk" or fc.domain("https://www.gov.uk/foo") == "gov.uk"
    assert fc.domain("https://travel.state.gov/a") == "state.gov"
    pg = fc.mnorm("The fee is EUR 20. Travellers under 18 are exempt unless they apply late.")
    s, pos = fc.match_quote("The fee is EUR 20.", pg)
    assert s == 1.0 and any("exempt" in n for n in fc.nearby_exceptions(pg, pos))


def _run(tmp, answers, claims):
    run = str(tmp)
    os.makedirs(os.path.join(run, "out"))
    open(os.path.join(run, "source.md"), "w").write("[¶1] The fee is €7. It is required since 2025.\n\n")
    open(os.path.join(run, "context.md"), "w").write("Checked as of: 2026-09-23.\n")
    json.dump(claims, open(os.path.join(run, "claims.json"), "w"))
    heads = fc.parse_spec(",".join(f"{h.rsplit('-', 1)[0]}:1" for h in answers))
    json.dump({"spec": "", "heads": heads, "jobs": [dict(h, job=h["head"]) for h in heads],
               "claims": [c["id"] for c in claims]}, open(os.path.join(run, "heads.json"), "w"))
    for h, a in answers.items():
        open(os.path.join(run, "out", h + ".md"), "w").write("```json\n" + json.dumps(a) + "\n```")
    fc.cmd_collect([run, "--no-fetch"])
    return json.load(open(os.path.join(run, "merged.json")))


def test_collect_unbacked_votes_do_not_count(tmp_path):
    claims = [{"id": "C1", "para": "¶1", "stakes": "money", "span": "The fee is €7.", "claim": "The fee is EUR 7."}]
    ev = [{"url": "https://example.gov/x", "quote": "The fee is EUR 20.", "kind": "official"}]
    m = _run(tmp_path, {"astra-1": {"claims": [{"id": "C1", "verdict": "contradicted", "correct": "EUR 20", "evidence": ev}]},
                        "sol-1": {"claims": [{"id": "C1", "verdict": "supported", "evidence": ev}]}},
             claims)
    row = m["rows"][0]
    # --no-fetch: nothing is verified, so no verdict is backed and the claim is unresolved and under review
    assert row["backed"] == {} and "UNRESOLVED" in row["flags"] and row["review"]
    assert m["stats"]["astra-1"]["checked"] == 1


def test_collect_missing_claim_is_not_checked(tmp_path):
    claims = [{"id": "C1", "span": "The fee is €7.", "claim": "x"}, {"id": "C2", "span": "It is required since 2025.", "claim": "y"}]
    m = _run(tmp_path, {"astra-1": {"claims": [{"id": "C1", "verdict": "supported", "evidence": []}]}}, claims)
    c2 = [r for r in m["rows"] if r["id"] == "C2"][0]
    assert c2["verdicts"]["astra-1"] == "not_checked" and "NOT_CHECKED" in c2["flags"]


def test_prep_agent_heads_are_general_purpose(tmp_path, capsys):
    # Opus heads run as general-purpose agents: the low-effort thinktank:fact-checker agent was removed
    run = str(tmp_path)
    open(os.path.join(run, "source.md"), "w").write("[¶1] The fee is €7. It is required since 2025.\n\n")
    open(os.path.join(run, "context.md"), "w").write("Checked as of: 2026-10-01.\n")
    claims = [{"id": "C1", "span": "The fee is €7.", "claim": "x"}, {"id": "C2", "span": "It is required since 2025.", "claim": "y"}]
    json.dump(claims, open(os.path.join(run, "claims.json"), "w"))
    fc.cmd_prep([run, "--heads", "astra:1,opus:2"])
    out = capsys.readouterr().out
    assert "subagent_type general-purpose" in out and "opus-1 (model opus)" in out and "opus-2 (model opus)" in out
    assert "fact-checker" not in out
    assert sorted(os.listdir(os.path.join(run, "briefs"))) == ["astra-1.md", "opus-1.md", "opus-2.md"]


def test_pro_reply_go_rows_are_confirmations_not_misses(tmp_path, monkeypatch, capsys):
    run = tmp_path / "run"; run.mkdir()
    (run / "verdicts.json").write_text(json.dumps([{"id": "C1", "status": "SUPPORTED"}, {"id": "C2", "status": "CONTRADICTED"}]))
    rows = [{"ref": "C1", "verdict": "GO"}, {"ref": "C1", "verdict": "FIX"}, {"ref": "C2", "verdict": "QUALIFY"},
            {"ref": "R7", "verdict": "confirm"}, {"ref": "R8", "verdict": "REFUTE"}, {"ref": "OVERALL", "verdict": "GO"},
            {"ref": "R9", "verdict": "UNCERTAIN"}]
    reply = tmp_path / "reply.md"; reply.write_text("```json\n" + json.dumps({"findings": rows}) + "\n```")
    monkeypatch.setattr(fc, "LEDGER", str(tmp_path / "ledger.jsonl"))
    fc.cmd_pro([str(run), "--round", "2", "--reply", str(reply)])
    misses = [json.loads(l) for l in open(tmp_path / "factcheck-misses.jsonl")]
    assert sorted((m["ref"], m["verdict"]) for m in misses) == [("C1", "FIX"), ("R8", "REFUTE"), ("R9", "UNCERTAIN")]
    led = [json.loads(l) for l in open(tmp_path / "ledger.jsonl")][-1]
    assert (led["pro_findings"], led["pro_confirmations"], led["on_flagged_claims"], led["on_supported_claims"],
            led["outside_ledger"]) == (4, 3, 1, 1, 2)
    assert "4 findings, 3 confirmations" in capsys.readouterr().out


def _fake_codex(tmp_path, monkeypatch, body):
    """Put a fake `codex` first on PATH. It gets the real argv, so `-o <file>` is where the answer goes."""
    b = tmp_path / "bin"; b.mkdir()
    exe = b / "codex"; exe.write_text("#!/bin/bash\nwhile [ $# -gt 0 ]; do [ \"$1\" = -o ] && OUT=\"$2\"; shift; done\n" + body)
    exe.chmod(0o755)
    monkeypatch.setenv("PATH", f"{b}:{os.environ['PATH']}")
    run = tmp_path / "run"; run.mkdir()
    (run / "context.md").write_text("ctx\n"); (run / "source.md").write_text("[¶1] The fee is €7.\n\n")
    return str(run)


def test_extract_uses_sol_and_writes_sol_json(tmp_path, monkeypatch, capsys):
    run = _fake_codex(tmp_path, monkeypatch, 'echo "model: $*" ; printf \'```json\\n{"claims":[{"para":"¶1","claim":"x"}]}\\n```\\n\' > "$OUT"\n')
    fc.cmd_extract([run])
    assert json.load(open(os.path.join(run, "extract", "sol.json"))) == [{"para": "¶1", "claim": "x"}]
    assert not os.path.exists(os.path.join(run, "extract", "astra.json"))
    assert "gpt-6.1-sol" in capsys.readouterr().out


def test_extract_retries_once_after_a_failed_attempt(tmp_path, monkeypatch):
    flag = tmp_path / "second"
    run = _fake_codex(tmp_path, monkeypatch, f'[ -e {flag} ] || {{ touch {flag}; echo "stream disconnected"; exit 1; }}\n'
                                             'echo \'{"claims":[]}\' > "$OUT"\n')
    fc.cmd_extract([run])
    assert json.load(open(os.path.join(run, "extract", "sol.json"))) == []
    assert "retry: codex exit 1" in open(os.path.join(run, "extract", "sol.log")).read()


def test_extract_timeout_kills_the_whole_process_group(tmp_path, monkeypatch, capsys):
    late = tmp_path / "late"
    # the wrapper starts a child that would write 3 s later, the way the node wrapper starts the native codex binary
    run = _fake_codex(tmp_path, monkeypatch, f'( sleep 3; touch {late} ) &\nwait\n')
    try: fc.cmd_extract([run, "--timeout", "1"]); raise AssertionError("no exit")
    except SystemExit as e: assert e.code == 1
    assert "timeout after 1s" in capsys.readouterr().err
    assert "TIMEOUT after 1s" in open(os.path.join(run, "extract", "sol.log")).read()
    import time; time.sleep(3.5)
    assert not late.exists()


def test_extract_timeout_scales_with_text():
    assert fc.extract_timeout("w " * 500) == 600
    assert fc.extract_timeout("w " * 3000) == 1800
    assert fc.extract_timeout("w " * 50000) == 3600


def test_extract_chunks_preserve_init_labels_and_boundaries(tmp_path):
    src = tmp_path / "input.md"
    src.write_text("first line\ncontinued\n\n" + "word " * 700 + "\n\n" + "last " * 700)
    run = tmp_path / "run"
    fc.cmd_init([str(run), str(src)])
    source = (run / "source.md").read_text().strip()
    chunks = fc.extract_chunks(source)
    assert len(chunks) == 2
    assert all(len(c.split()) <= 1200 for c in chunks)
    assert "\n\n".join(chunks) == source
    assert chunks[0].startswith("[¶1] first line\ncontinued")
    assert "[¶2]" in chunks[0] and chunks[1].startswith("[¶3]")
    assert fc.extract_chunks("[¶17] short\n\n[¶18] next") == ["[¶17] short\n\n[¶18] next"]
    # An indivisible paragraph keeps its label and content, even when it exceeds the target.
    huge = "[¶19] " + "word " * 1300
    assert fc.extract_chunks(huge.strip() + "\n\n[¶20] next") == [huge.strip(), "[¶20] next"]


def _extract_run(tmp_path, paragraphs=3):
    run = tmp_path / "run"; run.mkdir()
    (run / "source.md").write_text("\n\n".join(f"[¶{i}] " + "word " * 700 for i in range(1, paragraphs + 1)))
    (run / "context.md").write_text("ctx")
    return run


def test_extract_merge_source_order_and_per_chunk_timeout(tmp_path, monkeypatch, capsys):
    import re, time, threading
    run = _extract_run(tmp_path, 8)
    calls, active, peak = [], 0, 0
    lock = threading.Lock()

    def fake(cmd, stdin, logf, cwd, timeout):
        nonlocal active, peak
        text = stdin.read(); para = re.search(r"\[¶(\d+)\]", text).group(1)
        with lock:
            active += 1; peak = max(peak, active); calls.append((para, timeout))
        time.sleep((9 - int(para)) * 0.01)
        items = [{"para": "¶" + para, "claim": para + suffix} for suffix in ("a", "b")]
        open(cmd[cmd.index("-o") + 1], "w").write(json.dumps({"claims": items}))
        with lock: active -= 1
        return 0

    monkeypatch.setattr(fc, "run_codex", fake)
    monkeypatch.setattr(fc.shutil, "which", lambda n: "/bin/" + n)  # extract checks that codex is installed
    fc.cmd_extract([str(run), "--timeout", "42"])
    items = json.loads((run / "extract/sol.json").read_text())
    assert [c["claim"] for c in items] == [str(i) + s for i in range(1, 9) for s in ("a", "b")]
    assert len(calls) == 8 and all(t == 42 for _, t in calls)
    assert 1 < peak <= 6
    assert "extract: 16 claims from sol (gpt-6.1-sol)" in capsys.readouterr().out


def test_extract_recovers_all_rollout_messages_from_latest_attempt(tmp_path, monkeypatch):
    home = tmp_path / "home"; sessions = home / ".codex/sessions/2026/10/04"; sessions.mkdir(parents=True)
    monkeypatch.setattr(fc, "HOME", str(home))
    run = _extract_run(tmp_path, 1)
    text = '```json\n{"claims":[{"para":"¶1","claim":"first"},{"para":"¶1","claim":"second"}]}\n```'
    parts = [text[:38], text[38:71], text[71:]]
    rows = [{"type": "response_item", "payload": {"type": "message", "role": "user",
             "content": [{"type": "input_text", "text": "must not join this"}]}}]
    for part in parts:
        rows += [{"type": "event_msg", "payload": {"type": "agent_message", "message": part}},
                 {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                  "content": [{"type": "output_text", "text": part}]}}]
    (sessions / "rollout-current.jsonl").write_text("\n".join(json.dumps(r) for r in rows))
    calls = []

    def fake(cmd, stdin, logf, cwd, timeout):
        calls.append(timeout)
        if len(calls) == 1:
            logf.write("session id: old\n"); return 1
        logf.write("session id: current\n")
        open(cmd[cmd.index("-o") + 1], "w").write(parts[-1])
        return 0

    monkeypatch.setattr(fc, "run_codex", fake)
    monkeypatch.setattr(fc.shutil, "which", lambda n: "/bin/" + n)  # extract checks that codex is installed
    fc.cmd_extract([str(run)])
    assert len(calls) == 2
    assert calls == [fc.extract_timeout((run / "extract/brief.md").read_text())] * 2
    assert [c["claim"] for c in json.loads((run / "extract/sol.json").read_text())] == ["first", "second"]


def test_extract_one_chunk_failure_never_writes_partial_list(tmp_path, monkeypatch, capsys):
    import re
    run = _extract_run(tmp_path)
    (run / "extract").mkdir()
    (run / "extract/sol.json").write_text('[{"claim":"stale"}]')
    calls = []

    def fake(cmd, stdin, logf, cwd, timeout):
        para = re.search(r"\[¶(\d+)\]", stdin.read()).group(1); calls.append(para)
        if para == "2": return 1
        open(cmd[cmd.index("-o") + 1], "w").write(json.dumps({"claims": [{"para": "¶" + para, "claim": para}]}))
        return 0

    monkeypatch.setattr(fc, "run_codex", fake)
    monkeypatch.setattr(fc.shutil, "which", lambda n: "/bin/" + n)  # extract checks that codex is installed
    try: fc.cmd_extract([str(run)]); raise AssertionError("no exit")
    except SystemExit as e: assert e.code == 1
    assert calls.count("2") == 2
    assert "chunk 2/3" in capsys.readouterr().err
    assert not (run / "extract/sol.json").exists()


def test_extract_default_timeout_is_computed_per_chunk(tmp_path, monkeypatch):
    run = _extract_run(tmp_path, 2)
    (run / "source.md").write_text("[¶1] " + "word " * 1199 + "\n\n[¶2] short")
    calls = []

    def fake(cmd, stdin, logf, cwd, timeout):
        calls.append((stdin.read(), timeout))
        open(cmd[cmd.index("-o") + 1], "w").write("[]")
        return 0

    monkeypatch.setattr(fc, "run_codex", fake)
    monkeypatch.setattr(fc.shutil, "which", lambda n: "/bin/" + n)  # extract checks that codex is installed
    fc.cmd_extract([str(run)])
    assert len(calls) == 2 and all(t == fc.extract_timeout(b) for b, t in calls)
    assert sorted(t for _, t in calls)[0] == 600
    assert json.loads((run / "extract/sol.json").read_text()) == []


def test_extract_items_rejects_wrong_shape_and_accepts_empty_lists():
    assert fc.extract_items('{"claim":"tail record"}') is None
    assert fc.extract_items('{"claims":{}}') is None
    assert fc.extract_items('{"claims":[1]}') is None
    assert fc.extract_items('[]') == []
    assert fc.extract_items('{"claims":[]}') == []
