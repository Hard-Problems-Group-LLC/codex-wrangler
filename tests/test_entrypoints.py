import os
from pathlib import Path
import subprocess
import sys


def _write_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _fake_root_env(tmp_path: Path) -> dict[str, str]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    _write_executable(fake_bin / "id", "#!/usr/bin/env bash\necho 0\n")
    env = os.environ.copy()
    env["PATH"] = "{}:{}".format(fake_bin, env["PATH"])
    return env


def test_repo_wrapper_runs_from_checkout():
    repo_root = Path(__file__).resolve().parents[1]
    wrapper = repo_root / "codex-wrangler.py"
    completed = subprocess.run(
        [sys.executable, str(wrapper), "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "codex-wrangler" in completed.stdout


def test_install_stage_2_help_runs_from_checkout():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "install-stage-2.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "user-local non-development install" in completed.stdout
    assert "--mode {standard,dev,venv-only}" in completed.stdout
    assert "--system" in completed.stdout
    assert "--force-direct-run" in completed.stdout


def test_install_stage_2_rejects_direct_execution():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "scripts" / "install-stage-2.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--skip-direnv-install"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 1
    assert "must be started by ./install.sh" in completed.stderr


def test_install_sh_help_runs_from_checkout():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    completed = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "Install or bootstrap from this checkout." in completed.stdout
    assert "normal user: user-local non-development install" in completed.stdout
    assert "All remaining options are passed through" in completed.stdout
    assert "--mode {standard,dev,venv-only}" in completed.stdout
    assert "--system" in completed.stdout


def test_install_sh_rejects_system_without_root():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    completed = subprocess.run(
        ["bash", str(script), "--system"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "--system requires root privileges" in completed.stderr


def test_install_sh_rejects_sudo_without_system(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    env = _fake_root_env(tmp_path)
    env["SUDO_USER"] = "operator"
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "sudo is only supported together with --system" in completed.stderr


def test_install_sh_root_without_system_requires_confirmation(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    completed = subprocess.run(
        ["bash", str(script)],
        cwd=repo_root,
        env=_fake_root_env(tmp_path),
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 1
    assert "needs interactive confirmation" in completed.stderr


def test_install_sh_root_with_system_invokes_stage_2(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "install.sh"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    output = tmp_path / "python-args.txt"
    _write_executable(fake_bin / "id", "#!/usr/bin/env bash\necho 0\n")
    _write_executable(
        fake_bin / "python3",
        """#!/usr/bin/env bash
if [ "${1-}" = "-" ]; then
  exit 0
fi
printf '%s\n' "$@" > "$INSTALL_SH_TEST_OUTPUT"
""",
    )
    env = os.environ.copy()
    env["PATH"] = "{}:{}".format(fake_bin, env["PATH"])
    env["INSTALL_SH_TEST_OUTPUT"] = str(output)
    completed = subprocess.run(
        ["bash", str(script), "--system"],
        cwd=repo_root,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0
    forwarded_args = output.read_text(encoding="utf-8").splitlines()
    assert forwarded_args[0].endswith("scripts/install-stage-2.py")
    assert "--system" in forwarded_args


def test_bootstrap_sh_help_delegates_to_install_sh():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "bootstrap.sh"
    completed = subprocess.run(
        ["bash", str(script), "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "Delegating to" in completed.stdout
    assert "Usage: ./install.sh" in completed.stdout


def test_bootstrap_stage2_help_delegates_to_install_stage_2():
    repo_root = Path(__file__).resolve().parents[1]
    script = repo_root / "bootstrap-stage2.py"
    completed = subprocess.run(
        [sys.executable, str(script), "--help"],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0
    assert "--mode {standard,dev,venv-only}" in completed.stdout
    assert "--system" in completed.stdout
