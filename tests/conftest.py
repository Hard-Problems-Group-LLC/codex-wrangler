from pathlib import Path

import pytest

from codex_wrangler.constants import DEFAULT_ALPHA_CODEX_VERSION
from codex_wrangler.layout import build_layout
from codex_wrangler.models import Config


@pytest.fixture
def config_factory():
    def factory(project_root: Path, **overrides) -> Config:
        layout = build_layout(
            project_root,
            overrides.pop("local_dir_raw", ".codex-local"),
            overrides.pop("codex_home_raw", ".codex-home"),
            overrides.pop("launcher_raw", "bin/codex-local"),
            overrides.pop("readme_raw", "README-LOCAL-Start-Codex.md"),
        )
        defaults = {
            "operation": "install",
            "project_root": project_root,
            "codex_version": DEFAULT_ALPHA_CODEX_VERSION,
            "shared_home": False,
            "skip_install": False,
            "force": False,
            "dry_run": False,
            "layout": layout,
            "version_source": "test",
        }
        defaults.update(overrides)
        return Config(**defaults)

    return factory
