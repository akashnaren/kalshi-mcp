from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_STAMP = "1ce6d72abcde0123456789abcdef0123456789ab"
_OTHER = "ffffffffffffffffffffffffffffffffffffffff"
_CHILD = """
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
mode = sys.argv[2]
rewrite = sys.argv[3]
sys.path.insert(0, str(root))

import kalshi_readonly.tools as tools

def fake_public_get(path, query=None):
    return {
        "exchange_active": True,
        "trading_active": True,
        "build_sha": "from-payload",
    }

tools.public_get = fake_public_get
if mode == "rewrite-before-call":
    (root / "DEPLOYED_SHA").write_text(rewrite, encoding="ascii")
out = tools.exchange_status()
print(json.dumps({"build_sha": out["build_sha"]}))
"""


def _install(tmp_path: Path) -> Path:
    root = tmp_path / "install"
    shutil.copytree(
        ROOT / "kalshi_readonly",
        root / "kalshi_readonly",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    return root


def _status(root: Path, mode: str, rewrite: str = "") -> str:
    env = {
        "PATH": os.environ["PATH"],
        "PYTHONPATH": str(root),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    proc = subprocess.run(
        [sys.executable, "-c", _CHILD, str(root), mode, rewrite],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["build_sha"]


def test_exchange_status_build_sha_is_the_files_first_12_chars(tmp_path: Path) -> None:
    root = _install(tmp_path)
    (root / "DEPLOYED_SHA").write_text(_STAMP + "\n", encoding="ascii")
    assert _status(root, "present") == _STAMP[:12]


def test_exchange_status_build_sha_is_unknown_when_the_file_is_absent(tmp_path: Path) -> None:
    root = _install(tmp_path)
    assert _status(root, "absent") == "unknown"


def test_exchange_status_build_sha_is_read_once_at_startup(tmp_path: Path) -> None:
    root = _install(tmp_path)
    (root / "DEPLOYED_SHA").write_text(_STAMP + "\n", encoding="ascii")
    assert _status(root, "rewrite-before-call", _OTHER + "\n") == _STAMP[:12]


def test_parse_build_sha_accepts_one_hex_token_or_unknown() -> None:
    from kalshi_readonly.build import parse_build_sha

    twelve = b"ABCDEF012345"
    full = (_STAMP + "\n").encode("ascii")
    assert parse_build_sha(full) == _STAMP[:12]
    assert parse_build_sha(twelve) == "ABCDEF012345"
    assert parse_build_sha(None) == "unknown"
    assert parse_build_sha(b"") == "unknown"
    assert parse_build_sha(b"not-a-sha\n") == "unknown"
    assert parse_build_sha(b"0123456\n") == "unknown"
    assert parse_build_sha(b"0123456789ab\nffff\n") == "unknown"
    assert parse_build_sha(b"\xff\xff") == "unknown"
    assert parse_build_sha(b"0123456789ab" + b" " * 200) == "unknown"


def _git_sha() -> str:
    return subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=ROOT,
        text=True,
    ).strip()


def _refresh(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "refresh-host.sh"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_refresh_help_includes_examples() -> None:
    proc = _refresh("--help")
    assert proc.returncode == 0, proc.stderr
    assert "Examples:" in proc.stdout
    assert "DEPLOYED_SHA" in proc.stdout
    assert "scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main 1ce6d72" in proc.stdout


def test_refresh_dry_run_writes_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    proc = _refresh("--dry-run", "--dest", str(dest), _git_sha())
    assert proc.returncode == 0, proc.stderr
    assert not dest.exists()
    assert "git archive" in proc.stdout
    assert _git_sha() in proc.stdout
    again = _refresh("--dry-run", "--dest", str(dest), _git_sha())
    assert again.returncode == 0, again.stderr
    assert again.stdout == proc.stdout
    assert not dest.exists()


def test_refresh_refuses_bad_usage(tmp_path: Path) -> None:
    sha = _git_sha()
    missing = _refresh("--dest", str(tmp_path / "host"))
    assert missing.returncode == 2
    assert "scripts/refresh-host.sh --dry-run --dest" in missing.stderr
    relative = _refresh("--dry-run", "--dest", "relative-host", sha)
    assert relative.returncode == 2
    checkout = _refresh("--dry-run", "--dest", str(ROOT), sha)
    assert checkout.returncode == 2
    branch = _refresh("--dry-run", "--dest", str(tmp_path / "host"), "main")
    assert branch.returncode == 2


def test_refresh_already_installed_is_a_noop(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    sha = _git_sha()
    stamp = dest / "DEPLOYED_SHA"
    stamp.write_text(sha + "\n", encoding="ascii")
    before = stamp.read_bytes()
    proc = _refresh("--dest", str(dest), sha)
    assert proc.returncode == 0, proc.stderr
    assert f"already installed: {sha}" in proc.stdout
    assert stamp.read_bytes() == before
    assert not (dest / "node_modules").exists()
    assert not (dest / "package.json").exists()
