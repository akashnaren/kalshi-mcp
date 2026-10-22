from __future__ import annotations

import fcntl
import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from kalshi_readonly.build import parse_build_sha

ROOT = Path(__file__).resolve().parents[1]
_CHECKOUT = (ROOT / ".git").exists()
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


def _refresh(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(ROOT / "scripts" / "refresh-host.sh"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _python_stub(*, log: bool) -> str:
    real = shutil.which("python3")
    assert real is not None
    logged = ""
    if log:
        logged = "printf '%s %s\\n' \"$(pwd)\" \"python3 $*\" >> \"$REFRESH_LOG\"\n"
    return (
        "#!/bin/sh\n"
        f"{logged}"
        'if [ "$1" = "-c" ]; then\n'
        f"  exec {shlex.quote(real)} \"$@\"\n"
        "fi\n"
        "exit 0\n"
    )


def _tool_stubs(tmp_path: Path) -> dict[str, str]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    npm.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text(_python_stub(log=False), encoding="ascii")
    python.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    return env


def test_refresh_help_includes_examples() -> None:
    proc = _refresh("--help")
    assert proc.returncode == 0, proc.stderr
    assert "Examples:" in proc.stdout
    assert "DEPLOYED_SHA" in proc.stdout
    assert "scripts/refresh-host.sh --dry-run --dest /home/box/mcp/kalshi-mcp-main 1ce6d72" in proc.stdout


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_dry_run_writes_nothing(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    proc = _refresh("--dry-run", "--dest", str(dest), _git_sha())
    assert proc.returncode == 0, proc.stderr
    assert not dest.exists()
    assert not (tmp_path / "host.refresh.lock").exists()
    assert "git archive" in proc.stdout
    assert _git_sha() in proc.stdout
    again = _refresh("--dry-run", "--dest", str(dest), _git_sha())
    assert again.returncode == 0, again.stderr
    assert again.stdout == proc.stdout
    assert not dest.exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
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


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_already_installed_is_a_noop(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    sha = _git_sha()
    (dest / "package.json").write_text('{"name": "kalshi-readonly"}\n', encoding="utf-8")
    (dest / "server.py").write_text("\n", encoding="utf-8")
    stamp = dest / "DEPLOYED_SHA"
    stamp.write_text(sha + "\n", encoding="ascii")
    before = stamp.read_bytes()
    proc = _refresh("--dest", str(dest), sha)
    assert proc.returncode == 0, proc.stderr
    assert f"already installed: {sha}" in proc.stdout
    assert stamp.read_bytes() == before
    assert not (dest / "node_modules").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_foreign_directory(tmp_path: Path) -> None:
    dest = tmp_path / "other-server"
    dest.mkdir()
    secret = dest / "private.pem"
    secret.write_text("secret\n", encoding="ascii")
    proc = _refresh("--dest", str(dest), _git_sha())
    assert proc.returncode == 2, proc.stderr
    assert secret.read_text(encoding="ascii") == "secret\n"
    assert dest.is_dir()
    assert not (tmp_path / "other-server.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_stray_deployed_sha(tmp_path: Path) -> None:
    dest = tmp_path / "other"
    dest.mkdir()
    (dest / "notes.txt").write_text("keep\n", encoding="ascii")
    (dest / "DEPLOYED_SHA").write_text("", encoding="ascii")
    proc = _refresh("--dest", str(dest), _git_sha())
    assert proc.returncode == 2, proc.stderr
    assert (dest / "notes.txt").read_text(encoding="ascii") == "keep\n"
    assert not (tmp_path / "other.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_does_not_treat_a_dirty_stamp_as_installed(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    (dest / "package.json").write_text('{"name": "kalshi-readonly"}\n', encoding="utf-8")
    (dest / "server.py").write_text("\n", encoding="utf-8")
    sha = _git_sha()
    stamp = dest / "DEPLOYED_SHA"
    stamp.write_text(sha + "\nnot-hex\n", encoding="ascii")
    proc = _refresh("--dry-run", "--dest", str(dest), sha)
    assert proc.returncode == 0, proc.stderr
    assert "already installed" not in proc.stdout
    assert "current: unknown" in proc.stdout
    assert stamp.read_text(encoding="ascii") == sha + "\nnot-hex\n"


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_filesystem_root() -> None:
    proc = _refresh("--dry-run", "--dest", "/", _git_sha())
    assert proc.returncode == 2
    assert not Path("/DEPLOYED_SHA").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_publish_writes_the_stamp(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "commands"
    (bin_dir / "npm").write_text(
        "#!/bin/sh\nprintf '%s %s\\n' \"$(pwd)\" \"npm $*\" >> \"$REFRESH_LOG\"\nexit 0\n",
        encoding="ascii",
    )
    (bin_dir / "python3").write_text(_python_stub(log=True), encoding="ascii")
    (bin_dir / "npm").chmod(0o755)
    (bin_dir / "python3").chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    env["REFRESH_LOG"] = str(log)
    dest = tmp_path / "host"
    sha = _git_sha()
    proc = subprocess.run(
        ["bash", str(ROOT / "scripts" / "refresh-host.sh"), "--dest", str(dest), sha],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert (dest / "DEPLOYED_SHA").read_text(encoding="ascii").strip() == sha
    assert parse_build_sha((dest / "DEPLOYED_SHA").read_bytes()) == sha[:12]
    assert (dest / "package.json").is_file()
    lines = log.read_text(encoding="ascii").splitlines()
    assert any(line.endswith(" npm ci --include=dev") and ".staging" in line for line in lines)
    assert any(line.endswith(" npm run build") and ".staging" in line for line in lines)
    assert any(line.endswith(" python3 -m pytest -q") and ".staging" in line for line in lines)
    again = subprocess.run(
        ["bash", str(ROOT / "scripts" / "refresh-host.sh"), "--dest", str(dest), sha],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert again.returncode == 0, again.stderr
    assert f"already installed: {sha}" in again.stdout


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_an_unreadable_directory(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    kept = dest / "keep.txt"
    kept.write_text("keep\n", encoding="ascii")
    previous = tmp_path / "host.previous"
    dest.chmod(0)
    try:
        proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
        assert proc.returncode == 2, proc.stderr
        assert "cannot read --dest" in proc.stderr
        assert not previous.exists()
        assert not (tmp_path / "host.refresh.lock").exists()
    finally:
        if dest.exists():
            dest.chmod(0o700)
        if previous.exists():
            previous.chmod(0o700)
    assert kept.read_text(encoding="ascii") == "keep\n"
    assert dest.is_dir()
    assert not previous.exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_git_clone(tmp_path: Path) -> None:
    dest = tmp_path / "clone"
    dest.mkdir()
    (dest / "package.json").write_text('{"name": "kalshi-readonly"}\n', encoding="utf-8")
    (dest / "server.py").write_text("\n", encoding="utf-8")
    kept = dest / "keep.txt"
    kept.write_text("uncommitted\n", encoding="ascii")
    subprocess.run(["git", "init"], cwd=dest, check=True, capture_output=True, text=True)
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert kept.read_text(encoding="ascii") == "uncommitted\n"
    assert dest.is_dir()
    assert not (tmp_path / "clone.previous").exists()
    assert not (tmp_path / "clone.staging").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_symlinked_lock(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    precious = tmp_path / "precious.txt"
    precious.write_text("keep\n", encoding="ascii")
    lock = tmp_path / "host.refresh.lock"
    lock.symlink_to(precious)
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert precious.read_text(encoding="ascii") == "keep\n"
    assert lock.is_symlink()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_symlinked_staging_dir(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep\n", encoding="ascii")
    (tmp_path / "host.staging").symlink_to(precious)
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert (precious / "keep.txt").read_text(encoding="ascii") == "keep\n"
    assert not dest.joinpath("keep.txt").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_symlinked_previous_old(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    precious = tmp_path / "precious"
    precious.mkdir()
    (precious / "keep.txt").write_text("keep\n", encoding="ascii")
    (tmp_path / "host.previous.old").symlink_to(precious)
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert (precious / "keep.txt").read_text(encoding="ascii") == "keep\n"


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_foreign_previous_dir(tmp_path: Path) -> None:
    previous = tmp_path / "host.previous"
    previous.mkdir()
    (previous / "keep.txt").write_text("keep\n", encoding="ascii")
    proc = _refresh("--dest", str(tmp_path / "host"), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert (previous / "keep.txt").read_text(encoding="ascii") == "keep\n"
    assert not (tmp_path / "host").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_takes_the_lock_before_installing(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    lock_path = tmp_path / "host.refresh.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    finally:
        os.close(fd)
    assert proc.returncode == 1, proc.stderr
    assert "another refresh holds" in proc.stderr
    assert list(dest.iterdir()) == []
    assert not (tmp_path / "host.staging").exists()
    assert not (tmp_path / "host.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_package_name_mismatch(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    (dest / "package.json").write_text('{"name": "not-kalshi"}\n', encoding="utf-8")
    (dest / "server.py").write_text("\n", encoding="utf-8")
    kept = dest / "keep.txt"
    kept.write_text("keep\n", encoding="ascii")
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert kept.read_text(encoding="ascii") == "keep\n"
    assert dest.is_dir()
    assert not (tmp_path / "host.previous").exists()
    assert not (tmp_path / "host.refresh.lock").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_does_not_create_a_lock_when_dest_is_refused(tmp_path: Path) -> None:
    dest = tmp_path / "other"
    dest.mkdir()
    (dest / "private.pem").write_text("secret\n", encoding="ascii")
    proc = _refresh("--dest", str(dest), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert (dest / "private.pem").read_text(encoding="ascii") == "secret\n"
    assert not (tmp_path / "other.refresh.lock").exists()
    assert not (tmp_path / "other.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_symlinked_dest(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "host"
    link.symlink_to(target, target_is_directory=True)
    proc = _refresh("--dest", str(link), _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert "symlink" in proc.stderr
    assert list(target.iterdir()) == []
    assert not (tmp_path / "real.refresh.lock").exists()
    assert not (tmp_path / "host.refresh.lock").exists()
    assert not (tmp_path / "real.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_rechecks_a_foreign_dest_after_the_lock(tmp_path: Path) -> None:
    dest = tmp_path / "host"
    dest.mkdir()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    npm = bin_dir / "npm"
    npm.write_text("#!/bin/sh\nexit 0\n", encoding="ascii")
    npm.chmod(0o755)
    python = bin_dir / "python3"
    python.write_text(_python_stub(log=False), encoding="ascii")
    python.chmod(0o755)
    real_flock = shutil.which("flock")
    assert real_flock is not None
    quoted_dest = shlex.quote(str(dest))
    flock = bin_dir / "flock"
    flock.write_text(
        "#!/bin/sh\n"
        f"rm -rf {quoted_dest}\n"
        f"mkdir -p {quoted_dest}\n"
        f"printf 'secret\\n' > {quoted_dest}/private.pem\n"
        f"exec {shlex.quote(real_flock)} \"$@\"\n",
        encoding="ascii",
    )
    flock.chmod(0o755)
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env['PATH']}"
    proc = _refresh("--dest", str(dest), _git_sha(), env=env)
    assert proc.returncode == 2, proc.stderr
    assert (dest / "private.pem").read_text(encoding="ascii") == "secret\n"
    assert not (tmp_path / "host.previous").exists()


@pytest.mark.skipif(not _CHECKOUT, reason="refresh checks need the git checkout, not a git archive")
def test_refresh_refuses_a_symlinked_dest_with_a_trailing_slash(tmp_path: Path) -> None:
    target = tmp_path / "real"
    target.mkdir()
    link = tmp_path / "lnk"
    link.symlink_to(target, target_is_directory=True)
    proc = _refresh("--dest", f"{link}/", _git_sha(), env=_tool_stubs(tmp_path))
    assert proc.returncode == 2, proc.stderr
    assert "symlink" in proc.stderr
    assert list(target.iterdir()) == []
    assert not (tmp_path / "real.refresh.lock").exists()
    assert not (tmp_path / "lnk.refresh.lock").exists()
    assert not (tmp_path / "real.previous").exists()
