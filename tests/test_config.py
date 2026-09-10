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
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_CHANNEL,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_NPM_INSTALL_LOGLEVEL,
    DEFAULT_NPM_TIMEOUT_SECONDS,
)
from codex_wrangler.layout import (
    build_layout,
    metadata_looks_managed,
    resolve_relative_within_root,
)
from codex_wrangler.models import CodexWranglerError, ExistingState
from codex_wrangler.rendering import build_local_package_json, build_metadata
from codex_wrangler.slots import promote_active_slot, write_slot_metadata


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


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "#history",
        "!history",
        "hist[ory]",
        "history space",
        "history\\tail",
        "history\nleak",
        "$(touch-pwned)",
        chr(96) + "touch-pwned" + chr(96),
        'history"quote',
    ],
)
def test_resolve_relative_within_root_rejects_generated_syntax(unsafe_path, tmp_path):
    """Managed paths remain literal in shell and gitignore projections."""

    with pytest.raises(CodexWranglerError, match="unsupported path syntax"):
        resolve_relative_within_root(
            tmp_path,
            unsafe_path,
            "--codex-home-dir",
        )


def test_resolve_relative_within_root_rejects_non_directory_ancestor(tmp_path):
    """Nested managed paths cannot traverse an existing regular file."""

    blocking_parent = tmp_path / "state"
    blocking_parent.write_text("operator data\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="parent is not a directory"):
        resolve_relative_within_root(
            tmp_path,
            "state/nested/home",
            "--codex-home-dir",
        )

    assert blocking_parent.read_text(encoding="utf-8") == "operator data\n"


@pytest.mark.parametrize(
    ("local_dir", "codex_home", "launcher", "readme"),
    [
        (".managed", ".managed/slots/a", "bin/codex-local", "LOCAL.md"),
        (".state/codex", ".state", "bin/codex-local", "LOCAL.md"),
        (
            ".managed",
            ".codex-home",
            ".managed/slots/a/codex-local",
            "LOCAL.md",
        ),
        (
            ".managed",
            ".codex-home",
            "bin/codex-local",
            ".managed/slots/b/LOCAL.md",
        ),
        (
            ".managed",
            ".codex-home",
            ".codex-home/bin/codex-local",
            "LOCAL.md",
        ),
        (".managed", ".codex-home", "LOCAL.md", "LOCAL.md"),
        (".gitignore/runtime", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".codex", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".agents/runtime", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".local", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".git", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".git/runtime", ".codex-home", "bin/codex-local", "LOCAL.md"),
        (".codex-home", ".other-home", "bin/codex-local", "LOCAL.md"),
        (".managed", ".codex-home", ".codex/launcher", "LOCAL.md"),
        (".managed", ".codex-home", "bin/codex-local", ".agents/LOCAL.md"),
        (".managed", ".local/codex", "bin/codex-local", "LOCAL.md"),
        (
            ".codex-wrangler.lock",
            ".codex-home",
            "bin/codex-local",
            "LOCAL.md",
        ),
        (
            ".managed",
            ".codex-wrangler.lock",
            "bin/codex-local",
            "LOCAL.md",
        ),
        (
            ".managed",
            ".codex-home",
            ".codex-wrangler.lock",
            "LOCAL.md",
        ),
        (
            ".managed",
            ".codex-home",
            "bin/codex-local",
            ".codex-wrangler.lock",
        ),
    ],
)
def test_build_layout_rejects_overlapping_managed_paths(
    tmp_path,
    local_dir,
    codex_home,
    launcher,
    readme,
):
    """Runtime rotation can never consume context or generated support files."""

    with pytest.raises(CodexWranglerError, match="must not overlap"):
        build_layout(
            tmp_path,
            local_dir,
            codex_home,
            launcher,
            readme,
        )


@pytest.mark.parametrize(
    ("local_dir", "codex_home", "launcher", "readme"),
    [
        (".X", ".x", "bin/codex-local", "LOCAL.md"),
        (".managed", ".home", "Support/File", "support/file"),
        (".managed", ".home", ".CODEX/launcher", "LOCAL.md"),
        (".managed", ".CODEX-HOME", "bin/codex-local", "LOCAL.md"),
    ],
)
def test_build_layout_rejects_casefold_equivalent_path_aliases(
    tmp_path,
    local_dir,
    codex_home,
    launcher,
    readme,
):
    """Absent path aliases cannot become destructive on insensitive hosts."""

    with pytest.raises(CodexWranglerError, match="must not overlap"):
        build_layout(
            tmp_path,
            local_dir,
            codex_home,
            launcher,
            readme,
        )


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


@pytest.mark.parametrize(
    ("mode_flag", "expected_shared"),
    [("--shared-home", True), ("--isolated-home", False)],
)
def test_parse_args_accepts_explicit_repair_home_mode(
    tmp_path,
    mode_flag,
    expected_shared,
):
    """Repair can accept operator authority only when records lost HOME mode."""

    args = parse_args(["--repair", str(tmp_path), mode_flag])

    assert args.shared_home is expected_shared


def test_config_from_args_repair_requires_home_mode_when_evidence_lost(tmp_path):
    """A package manifest alone cannot reveal where historical context lives."""

    local_dir = tmp_path / DEFAULT_LOCAL_DIR
    local_dir.mkdir(parents=True)
    (local_dir / "package.json").write_text(
        build_local_package_json("0.31.0"),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="cannot recover whether"):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


def test_config_from_args_builds_exact_version_repair(tmp_path, monkeypatch):
    local_dir = tmp_path / DEFAULT_LOCAL_DIR
    local_dir.mkdir(parents=True)
    (local_dir / "package.json").write_text(
        build_local_package_json("0.31.0-beta.2"),
        encoding="utf-8",
    )
    elsewhere = tmp_path / "spare-codex-wrangler-checkout"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    config = config_from_args(
        parse_args(["--repair", str(tmp_path), "--isolated-home"])
    )

    assert config.operation == "repair"
    assert config.project_root == tmp_path.resolve()
    assert config.codex_selector == "0.31.0-beta.2"
    assert config.codex_channel == "beta"
    assert config.codex_version == "0.31.0-beta.2"
    assert config.repair_install is True
    assert config.version_source == "repair evidence: managed root package.json"


def test_config_from_args_repairs_from_active_slot_without_root_projections(
    tmp_path,
    config_factory,
):
    """Absolute-path repair treats the completed active slot as authoritative."""

    source = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    candidate = source.layout.local_dir / "slots" / "a"
    candidate.mkdir(parents=True)
    candidate.joinpath("package.json").write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    candidate.joinpath("package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": "0.30.0"}}}),
        encoding="utf-8",
    )
    installed_manifest = (
        candidate / "node_modules" / "@openai" / "codex" / "package.json"
    )
    installed_manifest.parent.mkdir(parents=True)
    installed_manifest.write_text('{"version":"0.30.0"}\n', encoding="utf-8")
    write_slot_metadata(source, "a")
    promote_active_slot(source.layout, "a")

    config = config_from_args(parse_args(["--repair", str(tmp_path)]))

    assert config.operation == "repair"
    assert config.codex_version == "0.30.0"
    assert config.version_source.startswith(
        "repair evidence: active slot completion record"
    )
    assert config.active_slot == "a"
    assert config.active_pointer_kind == "symlink"


