import argparse
import json

import pytest

from codex_wrangler.config import (
    config_from_args,
    determine_shared_home,
    determine_target_selection,
    normalize_requested_version,
    parse_args,
    parse_positive_int,
)
from codex_wrangler.constants import (
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_NPM_INSTALL_LOGLEVEL,
    DEFAULT_NPM_TIMEOUT_SECONDS,
)
from codex_wrangler.layout import resolve_relative_within_root
from codex_wrangler.models import CodexWranglerError, ExistingState
from codex_wrangler.rendering import build_local_package_json, build_metadata


class DummyArgs:
    inspect = False
    selftest = False
    uninstall = False
    update = False
    upgrade = False
    requested_version = None
    channel = None
    shared_home = None
    set_reasonable_permissions = False
    clear_reasonable_permissions = False


def test_normalize_requested_version_accepts_case_insensitive_latest():
    assert normalize_requested_version("LATEST") == "latest"
    assert normalize_requested_version("  latest  ") == "latest"
    assert normalize_requested_version("0.30.0-beta.2") == "0.30.0-beta.2"


def test_parse_positive_int_rejects_non_positive_values():
    with pytest.raises(argparse.ArgumentTypeError, match="positive integer"):
        parse_positive_int("0")


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


def test_parse_args_keeps_bare_update_as_project_root():
    args = parse_args(["update"])

    assert args.update is False
    assert args.project_root == "update"


def test_config_from_args_suggests_missing_dashes_for_bare_update():
    with pytest.raises(CodexWranglerError, match="Did you perhaps mean '--update'"):
        config_from_args(parse_args(["update"]))


def test_config_from_args_suggests_missing_dashes_for_other_known_flags():
    with pytest.raises(CodexWranglerError, match="Did you perhaps mean '--force'"):
        config_from_args(parse_args(["force"]))


def test_config_from_args_allows_existing_project_named_update(tmp_path, monkeypatch):
    project = tmp_path / "update"
    project.mkdir()
    monkeypatch.chdir(tmp_path)

    config = config_from_args(parse_args(["update"]))

    assert config.operation == "install"
    assert config.project_root == project.resolve()


def test_parse_args_rejects_set_reasonable_permissions_with_inspect():
    with pytest.raises(SystemExit):
        parse_args(["--inspect", "--set-reasonable-permissions"])


def test_parse_args_rejects_mismatched_upgrade_channel_and_version():
    with pytest.raises(SystemExit):
        parse_args(["--upgrade", "--channel", "stable", "--version", "0.31.0-beta.2"])


def test_parse_args_rejects_mismatched_install_channel_and_version():
    with pytest.raises(SystemExit):
        parse_args(["--channel", "stable", "--version", "0.31.0-beta.2"])


def test_parse_args_rejects_invalid_npm_timeout():
    with pytest.raises(SystemExit):
        parse_args(["--npm-timeout-seconds", "0"])


def test_parse_args_requires_repair_value():
    with pytest.raises(SystemExit):
        parse_args(["--repair"])


def test_parse_args_requires_absolute_repair_root():
    with pytest.raises(SystemExit):
        parse_args(["--repair", "relative/project"])


def test_parse_args_rejects_repair_with_positional_root(tmp_path):
    with pytest.raises(SystemExit):
        parse_args(["--repair", str(tmp_path), "."])


@pytest.mark.parametrize(
    "incompatible_args",
    [
        ["--inspect"],
        ["--channel", "stable"],
        ["--version", "0.30.0"],
        ["--skip-install"],
        ["--repair-install"],
        ["--shared-home"],
        ["--isolated-home"],
        ["--set-reasonable-permissions"],
        ["--clear-reasonable-permissions"],
        ["--local-dir", ".different-local"],
        ["--codex-home-dir", ".different-home"],
        ["--launcher", "bin/other"],
        ["--readme-local", "OTHER.md"],
    ],
)
def test_parse_args_rejects_incoherent_repair_options(tmp_path, incompatible_args):
    with pytest.raises(SystemExit):
        parse_args(["--repair", str(tmp_path), *incompatible_args])


def test_config_from_args_builds_exact_version_repair(tmp_path, monkeypatch):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / "package.json").write_text(
        build_local_package_json("0.31.0-beta.2"),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "spare-codex-wrangler-checkout"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = config_from_args(parse_args(["--repair", str(tmp_path)]))

    assert config.operation == "repair"
    assert config.project_root == tmp_path.resolve()
    assert config.codex_selector == "0.31.0-beta.2"
    assert config.codex_channel == "beta"
    assert config.codex_version == "0.31.0-beta.2"
    assert config.repair_install is True
    assert config.version_source == "repair evidence: managed package.json"


def test_config_from_args_repair_refuses_selector_only_evidence(tmp_path):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / "package.json").write_text(
        build_local_package_json("latest"),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="no surviving exact Codex version"):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


