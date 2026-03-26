from pathlib import Path
import subprocess
import sys


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
