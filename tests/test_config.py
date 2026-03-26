import pytest

from codex_wrangler.config import (
    config_from_args,
    determine_shared_home,
    determine_target_version,
    parse_args,
)
from codex_wrangler.constants import (
    DEFAULT_ALPHA_CODEX_VERSION,
    DEFAULT_STABLE_CODEX_VERSION,
)
from codex_wrangler.layout import resolve_relative_within_root
from codex_wrangler.models import CodexWranglerError, ExistingState


class DummyArgs:
    inspect = False
    selftest = False
    uninstall = False
    upgrade = False
    upgrade_to_alpha = False
    downgrade_to_stable = False
    codex_version = None
    shared_home = None


def test_determine_target_version_defaults_to_alpha_for_install():
    version, source = determine_target_version(
        DummyArgs(),
        ExistingState(None, None, None),
        "install",
    )
    assert version == DEFAULT_ALPHA_CODEX_VERSION
    assert source == "default install target"


def test_determine_target_version_preserves_alpha_channel_on_upgrade():
    existing = ExistingState(
        metadata=None,
        pinned_codex_version="0.117.0-alpha.8",
        shared_home=False,
    )
    version, source = determine_target_version(DummyArgs(), existing, "upgrade")
    assert version == DEFAULT_ALPHA_CODEX_VERSION
    assert source == "preserved alpha channel"


def test_determine_target_version_preserves_stable_channel_on_upgrade():
    existing = ExistingState(
        metadata=None,
        pinned_codex_version="0.116.0",
        shared_home=False,
    )
    version, source = determine_target_version(DummyArgs(), existing, "upgrade")
    assert version == DEFAULT_STABLE_CODEX_VERSION
    assert source == "preserved stable channel"


def test_determine_target_version_uses_metadata_source_for_inspection():
    existing = ExistingState(
        metadata={"version_source": "default install target"},
        pinned_codex_version="0.117.0-alpha.19",
        shared_home=False,
    )
    version, source = determine_target_version(DummyArgs(), existing, "inspect")
    assert version == "0.117.0-alpha.19"
    assert source == "default install target"


def test_determine_shared_home_preserves_existing_choice():
    args = DummyArgs()
    existing = ExistingState(
        metadata=None,
        pinned_codex_version=None,
        shared_home=True,
    )
    assert determine_shared_home(args, existing) is True


def test_determine_shared_home_honors_explicit_flag():
    args = DummyArgs()
    args.shared_home = False
    existing = ExistingState(
        metadata=None,
        pinned_codex_version=None,
        shared_home=True,
    )
    assert determine_shared_home(args, existing) is False


def test_resolve_relative_within_root_rejects_escape(tmp_path):
    with pytest.raises(CodexWranglerError):
        resolve_relative_within_root(tmp_path, "../escape", "test")


def test_config_from_args_builds_expected_configuration(tmp_path):
    args = parse_args(
        [
            "--shared-home",
            "--launcher",
            "bin/custom-codex",
            "--readme-local",
            "LOCAL-README.md",
            str(tmp_path),
        ]
    )
    config = config_from_args(args)

    assert config.operation == "install"
    assert config.shared_home is True
    assert config.layout.launcher_relative == "bin/custom-codex"
    assert config.layout.readme_relative == "LOCAL-README.md"