def test_repair_home_override_cannot_conflict_with_active_authority(
    tmp_path,
    config_factory,
):
    """Explicit recovery input may fill missing evidence, not reconfigure it."""

    source = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    candidate = source.layout.local_dir / "slots" / "a"
    candidate.mkdir(parents=True)
    candidate.joinpath("package.json").write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    candidate.joinpath("package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": "0.30.0"}}}),
        encoding="utf-8",
    )
    installed_manifest = (
        candidate / "node_modules" / "@openai" / "codex" / "package.json"
    )
    installed_manifest.parent.mkdir(parents=True)
    installed_manifest.write_text('{"version":"0.30.0"}\n', encoding="utf-8")
    write_slot_metadata(source, "a")
    promote_active_slot(source.layout, "a")

    with pytest.raises(CodexWranglerError, match="cannot change the HOME mode"):
        config_from_args(parse_args(["--repair", str(tmp_path), "--shared-home"]))


def test_config_from_args_repair_refuses_selector_only_evidence(tmp_path):
    local_dir = tmp_path / DEFAULT_LOCAL_DIR
    local_dir.mkdir(parents=True)
    (local_dir / "package.json").write_text(
        build_local_package_json("latest"),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="no surviving exact Codex version"):
        config_from_args(parse_args(["--repair", str(tmp_path), "--isolated-home"]))


