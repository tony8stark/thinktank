"""release.sh: one GitHub Release per plugin.json version, notes from CHANGELOG.md.
Offline: a temp git repo stands in for the checkout, a fake gh on PATH records the release."""
import json, os, pathlib, subprocess, sys
import pytest

SRC = pathlib.Path(__file__).resolve().parent.parent / ".github/release.sh"
FAKE_GH = f"""#!{sys.executable}
import json, os, sys
a = sys.argv[1:]
if a[:2] == ["release", "view"]: sys.exit(0 if os.environ.get("FAKE_RELEASED") else 1)
assert a[:2] == ["release", "create"]
open(os.environ["FAKE_LOG"], "w").write(json.dumps({{"argv": a[2:], "notes": sys.stdin.read()}}))
"""
CHANGELOG = """# Changelog

## 1.3.10 (2026-10-30)

- Longer version, same prefix.

## 1.3.1 (2026-10-29)

- First change.

- Second change.

## 1.3.0 (2026-10-28)

- Older.
"""


class Repo:
    def __init__(self, root):
        self.root = root
        (root / ".claude-plugin").mkdir()
        self.git("init", "-q")
        self.log = root.parent / "gh.json"
        self.bin = root.parent / "bin"; self.bin.mkdir()
        (self.bin / "gh").write_text(FAKE_GH); (self.bin / "gh").chmod(0o755)

    def git(self, *args):
        c = ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", *args]
        return subprocess.run(c, cwd=self.root, check=True, capture_output=True, text=True).stdout.strip()

    def commit(self, version, changelog=CHANGELOG):
        (self.root / ".claude-plugin/plugin.json").write_text(json.dumps({"version": version}, indent=2))
        (self.root / "CHANGELOG.md").write_text(changelog)
        self.git("add", "-A"); self.git("commit", "-q", "--allow-empty", "-m", "x")
        return self.git("rev-parse", "HEAD")

    def run(self, **env):
        return subprocess.run(["bash", str(SRC)], cwd=self.root, capture_output=True, text=True,
                              env=dict(os.environ, PATH=f"{self.bin}:{os.environ['PATH']}", FAKE_LOG=str(self.log), **env))


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"; root.mkdir()
    return Repo(root)


def test_release_uses_this_versions_entry_at_the_pushed_commit(repo):
    repo.commit("1.3.0")
    repo.commit("1.3.1")
    (repo.root / "README.md").write_text("later\n"); repo.git("add", "-A"); repo.git("commit", "-q", "-m", "docs")
    head = repo.git("rev-parse", "HEAD")
    r = repo.run()
    assert r.returncode == 0, r.stderr
    got = json.loads(repo.log.read_text())
    assert got["argv"] == ["v1.3.1", "--target", head, "--title", "v1.3.1", "--notes-file", "-"]
    assert got["notes"] == "- First change.\n\n- Second change.\n"


def test_entry_of_a_longer_version_with_the_same_prefix_is_not_used(repo):
    repo.commit("1.3.10")
    assert repo.run().returncode == 0
    assert json.loads(repo.log.read_text())["notes"] == "- Longer version, same prefix.\n"


def test_released_version_is_left_alone(repo):
    repo.commit("1.3.1")
    r = repo.run(FAKE_RELEASED="1")
    assert r.returncode == 0 and "already released" in r.stdout and not repo.log.exists()


def test_version_without_a_changelog_entry_fails(repo):
    repo.commit("1.4.0")
    r = repo.run()
    assert r.returncode == 1 and "no entry for 1.4.0" in r.stderr and not repo.log.exists()


def test_empty_changelog_entry_fails(repo):
    repo.commit("1.3.1", changelog="# Changelog\n\n## 1.3.1 (2026-10-29)\n\n## 1.3.0 (2026-10-28)\n\n- Older.\n")
    assert repo.run().returncode == 1 and not repo.log.exists()


@pytest.mark.parametrize("version", ["1.3", "v1.3.1", "1.3.1-beta", "1.3.1; touch x"])
def test_malformed_version_fails(repo, version):
    repo.commit(version)
    r = repo.run()
    assert r.returncode == 1 and "bad version" in r.stderr and not repo.log.exists()
