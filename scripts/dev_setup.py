#!/usr/bin/env python3
"""Bootstrap a starter Python toolchain from pinned requirements."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = REPO_ROOT / ".venv"
REQUIREMENTS_FILE = REPO_ROOT / "requirements-dev.txt"


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create or refresh the starter Python environment from the pinned "
            "TheKnowledge tool requirements."
        )
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="Python interpreter used to create the virtual environment.",
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help="Virtual environment path.",
    )
    parser.add_argument(
        "--skip-install",
        action="store_true",
        help="Skip pip installation after ensuring the virtual environment.",
    )
    return parser.parse_args(argv)


def venv_python_path(venv_path: Path) -> Path:
    bin_dir = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    return venv_path / bin_dir / executable


def run(command: Sequence[str | Path]) -> None:
    printable = " ".join(str(part) for part in command)
    print(f"[dev-setup] -> {printable}")
    subprocess.run([str(part) for part in command], cwd=REPO_ROOT, check=True)


def ensure_virtualenv(python_executable: str, venv_path: Path) -> Path:
    python_path = venv_python_path(venv_path)
    if python_path.exists():
        return python_path

    run([python_executable, "-m", "venv", venv_path])
    return python_path


def install_requirements(python_executable: Path) -> None:
    if not REQUIREMENTS_FILE.is_file():
        raise RuntimeError(f"Missing requirements file: {REQUIREMENTS_FILE}")

    run(
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "--upgrade",
            "pip",
            "setuptools",
            "wheel",
        ]
    )
    run(
        [
            python_executable,
            "-m",
            "pip",
            "install",
            "-r",
            REQUIREMENTS_FILE,
        ]
    )
    run([python_executable, "--version"])


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    try:
        python_executable = ensure_virtualenv(args.python, args.venv)
        if not args.skip_install:
            install_requirements(python_executable)
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"[dev-setup] FAIL: {error}", file=sys.stderr)
        return 1

    print("[dev-setup] PASS: pinned starter Python tooling is ready.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
