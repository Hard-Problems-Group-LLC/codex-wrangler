import pytest

from codex_wrangler.config import (
    config_from_args,
    determine_shared_home,
    determine_target_selection,
    normalize_requested_version,
    parse_args,
)
from codex_wrangler.constants import (
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_INSTALL_CODEX_SELECTOR,
)
from codex_wrangler.layout import resolve_relative_within_root
from codex_wrangler.models import CodexWranglerError, ExistingState
from codex_wrangler.rendering import build_local_package_json


class DummyArgs:
    inspect = False
    selftest = False
    uninstall = False
    update = False
    upgrade = False
    requested_version = None
    channel = None
    shared_home = None


def test_normalize_requested_version_accepts_case_insensitive_latest():
    assert normalize_requested_version("LATEST") == "latest"
    assert normalize_requested_version("  latest  ") == "latest"
    assert normalize_requested_version("0.30.0-beta.2") == "0.30.0-beta.2"


def test_determine_target_selection_defaults_to_latest_stable_for_install():
    selector, channel, version, source = determine_target_selection(
        DummyArgs(),
        ExistingState(),
        "install",
    )
    assert selector == DEFAULT_INSTALL_CODEX_SELECTOR
    assert channel == DEFAULT_INSTALL_CODEX_CHANNEL
    assert version == "latest"
    assert source == "default latest request"


def test_determine_target_selection_uses_existing_state_for_update():
    existing = ExistingState(
        metadata={"version_source": "previous install"},
        requested_codex_selector="latest",
        codex_channel="beta",
        pinned_codex_version="0.31.0-beta.2",
    )
    selector, channel, version, source = determine_target_selection(
        DummyArgs(),
        existing,
        "update",
    )
    assert selector == "latest"
    assert channel == "beta"
    assert version == "0.31.0-beta.2"
    assert source == "previous install"


def test_determine_target_selection_requires_upgrade_channel():
    with pytest.raises(CodexWranglerError):
        determine_target_selection(DummyArgs(), ExistingState(), "upgrade")


def test_determine_shared_home_preserves_existing_choice():
    args = DummyArgs()
    existing = ExistingState(shared_home=True)
    assert determine_shared_home(args, existing) is True


def test_determine_shared_home_honors_explicit_flag():
    args = DummyArgs()
    args.shared_home = False
    existing = ExistingState(shared_home=True)
    assert determine_shared_home(args, existing) is False


def test_resolve_relative_within_root_rejects_escape(tmp_path):
    with pytest.raises(CodexWranglerError):
        resolve_relative_within_root(tmp_path, "../escape", "test")


def test_parse_args_requires_upgrade_channel():
    with pytest.raises(SystemExit):
        parse_args(["--upgrade"])


def test_parse_args_rejects_update_version_request():
    with pytest.raises(SystemExit):
        parse_args(["--update", "--version", "latest"])


def test_parse_args_rejects_mismatched_upgrade_channel_and_version():
    with pytest.raises(SystemExit):
        parse_args(["--upgrade", "--channel", "stable", "--version", "0.31.0-beta.2"])


def test_parse_args_rejects_mismatched_install_channel_and_version():
    with pytest.raises(SystemExit):
        parse_args(["--channel", "stable", "--version", "0.31.0-beta.2"])


def test_config_from_args_builds_expected_configuration(tmp_path):
    args = parse_args(
        [
            "--shared-home",
            "--channel",
            "beta",
            "--version",
            "latest",
            "--launcher",
            "bin/custom-codex",
            "--readme-local",
            "LOCAL-README.md",
            str(tmp_path),
        ]
    )
    config = config_from_args(args)

    assert config.operation == "install"
    assert config.codex_selector == "latest"
    assert config.codex_channel == "beta"
    assert config.shared_home is True
    assert config.layout.launcher_relative == "bin/custom-codex"
    assert config.layout.readme_relative == "LOCAL-README.md"


def test_config_from_args_update_falls_back_to_existing_managed_files(tmp_path):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / "package.json").write_text(
        build_local_package_json("0.31.0-beta.2"),
        encoding="utf-8",
    )
    (local_dir / "package-lock.json").write_text(
        '{"packages": {"node_modules/@openai/codex": {"version": "0.31.0-beta.2"}}}',
        encoding="utf-8",
    )

    config = config_from_args(parse_args(["--update", str(tmp_path)]))

    assert config.operation == "update"
    assert config.codex_selector == "0.31.0-beta.2"
    assert config.codex_channel == "beta"
    assert config.codex_version == "0.31.0-beta.2"
    assert config.version_source == "existing managed files"
