#!/usr/bin/env python3
"""Run the TheKnowledge timeout wrapper from this repository root."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
HELPER_PATH = REPO_ROOT / "TheKnowledge" / "scripts" / "run_tool_with_timeout.py"
HELPER_DIR = HELPER_PATH.parent
LOCAL_PROFILES_PATH = REPO_ROOT / "scripts" / "tool_validation_profiles.py"


def preserve_project_venv_runtime() -> None:
    """Expose the venv path to shared helpers without resolving its symlink."""

    if os.environ.get("THEKNOWLEDGE_BLACK_PYTHON") or os.environ.get(
        "THEKNOWLEDGE_PYTHON_TOOLS"
    ):
        return
    candidate = Path(os.path.abspath(str(REPO_ROOT / ".venv" / "bin" / "python")))
    if candidate.is_file() and os.access(candidate, os.X_OK):
        os.environ["THEKNOWLEDGE_PYTHON_TOOLS"] = str(candidate)


def load_local_profiles_module() -> None:
    """Load the project overlay used by the shared timeout helper."""

    spec = importlib.util.spec_from_file_location(
        "tool_validation_profiles",
        LOCAL_PROFILES_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load local tool validation profiles.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)


def load_helper_main():
    """Load the TheKnowledge timeout helper as a module."""

    preserve_project_venv_runtime()
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    if str(HELPER_DIR) not in sys.path:
        sys.path.append(str(HELPER_DIR))
    load_local_profiles_module()

    spec = importlib.util.spec_from_file_location(
        "theknowledge_run_tool_with_timeout",
        HELPER_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load timeout helper.")

    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module.main


if __name__ == "__main__":
    main = load_helper_main()
    raise SystemExit(main(sys.argv[1:]))