def test_config_from_args_repair_refuses_conflicting_versions(tmp_path):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / "package.json").write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    (local_dir / "package-lock.json").write_text(
        '{"packages":{"node_modules/@openai/codex":{"version":"0.31.0"}}}',
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


def test_config_from_args_repair_ignores_copied_metadata_when_manifest_is_managed(
    tmp_path,
    config_factory,
):
    source_root = tmp_path / "source"
    source_root.mkdir()
    source_config = config_factory(
        source_root,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    target_root = tmp_path / "target"
    target_root.mkdir()
    target_local = target_root / ".codex-local"
    target_local.mkdir()
    (target_local / ".codex-wrangler.json").write_text(
        json.dumps(build_metadata(source_config)),
        encoding="utf-8",
    )
    (target_local / "package.json").write_text(
        build_local_package_json("0.31.0"),
        encoding="utf-8",
    )

    config = config_from_args(parse_args(["--repair", str(target_root)]))

    assert config.codex_version == "0.31.0"
    assert config.version_source == "repair evidence: managed package.json"


@pytest.mark.parametrize(
    "path_name",
    ["local_dir", "codex_home_dir", "launcher", "readme_local"],
)
def test_config_from_args_repair_rejects_mismatched_metadata_paths(
    tmp_path,
    config_factory,
    path_name,
):
    source_config = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    metadata = build_metadata(source_config)
    metadata["paths"][path_name] = "mismatched-path"
    source_config.layout.local_dir.mkdir()
    source_config.layout.metadata_path.write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


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
    assert config.npm_timeout_seconds == DEFAULT_NPM_TIMEOUT_SECONDS
    assert config.npm_install_loglevel == DEFAULT_NPM_INSTALL_LOGLEVEL
    assert config.layout.launcher_relative == "bin/custom-codex"
    assert config.layout.readme_relative == "LOCAL-README.md"


def test_config_from_args_honors_npm_timeout_override(tmp_path):
    config = config_from_args(
        parse_args(["--npm-timeout-seconds", "42", str(tmp_path)])
    )

    assert config.npm_timeout_seconds == 42


def test_config_from_args_honors_npm_install_loglevel_override(tmp_path):
    config = config_from_args(
        parse_args(["--npm-install-loglevel", "notice", str(tmp_path)])
    )

    assert config.npm_install_loglevel == "notice"


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


def test_config_from_args_inspect_tolerates_corrupt_managed_json(tmp_path):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / ".codex-wrangler.json").write_text("{not json\n", encoding="utf-8")
    (local_dir / "package.json").write_text(
        build_local_package_json("0.31.0-beta.2"),
        encoding="utf-8",
    )
    (local_dir / "package-lock.json").write_text("{not json\n", encoding="utf-8")

    config = config_from_args(parse_args(["--inspect", str(tmp_path)]))

    assert config.operation == "inspect"
    assert config.codex_selector == "0.31.0-beta.2"
    assert config.codex_channel == "beta"
    assert config.codex_version == "0.31.0-beta.2"


def test_config_from_args_inspect_tolerates_fully_corrupt_managed_state(tmp_path):
    local_dir = tmp_path / ".codex-local"
    local_dir.mkdir()
    (local_dir / ".codex-wrangler.json").write_text("{not json\n", encoding="utf-8")
    (local_dir / "package.json").write_text("{not json\n", encoding="utf-8")
    (local_dir / "package-lock.json").write_text("{not json\n", encoding="utf-8")

    config = config_from_args(parse_args(["--inspect", str(tmp_path)]))

    assert config.operation == "inspect"
    assert config.codex_selector == DEFAULT_INSTALL_CODEX_SELECTOR
    assert config.codex_channel == DEFAULT_INSTALL_CODEX_CHANNEL
    assert config.codex_version == DEFAULT_INSTALL_CODEX_SELECTOR


def test_config_from_args_toggle_only_preserves_existing_selection(tmp_path):
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
    (local_dir / ".codex-wrangler.json").write_text(
        (
            "{"
            '"project_root": "%s", '
            '"codex_selector": "latest", '
            '"codex_channel": "beta", '
            '"codex_version": "0.31.0-beta.2", '
            '"version_source": "existing metadata", '
            '"reasonable_permissions_enabled": false'
            "}"
        )
        % tmp_path,
        encoding="utf-8",
    )

    config = config_from_args(
        parse_args(["--set-reasonable-permissions", str(tmp_path)])
    )

    assert config.operation == "install"
    assert config.codex_selector == "latest"
    assert config.codex_channel == "beta"
    assert config.codex_version == "0.31.0-beta.2"
    assert config.version_source == "existing metadata"
    assert config.reasonable_permissions_enabled is True
    assert config.reconfigure_only is True
