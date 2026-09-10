# ruff: noqa: E402

from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"

if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from codex_wrangler.constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_LOCAL_DIR,
)
from codex_wrangler.layout import build_layout
from codex_wrangler.models import Config


@pytest.fixture
def config_factory():
    def factory(project_root: Path, **overrides) -> Config:
        layout = build_layout(
            project_root,
            overrides.pop("local_dir_raw", DEFAULT_LOCAL_DIR),
            overrides.pop("codex_home_raw", DEFAULT_HOME_DIR),
            overrides.pop("launcher_raw", "bin/codex-local"),
            overrides.pop("readme_raw", "README-LOCAL-Start-Codex.md"),
        )
        defaults = {
            "operation": "install",
            "project_root": project_root,
            "codex_selector": DEFAULT_INSTALL_CODEX_SELECTOR,
            "codex_channel": DEFAULT_INSTALL_CODEX_CHANNEL,
            "codex_version": DEFAULT_INSTALL_CODEX_SELECTOR,
            "shared_home": False,
            "skip_install": False,
            "force": False,
            "dry_run": False,
            "layout": layout,
            "version_source": "test",
            "reasonable_permissions_enabled": False,
            "reconfigure_only": False,
        }
        defaults.update(overrides)
        return Config(**defaults)

    return factory