def test_config_from_args_repair_refuses_conflicting_versions(tmp_path):
    local_dir = tmp_path / DEFAULT_LOCAL_DIR
    local_dir.mkdir(parents=True)
    (local_dir / "package.json").write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    (local_dir / "package-lock.json").write_text(
        '{"packages":{"node_modules/@openai/codex":{"version":"0.31.0"}}}',
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        config_from_args(parse_args(["--repair", str(tmp_path), "--isolated-home"]))


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
    target_local = target_root / DEFAULT_LOCAL_DIR
    target_local.mkdir(parents=True)
    (target_local / ".codex-wrangler.json").write_text(
        json.dumps(build_metadata(source_config)),
        encoding="utf-8",
    )
    (target_local / "package.json").write_text(
        build_local_package_json("0.31.0"),
        encoding="utf-8",
    )

    config = config_from_args(
        parse_args(["--repair", str(target_root), "--isolated-home"])
    )

    assert config.codex_version == "0.31.0"
    assert config.version_source == "repair evidence: managed root package.json"


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
    source_config.layout.local_dir.mkdir(parents=True)
    source_config.layout.metadata_path.write_text(
        json.dumps(metadata),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        config_from_args(parse_args(["--repair", str(tmp_path), "--isolated-home"]))


def test_help_describes_transactional_skip_and_repair_install(capsys):
    """CLI help matches the A/B candidate behavior."""

    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert "without creating a candidate or rewriting published" in help_text
    assert "preserving the active runtime, rollback slot" in help_text
    assert "remove only managed npm install artifacts" not in help_text


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

    config = config_from_args(
        parse_args(
            [
                "--update",
                "--local-dir",
                LEGACY_LOCAL_DIR,
                "--codex-home-dir",
                LEGACY_HOME_DIR,
                str(tmp_path),
            ]
        )
    )

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


def test_config_from_args_toggle_only_preserves_existing_selection(
    tmp_path,
    config_factory,
):
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
    existing_config = config_factory(
        tmp_path,
        codex_selector="latest",
        codex_channel="beta",
        codex_version="0.31.0-beta.2",
        version_source="existing metadata",
        local_dir_raw=".codex-local",
        codex_home_raw=".codex-home",
    )
    (local_dir / ".codex-wrangler.json").write_text(
        json.dumps(build_metadata(existing_config)),
        encoding="utf-8",
    )
    (tmp_path / LEGACY_HOME_DIR).mkdir()

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


def test_permission_toggle_with_explicit_home_change_is_not_projection_only(
    tmp_path,
    config_factory,
):
    """An explicit HOME transition must use the transactional install path."""

    existing = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    existing.layout.local_dir.mkdir(parents=True)
    existing.layout.codex_home_dir.mkdir(parents=True)
    existing.layout.local_package_json_path.write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    existing.layout.metadata_path.write_text(
        json.dumps(build_metadata(existing)),
        encoding="utf-8",
    )

    config = config_from_args(
        parse_args(
            [
                "--set-reasonable-permissions",
                "--shared-home",
                str(tmp_path),
            ]
        )
    )

    assert config.shared_home is True
    assert config.reasonable_permissions_enabled is True
    assert config.reconfigure_only is False


def test_default_configuration_uses_canonical_local_layout(tmp_path):
    """Implicit defaults place runtime and isolated HOME beneath .local."""

    config = config_from_args(parse_args([str(tmp_path)]))

    assert config.layout.local_dir_relative == DEFAULT_LOCAL_DIR
    assert config.layout.codex_home_relative == DEFAULT_HOME_DIR
    assert config.layout.local_dir == tmp_path / DEFAULT_LOCAL_DIR
    assert config.layout.codex_home_dir == tmp_path / DEFAULT_HOME_DIR


def test_help_presents_canonical_repair_install_defaults(capsys):
    """Operator help does not present the deprecated flat roots as current."""

    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--help"])

    assert exit_info.value.code == 0
    help_text = capsys.readouterr().out
    assert DEFAULT_LOCAL_DIR in help_text
    assert DEFAULT_HOME_DIR in help_text
    assert "under .codex-local:" not in help_text
    assert "remove .codex-home" not in help_text


def test_metadata_accepts_only_complete_historical_default_tuple(
    tmp_path,
    config_factory,
):
    """Legacy metadata aliases are all-or-nothing, never a mixed path tuple."""

    config = config_factory(tmp_path)
    legacy = build_metadata(config)
    legacy["paths"]["local_dir"] = LEGACY_LOCAL_DIR
    legacy["paths"]["codex_home_dir"] = LEGACY_HOME_DIR
    mixed = json.loads(json.dumps(legacy))
    mixed["paths"]["codex_home_dir"] = DEFAULT_HOME_DIR

    assert metadata_looks_managed(legacy, config.layout) is False

    config.layout.local_dir.mkdir(parents=True)
    legacy_compatibility = tmp_path / LEGACY_LOCAL_DIR
    legacy_compatibility.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    shared_legacy = json.loads(json.dumps(legacy))
    shared_legacy["shared_home"] = True

    assert metadata_looks_managed(legacy, config.layout) is False
    assert metadata_looks_managed(shared_legacy, config.layout) is True

    config.layout.codex_home_dir.mkdir()
    legacy_home_compatibility = tmp_path / LEGACY_HOME_DIR
    legacy_home_compatibility.symlink_to(
        DEFAULT_HOME_DIR,
        target_is_directory=True,
    )

    assert metadata_looks_managed(legacy, config.layout) is True
    assert metadata_looks_managed(mixed, config.layout) is False
