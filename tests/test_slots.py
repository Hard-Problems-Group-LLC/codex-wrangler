"""Pessimistic tests for atomic A/B Codex package promotion."""

from __future__ import annotations

import errno
import json
import os
from pathlib import Path

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.constants import (
    DEFAULT_HOME_DIR,
    DEFAULT_LOCAL_DIR,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
)

from codex_wrangler.filesystem import write_metadata
from codex_wrangler.layout import read_existing_state
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import install_like_operation, publish_projection_state
from codex_wrangler.repair import build_repair_plan
from codex_wrangler.rendering import (
    build_launcher_content,
    build_local_package_json,
    build_local_readme_content,
    build_metadata,
)
from codex_wrangler.slots import (
    MaintenanceLock,
    PointerCommittedInterrupt,
    activate_candidate_slot,
    discover_active_runtime,
    fsync_candidate_tree,
    promote_active_slot,
    read_slot_metadata,
    write_slot_metadata,
)


def materialize_legacy_install(config) -> dict[Path, bytes]:
    """Create one usable-looking legacy install and return protected snapshots."""

    layout = config.layout
    layout.local_node_modules_dir.mkdir(parents=True)
    (layout.local_node_modules_dir / "legacy-runtime").write_text(
        "must survive\n",
        encoding="utf-8",
    )
    legacy_bin = layout.local_node_modules_dir / ".bin" / "codex"
    legacy_bin.parent.mkdir(parents=True)
    legacy_bin.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    legacy_bin.chmod(0o755)
    layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    layout.local_package_lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {
                        "version": config.codex_version,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    layout.launcher_path.parent.mkdir(parents=True)
    layout.launcher_path.write_text(build_launcher_content(config), encoding="utf-8")
    layout.launcher_path.chmod(0o755)
    layout.readme_path.write_text(
        build_local_readme_content(config),
        encoding="utf-8",
    )
    write_metadata(layout.metadata_path, build_metadata(config), dry_run=False)
    context = layout.codex_home_dir / ".codex" / "sessions" / "rollout.jsonl"
    context.parent.mkdir(parents=True)
    context.write_text("preserve this context\n", encoding="utf-8")
    protected = (
        layout.local_package_json_path,
        layout.local_package_lock_path,
        layout.local_node_modules_dir / "legacy-runtime",
        legacy_bin,
        layout.launcher_path,
        layout.readme_path,
        layout.metadata_path,
        context,
    )
    return {path: path.read_bytes() for path in protected}


def configure_install_mocks(monkeypatch, config) -> None:
    """Keep install tests local and retain one exact resolved configuration."""

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda _config, _npm, env=None, timeout_seconds=None: config,
    )


@pytest.mark.parametrize(
    "failure",
    [CodexWranglerError("timed out after 5s"), KeyboardInterrupt()],
)
def test_candidate_command_failure_preserves_legacy_runtime(
    monkeypatch,
    tmp_path,
    config_factory,
    failure,
):
    """A timeout or operator interrupt cannot publish or damage a candidate."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    snapshots = materialize_legacy_install(config)
    configure_install_mocks(monkeypatch, config)

    def fail_candidate(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    expected_exception = (
        KeyboardInterrupt
        if isinstance(failure, KeyboardInterrupt)
        else CodexWranglerError
    )
    with pytest.raises(expected_exception):
        install_like_operation(config)

    for path, expected in snapshots.items():
        assert path.read_bytes() == expected
    assert not (config.layout.local_dir / "active").exists()
    assert not (config.layout.local_dir / "active-slot").exists()


def test_candidate_validation_failure_preserves_published_state(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A structurally invalid native candidate never reaches the commit point."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    snapshots = materialize_legacy_install(config)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: (_ for _ in ()).throw(
            CodexWranglerError("native payload reaches beyond EOF")
        ),
    )

    with pytest.raises(CodexWranglerError, match="not promoted"):
        install_like_operation(config)

    for path, expected in snapshots.items():
        assert path.read_bytes() == expected
    assert not (config.layout.local_dir / "active").exists()


def test_version_resolution_timeout_preserves_legacy_runtime(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A registry timeout before candidate selection cannot publish any change."""

    config = config_factory(tmp_path)
    snapshots = materialize_legacy_install(config)
    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries",
        lambda: ("npm", "npx"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists",
        lambda _name: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CodexWranglerError("version lookup timed out")
        ),
    )

    with pytest.raises(CodexWranglerError, match="active runtime remains untouched"):
        install_like_operation(config)

    for path, expected in snapshots.items():
        assert path.read_bytes() == expected
    assert not (config.layout.local_dir / "active").exists()
    assert not (config.layout.local_dir / "active-slot").exists()


def test_unmanaged_launcher_is_rejected_before_candidate_npm(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A predictable post-commit projection conflict fails before mutation."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    snapshots = materialize_legacy_install(config)
    config.layout.launcher_path.write_text("operator launcher\n", encoding="utf-8")
    configure_install_mocks(monkeypatch, config)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("candidate npm must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="without --force"):
        install_like_operation(config)

    assert called is False
    assert (
        config.layout.launcher_path.read_text(encoding="utf-8") == "operator launcher\n"
    )
    assert (
        snapshots[config.layout.local_node_modules_dir / "legacy-runtime"]
        == (config.layout.local_node_modules_dir / "legacy-runtime").read_bytes()
    )
    assert not list(config.layout.local_dir.glob(".candidate-*"))


def test_malformed_gitignore_is_rejected_before_candidate_npm(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """An ambiguous ignore block cannot become a late post-pointer failure."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(config)
    config.layout.gitignore_path.write_text(
        "# BEGIN managed by codex-wrangler\n",
        encoding="utf-8",
    )
    configure_install_mocks(monkeypatch, config)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("candidate npm must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="begin marker without its end"):
        install_like_operation(config)

    assert called is False
    assert not list(config.layout.local_dir.glob(".candidate-*"))
    assert not (config.layout.local_dir / "active").exists()


def test_nonregular_root_package_projection_is_rejected_before_candidate_npm(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A directory at a projected file path fails before candidate creation."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        force=True,
    )
    config.layout.local_package_json_path.mkdir(parents=True)
    configure_install_mocks(monkeypatch, config)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("candidate npm must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="regular file or absent"):
        install_like_operation(config)

    assert called is False
    assert config.layout.local_package_json_path.is_dir()
    assert not list(config.layout.local_dir.glob(".candidate-*"))


def materialize_candidate(command: list[str], version: str) -> Path:
    """Complete the minimal candidate files expected after a mocked npm run."""

    prefix = Path(command[command.index("--prefix") + 1])
    prefix.joinpath("package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": version}}}),
        encoding="utf-8",
    )
    prefix.joinpath("node_modules", "@openai", "codex").mkdir(parents=True)
    prefix.joinpath("node_modules", "@openai", "codex", "package.json").write_text(
        json.dumps({"version": version}),
        encoding="utf-8",
    )
    return prefix


def materialize_completed_slot(config, slot_name: str, version: str) -> Path:
    """Create one fixed slot whose completion evidence is concordant."""

    prefix = config.layout.local_dir / "slots" / slot_name
    prefix.mkdir(parents=True, exist_ok=True)
    prefix.joinpath("package.json").write_text(
        build_local_package_json(version),
        encoding="utf-8",
    )
    materialize_candidate(
        ["npm", "install", "--prefix", str(prefix)],
        version,
    )
    write_slot_metadata(config, slot_name)
    return prefix


def test_install_rejects_symlinked_slots_container_before_candidate(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Maintenance cannot publish a candidate through a linked slots path."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(config)
    contained_target = config.layout.local_dir / ".contained-slots"
    contained_target.mkdir()
    (config.layout.local_dir / "slots").symlink_to(
        ".contained-slots",
        target_is_directory=True,
    )
    configure_install_mocks(monkeypatch, config)
    candidate_started = False

    def fail_candidate(*_args, **_kwargs):
        nonlocal candidate_started
        candidate_started = True
        raise AssertionError("candidate must not start")

    monkeypatch.setattr("codex_wrangler.operations.run_command", fail_candidate)

    with pytest.raises(CodexWranglerError, match="slots directory may not be"):
        install_like_operation(config)

    assert candidate_started is False
    assert (config.layout.local_dir / "slots").is_symlink()
    assert list(contained_target.iterdir()) == []
    assert not list(config.layout.local_dir.glob(".candidate-*"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schema_version", True),
        ("generated_at", 7),
        ("codex_selector", "not-a-selector"),
        ("codex_channel", ["stable"]),
        ("version_source", 7),
        ("shared_home", "false"),
        ("reasonable_permissions_enabled", 1),
        ("paths", None),
        ("available_versions", {"stable": "latest"}),
        ("available_versions", {"nightly": "0.30.0"}),
        ("available_versions_updated_at", 7),
    ],
)
def test_slot_metadata_rejects_malformed_authority_fields(
    tmp_path,
    config_factory,
    field,
    value,
):
    """A hand-edited completion record cannot become projection authority."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    prefix = materialize_completed_slot(config, "a", "0.30.0")
    record_path = prefix / ".codex-wrangler-slot.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload[field] = value
    record_path.write_text(json.dumps(payload), encoding="utf-8")

    assert read_slot_metadata(config.layout, "a") is None


def test_slot_metadata_is_bound_to_managed_local_root(tmp_path, config_factory):
    """A same-project record copied between custom roots is not authoritative."""

    source = config_factory(
        tmp_path,
        local_dir_raw=".runtime-one",
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    target = config_factory(
        tmp_path,
        local_dir_raw=".runtime-two",
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    source_prefix = materialize_completed_slot(source, "a", "0.30.0")
    target_prefix = materialize_completed_slot(target, "a", "0.30.0")
    target_record = target_prefix / ".codex-wrangler-slot.json"
    target_record.write_bytes(
        (source_prefix / ".codex-wrangler-slot.json").read_bytes()
    )

    assert read_slot_metadata(target.layout, "a") is None


def test_slot_metadata_is_bound_to_canonical_project_root(tmp_path, config_factory):
    """A completion record copied from another project cannot select a slot."""

    source_root = tmp_path / "source"
    target_root = tmp_path / "target"
    source_root.mkdir()
    target_root.mkdir()
    source = config_factory(
        source_root,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    target = config_factory(
        target_root,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    source_prefix = materialize_completed_slot(source, "a", "0.30.0")
    target_prefix = materialize_completed_slot(target, "a", "0.30.0")
    (target_prefix / ".codex-wrangler-slot.json").write_bytes(
        (source_prefix / ".codex-wrangler-slot.json").read_bytes()
    )

    assert read_slot_metadata(target.layout, "a") is None


def test_successive_promotions_alternate_slots_and_retain_previous(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Successful commits alternate A/B without deleting the former active slot."""

    first = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    snapshots = materialize_legacy_install(first)
    configure_install_mocks(monkeypatch, first)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.30.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )

    assert install_like_operation(first) == 0
    active_link = first.layout.local_dir / "active"
    assert str(active_link.readlink()) == "slots/a"
    assert read_slot_metadata(first.layout, "a")["codex_version"] == "0.30.0"
    for path, expected in snapshots.items():
        if path in (
            first.layout.local_package_json_path,
            first.layout.local_package_lock_path,
            first.layout.metadata_path,
            first.layout.readme_path,
        ):
            continue
        assert path.read_bytes() == expected

    second = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    configure_install_mocks(monkeypatch, second)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.31.0"),
    )

    assert install_like_operation(second) == 0
    assert str(active_link.readlink()) == "slots/b"
    assert read_slot_metadata(second.layout, "b")["codex_version"] == "0.31.0"
    assert read_slot_metadata(second.layout, "a")["codex_version"] == "0.30.0"


def test_post_commit_projection_failure_keeps_verified_candidate_active(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A failure after pointer replacement must not destructively roll back."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(config)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.30.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.publish_installed_state",
        lambda *_args: (_ for _ in ()).throw(CodexWranglerError("projection failed")),
    )

    with pytest.raises(
        CodexWranglerError,
        match="projection failed.*new runtime remains active",
    ):
        install_like_operation(config)

    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"
    assert read_slot_metadata(config.layout, "a")["codex_version"] == "0.30.0"
    assert (config.layout.local_node_modules_dir / "legacy-runtime").exists()


def test_interrupt_after_pointer_helper_returns_never_rolls_back_selected_slot(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """An async interrupt in the post-return window respects pointer authority."""

    config = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    prior = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(prior, "a", "0.30.0")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.31.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )
    real_promote = promote_active_slot

    def interrupt_after_promote(*args, **kwargs):
        real_promote(*args, **kwargs)
        raise KeyboardInterrupt

    monkeypatch.setattr(
        "codex_wrangler.operations.promote_active_slot",
        interrupt_after_promote,
    )

    with pytest.raises(PointerCommittedInterrupt, match="new runtime remains active"):
        install_like_operation(config)

    assert str((config.layout.local_dir / "active").readlink()) == "slots/b"
    assert read_slot_metadata(config.layout, "a") is not None
    assert read_slot_metadata(config.layout, "b") is not None


def test_post_commit_projection_interrupt_reports_active_candidate(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Ctrl-C during compatibility projection never obscures committed runtime."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(config)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.30.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.publish_installed_state",
        lambda *_args: (_ for _ in ()).throw(KeyboardInterrupt()),
    )

    with pytest.raises(PointerCommittedInterrupt, match="new runtime remains active"):
        install_like_operation(config)

    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"
    assert read_slot_metadata(config.layout, "a") is not None


def test_pointer_forms_are_mutually_exclusive(tmp_path, config_factory):
    """Never accept split-brain symlink and regular-file pointer state."""

    config = config_factory(tmp_path)
    local_dir = config.layout.local_dir
    (local_dir / "slots" / "a").mkdir(parents=True)
    (local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    (local_dir / "active-slot").write_text("a\n", encoding="ascii")

    with pytest.raises(CodexWranglerError, match="Both managed active"):
        discover_active_runtime(config.layout)


@pytest.mark.parametrize("target", ["../outside", "/tmp/outside"])
def test_active_pointer_rejects_escaping_targets(tmp_path, config_factory, target):
    """Pointer syntax cannot select an arbitrary external runtime."""

    config = config_factory(tmp_path)
    config.layout.local_dir.mkdir(parents=True)
    (config.layout.local_dir / "active").symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(CodexWranglerError, match="exactly `slots/a` or `slots/b`"):
        discover_active_runtime(config.layout)


def test_strict_file_pointer_can_be_atomically_replaced(
    tmp_path,
    config_factory,
):
    """An existing fallback pointer remains the sole fallback authority."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    local_dir = config.layout.local_dir
    materialize_completed_slot(config, "a", "0.30.0")
    materialize_completed_slot(config, "b", "0.30.0")
    (local_dir / "active-slot").write_text("a\n", encoding="ascii")

    assert promote_active_slot(config.layout, "b") == "file"
    active = discover_active_runtime(config.layout)
    assert active.slot_name == "b"
    assert active.pointer_kind == "file"
    assert not (local_dir / "active").exists()


def test_pointer_replace_failure_leaves_previous_selection_intact(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A failed commit syscall preserves the former active pointer."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(config, "a", "0.30.0")
    materialize_completed_slot(config, "b", "0.30.0")
    active = config.layout.local_dir / "active"
    active.symlink_to("slots/a", target_is_directory=True)
    real_replace = os.replace

    def fail_pointer_replace(source, destination):
        if Path(destination) == active:
            raise OSError(errno.EIO, "injected pointer failure")
        return real_replace(source, destination)

    monkeypatch.setattr("codex_wrangler.slots.os.replace", fail_pointer_replace)

    with pytest.raises(CodexWranglerError, match="Failed to atomically promote"):
        promote_active_slot(config.layout, "b")

    assert str(active.readlink()) == "slots/a"
    assert read_slot_metadata(config.layout, "a") is not None
    assert read_slot_metadata(config.layout, "b") is not None


@pytest.mark.skipif(os.name == "nt", reason="POSIX symlink fallback only")
def test_first_promotion_falls_back_to_strict_file_pointer(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Filesystems without symlink creation retain an atomic pointer form."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(config, "a", "0.30.0")
    monkeypatch.setattr(
        "codex_wrangler.slots.os.symlink",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError(errno.EPERM, "symlinks unavailable")
        ),
    )

    assert promote_active_slot(config.layout, "a") == "file"
    assert (config.layout.local_dir / "active-slot").read_bytes() == b"a\n"
    assert not (config.layout.local_dir / "active").exists()


def test_repair_plan_uses_active_slot_when_root_projections_are_stale(
    tmp_path,
    config_factory,
):
    """The active completion record recovers repair after projection failure."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    candidate = config.layout.local_dir / "slots" / "a"
    candidate.mkdir(parents=True)
    candidate.joinpath("package.json").write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )
    materialize_candidate(
        ["npm", "install", "--prefix", str(candidate)],
        "0.30.0",
    )
    write_slot_metadata(config, "a")
    promote_active_slot(config.layout, "a")
    config.layout.local_package_json_path.write_text(
        build_local_package_json("0.1.0"),
        encoding="utf-8",
    )

    plan = build_repair_plan(config.layout)

    assert plan.codex_version == "0.30.0"
    assert "active slot completion record" in plan.version_evidence


def test_repair_rejects_legacy_root_conflicting_with_completed_slot(
    tmp_path,
    config_factory,
):
    """A usable root cannot hide conflicting completed rollback evidence."""

    root = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(root)
    rollback = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    materialize_completed_slot(rollback, "b", "0.31.0")

    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        build_repair_plan(root.layout)


def test_active_slot_preferences_override_stale_root_projection(
    tmp_path,
    config_factory,
):
    """Post-crash reconstruction takes launcher state from pointer authority."""

    authoritative = config_factory(
        tmp_path,
        codex_selector="latest",
        codex_channel="stable",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=True,
        available_versions={"stable": "0.30.0", "beta": None, "alpha": None},
        available_versions_updated_at="2026-09-10T00:00:00Z",
    )
    materialize_completed_slot(authoritative, "a", "0.30.0")
    promote_active_slot(authoritative.layout, "a")
    stale = config_factory(
        tmp_path,
        codex_selector="0.1.0",
        codex_channel="stable",
        codex_version="0.1.0",
        shared_home=False,
        reasonable_permissions_enabled=False,
        available_versions={"stable": "0.1.0"},
        available_versions_updated_at="2020-01-01T00:00:00Z",
    )
    write_metadata(
        stale.layout.metadata_path,
        build_metadata(stale),
        dry_run=False,
    )

    existing = read_existing_state(authoritative.layout)

    assert existing.requested_codex_selector == "latest"
    assert existing.pinned_codex_version == "0.30.0"
    assert existing.shared_home is True
    assert existing.reasonable_permissions_enabled is True
    assert existing.available_versions["stable"] == "0.30.0"
    assert existing.available_versions_updated_at == "2026-09-10T00:00:00Z"


@pytest.mark.parametrize("pointer_state", ["absent", "corrupt", "incomplete"])
def test_repair_rejects_conflicting_home_authority_without_valid_pointer(
    tmp_path,
    config_factory,
    pointer_state,
):
    """Repair refuses stale root HOME mode when completed slots disagree."""

    root_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=True,
    )
    slot_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
        reasonable_permissions_enabled=False,
    )
    materialize_completed_slot(slot_config, "a", "0.30.0")
    write_metadata(
        root_config.layout.metadata_path,
        build_metadata(root_config),
        dry_run=False,
    )
    if pointer_state == "corrupt":
        (root_config.layout.local_dir / "active").symlink_to(
            "not-a-managed-slot",
            target_is_directory=True,
        )
    elif pointer_state == "incomplete":
        (root_config.layout.local_dir / "slots" / "b").mkdir()
        (root_config.layout.local_dir / "active").symlink_to(
            "slots/b",
            target_is_directory=True,
        )

    with pytest.raises(CodexWranglerError, match="Conflicting HOME-mode"):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


def test_repair_rejects_conflicting_permissions_without_valid_pointer(
    tmp_path,
    config_factory,
):
    """Recovery also requires concordant reasonable-permissions authority."""

    root_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=True,
    )
    slot_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=False,
    )
    materialize_completed_slot(slot_config, "a", "0.30.0")
    write_metadata(
        root_config.layout.metadata_path,
        build_metadata(root_config),
        dry_run=False,
    )

    with pytest.raises(
        CodexWranglerError,
        match="Conflicting reasonable-permissions",
    ):
        config_from_args(parse_args(["--repair", str(tmp_path)]))


def test_projection_only_change_updates_slot_authority_before_root_files(
    tmp_path,
    config_factory,
):
    """Launcher preferences are durable in the active completion record."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(initial, "a", "0.30.0")
    promote_active_slot(initial.layout, "a")
    changed = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=True,
        available_versions={"stable": "0.30.0"},
        available_versions_updated_at="2026-09-10T00:00:00Z",
    )

    publish_projection_state(changed)

    slot_record = read_slot_metadata(changed.layout, "a")
    assert slot_record is not None
    assert slot_record["shared_home"] is True
    assert slot_record["reasonable_permissions_enabled"] is True
    assert slot_record["available_versions"] == {"stable": "0.30.0"}
    root_record = json.loads(changed.layout.metadata_path.read_text(encoding="utf-8"))
    assert root_record["shared_home"] is True


def test_projection_refresh_failure_retains_updated_slot_authority(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A root write failure is reported without reverting committed preferences."""

    initial = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(initial, "a", "0.30.0")
    promote_active_slot(initial.layout, "a")
    changed = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=True,
        reasonable_permissions_enabled=True,
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.write_managed_supporting_files",
        lambda _config: (_ for _ in ()).throw(OSError("projection unavailable")),
    )

    with pytest.raises(
        CodexWranglerError,
        match="configuration committed.*runtime remains active",
    ):
        publish_projection_state(changed)

    slot_record = read_slot_metadata(changed.layout, "a")
    assert slot_record is not None
    assert slot_record["shared_home"] is True
    assert slot_record["reasonable_permissions_enabled"] is True


def test_repair_refuses_incomplete_selected_slot_conflicting_with_rollback(
    tmp_path,
    config_factory,
):
    """A damaged selected tree cannot silently override a completed rollback."""

    damaged = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    rollback = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    selected = damaged.layout.local_dir / "slots" / "a"
    selected.mkdir(parents=True)
    (selected / "package.json").write_text(
        build_local_package_json("0.31.0"),
        encoding="utf-8",
    )
    materialize_completed_slot(rollback, "b", "0.30.0")
    (damaged.layout.local_dir / "active").symlink_to(
        "slots/a", target_is_directory=True
    )

    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        build_repair_plan(damaged.layout)


def test_repair_refuses_root_evidence_conflicting_with_orphan_completed_slot(
    tmp_path,
    config_factory,
):
    """A corrupt pointer requires agreement across all surviving strong evidence."""

    root_config = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    rollback = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    root_config.layout.local_dir.mkdir(parents=True)
    root_config.layout.local_package_json_path.write_text(
        build_local_package_json("0.31.0"),
        encoding="utf-8",
    )
    materialize_completed_slot(rollback, "b", "0.30.0")
    (root_config.layout.local_dir / "active").symlink_to(
        "not-a-managed-slot", target_is_directory=True
    )

    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        build_repair_plan(root_config.layout)


def test_repair_ignores_weak_manifest_reached_through_symlink_ancestor(
    tmp_path,
    config_factory,
):
    """External copied evidence cannot influence exact-version recovery."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    config.layout.local_dir.mkdir(parents=True)
    write_metadata(
        config.layout.metadata_path,
        build_metadata(config),
        dry_run=False,
    )
    outside = tmp_path / "outside"
    outside_manifest = outside / "codex" / "package.json"
    outside_manifest.parent.mkdir(parents=True)
    outside_manifest.write_text('{"version":"0.31.0"}\n', encoding="utf-8")
    node_modules = config.layout.local_node_modules_dir
    node_modules.mkdir()
    (node_modules / "@openai").symlink_to(outside, target_is_directory=True)

    plan = build_repair_plan(config.layout)

    assert plan.codex_version == "0.30.0"
    assert not any("@openai/codex" in source for source in plan.version_evidence)


def test_maintenance_lock_rejects_concurrent_writer(tmp_path, config_factory):
    """Only one transaction may select and mutate an inactive slot."""

    config = config_factory(tmp_path)
    with MaintenanceLock(config.layout):
        with pytest.raises(CodexWranglerError, match="already active"):
            with MaintenanceLock(config.layout):
                pass


def test_maintenance_lock_releases_after_keyboard_interrupt(tmp_path, config_factory):
    """An interrupted owner cannot strand the stable lock as logically held."""

    config = config_factory(tmp_path)
    with pytest.raises(KeyboardInterrupt):
        with MaintenanceLock(config.layout):
            raise KeyboardInterrupt

    with MaintenanceLock(config.layout):
        pass


@pytest.mark.parametrize(
    ("local_dir_raw", "link_relative", "escaped_lock_relative"),
    [
        (".codex-local", ".codex-local", ".maintenance.lock"),
        (
            "managed/.codex-local",
            "managed",
            ".codex-local/.maintenance.lock",
        ),
    ],
)
def test_maintenance_lock_rejects_linked_managed_root_before_writing(
    tmp_path,
    config_factory,
    local_dir_raw,
    link_relative,
    escaped_lock_relative,
):
    """A linked managed root or ancestor cannot redirect the lock outside."""

    project = tmp_path / "project"
    outside = tmp_path / "outside"
    project.mkdir()
    outside.mkdir()
    config = config_factory(project, local_dir_raw=local_dir_raw)
    (project / link_relative).symlink_to(outside, target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="may not be a symbolic link"):
        with MaintenanceLock(config.layout):
            pass

    assert not (outside / escaped_lock_relative).exists()


def test_incomplete_active_slot_refuses_install_and_preserves_rollback(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Normal maintenance cannot erase B when an invalid pointer selects A."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    slot_b = materialize_completed_slot(config, "b", "0.30.0")
    rollback_sentinel = slot_b / "rollback-sentinel"
    rollback_sentinel.write_text("preserve B\n", encoding="utf-8")
    (config.layout.local_dir / "slots" / "a").mkdir()
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    configure_install_mocks(monkeypatch, config)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("candidate npm must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="incomplete or unverified"):
        install_like_operation(config)

    assert called is False
    assert rollback_sentinel.read_text(encoding="utf-8") == "preserve B\n"
    assert not list(config.layout.local_dir.glob(".candidate-*"))
    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"


def test_package_manifest_alone_is_not_a_usable_legacy_runtime(
    tmp_path,
    config_factory,
):
    """Stale package metadata cannot stand in for an executable rollback."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    config.layout.local_dir.mkdir(parents=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json("0.30.0"),
        encoding="utf-8",
    )

    active = discover_active_runtime(config.layout)

    assert active.kind == "none"
    assert active.prefix is None


def test_completed_orphan_slot_refuses_normal_install_without_pointer(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Normal maintenance will not guess which unselected slot is active."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    orphan = materialize_completed_slot(config, "a", "0.30.0")
    sentinel = orphan / "preserve-me"
    sentinel.write_text("orphan evidence\n", encoding="utf-8")
    configure_install_mocks(monkeypatch, config)
    called = False

    def forbidden_run(*_args, **_kwargs):
        nonlocal called
        called = True
        raise AssertionError("candidate npm must not run")

    monkeypatch.setattr("codex_wrangler.operations.run_command", forbidden_run)

    with pytest.raises(CodexWranglerError, match="no active pointer"):
        install_like_operation(config)

    assert called is False
    assert sentinel.read_text(encoding="utf-8") == "orphan evidence\n"
    assert not list(config.layout.local_dir.glob(".candidate-*"))


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), OSError("rename failed")])
def test_candidate_activation_observes_committed_rename_before_rollback(
    monkeypatch,
    tmp_path,
    config_factory,
    failure,
):
    """A signal after rename(2) commit still restores the prior inactive slot."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    local_dir = config.layout.local_dir
    fixed = materialize_completed_slot(config, "b", "0.30.0")
    (fixed / "identity").write_text("old B\n", encoding="utf-8")
    candidate = local_dir / ".candidate-test"
    candidate.mkdir()
    (candidate / "identity").write_text("new candidate\n", encoding="utf-8")
    real_replace = __import__("os").replace
    injected = False

    def interrupt_after_replace(source, destination):
        nonlocal injected
        real_replace(source, destination)
        if Path(source) == candidate and Path(destination) == fixed and not injected:
            injected = True
            raise failure

    monkeypatch.setattr("codex_wrangler.slots.os.replace", interrupt_after_replace)

    expected = (
        KeyboardInterrupt
        if isinstance(failure, KeyboardInterrupt)
        else CodexWranglerError
    )
    with pytest.raises(expected):
        activate_candidate_slot(config.layout, candidate, "b")

    assert (fixed / "identity").read_text(encoding="utf-8") == "old B\n"
    assert (candidate / "identity").read_text(encoding="utf-8") == "new candidate\n"
    assert not list((local_dir / "slots").glob(".b.retired-*"))


def test_candidate_activation_refuses_unverified_occupied_slot(
    tmp_path,
    config_factory,
):
    """An occupied slot without valid managed authority is never retired."""

    config = config_factory(tmp_path)
    local_dir = config.layout.local_dir
    fixed = local_dir / "slots" / "b"
    fixed.mkdir(parents=True)
    protected = fixed / "operator-data"
    protected.write_text("must survive\n", encoding="utf-8")
    candidate = local_dir / ".candidate-test"
    candidate.mkdir()
    candidate_sentinel = candidate / "candidate-data"
    candidate_sentinel.write_text("candidate survives\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="valid managed completion record"):
        activate_candidate_slot(config.layout, candidate, "b")

    assert protected.read_text(encoding="utf-8") == "must survive\n"
    assert candidate_sentinel.read_text(encoding="utf-8") == "candidate survives\n"
    assert not list((local_dir / "slots").glob(".b.retired-*"))


def test_post_activation_validation_failure_restores_retired_inactive_slot(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A fixed-slot flush failure before pointer commit restores old B."""

    active_config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    old_inactive = config_factory(
        tmp_path,
        codex_selector="0.29.0",
        codex_version="0.29.0",
    )
    target = config_factory(
        tmp_path,
        codex_selector="0.31.0",
        codex_version="0.31.0",
    )
    materialize_completed_slot(active_config, "a", "0.30.0")
    old_b = materialize_completed_slot(old_inactive, "b", "0.29.0")
    (old_b / "rollback-sentinel").write_text("old B\n", encoding="utf-8")
    (target.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    configure_install_mocks(monkeypatch, target)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.31.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )
    flush_calls = 0

    def fail_second_tree_flush(_prefix):
        nonlocal flush_calls
        flush_calls += 1
        if flush_calls == 2:
            raise CodexWranglerError("post-activation flush failed")

    monkeypatch.setattr(
        "codex_wrangler.operations.fsync_candidate_tree",
        fail_second_tree_flush,
    )

    with pytest.raises(CodexWranglerError, match="not promoted"):
        install_like_operation(target)

    assert str((target.layout.local_dir / "active").readlink()) == "slots/a"
    assert (target.layout.local_dir / "slots" / "b" / "rollback-sentinel").read_text(
        encoding="utf-8"
    ) == "old B\n"
    assert read_slot_metadata(target.layout, "a") is not None
    assert read_slot_metadata(target.layout, "b")["codex_version"] == "0.29.0"


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="FIFO support required")
def test_candidate_tree_flush_rejects_fifo_without_blocking(
    tmp_path,
    config_factory,
):
    """Durability traversal rejects special files before opening them."""

    config = config_factory(tmp_path)
    candidate = config.layout.local_dir / ".candidate-fifo"
    candidate.mkdir(parents=True)
    os.mkfifo(candidate / "blocked-reader")

    with pytest.raises(CodexWranglerError, match="non-regular file"):
        fsync_candidate_tree(candidate)


def test_pointer_fsync_interrupt_is_reported_as_committed(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Ctrl-C after pointer replacement cannot be treated as pre-commit."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_completed_slot(config, "a", "0.30.0")
    materialize_completed_slot(config, "b", "0.30.0")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)

    def interrupt_flush(_path):
        raise KeyboardInterrupt

    monkeypatch.setattr("codex_wrangler.slots.fsync_directory", interrupt_flush)

    with pytest.raises(PointerCommittedInterrupt):
        promote_active_slot(config.layout, "b")

    assert str((config.layout.local_dir / "active").readlink()) == "slots/b"
    assert read_slot_metadata(config.layout, "b") is not None


def test_repair_of_incomplete_active_slot_retains_previous_rollback(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """Repair may replace B only after validation and quarantines old B."""

    config = config_factory(
        tmp_path,
        operation="repair",
        repair_install=True,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        version_source="repair evidence: managed package.json",
    )
    materialize_legacy_install(config)
    (config.layout.local_dir / "slots" / "a").mkdir(parents=True)
    (config.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json").write_text(
        "{truncated\n", encoding="utf-8"
    )
    slot_b = materialize_completed_slot(config, "b", "0.30.0")
    (slot_b / "rollback-sentinel").write_text("old B\n", encoding="utf-8")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.30.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )

    assert install_like_operation(config) == 0

    assert str((config.layout.local_dir / "active").readlink()) == "slots/b"
    retired = list((config.layout.local_dir / "slots").glob(".b.retired-*"))
    assert len(retired) == 1
    assert (retired[0] / "rollback-sentinel").read_text(encoding="utf-8") == "old B\n"
    assert (
        config.layout.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    ).exists()


def test_failed_repair_of_incomplete_active_slot_preserves_both_slots(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A failed repair npm command does not touch either damaged or rollback slot."""

    config = config_factory(
        tmp_path,
        operation="repair",
        repair_install=True,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        version_source="repair evidence: managed package.json",
    )
    materialize_legacy_install(config)
    slot_a = config.layout.local_dir / "slots" / "a"
    slot_a.mkdir(parents=True)
    (slot_a / "damaged-sentinel").write_text("damaged A evidence\n", encoding="utf-8")
    slot_b = materialize_completed_slot(config, "b", "0.30.0")
    (slot_b / "rollback-sentinel").write_text("old B\n", encoding="utf-8")
    (config.layout.local_dir / "active").symlink_to("slots/a", target_is_directory=True)
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CodexWranglerError("npm timed out")
        ),
    )

    with pytest.raises(CodexWranglerError, match="not promoted"):
        install_like_operation(config)

    assert (slot_a / "damaged-sentinel").read_text(
        encoding="utf-8"
    ) == "damaged A evidence\n"
    assert (slot_b / "rollback-sentinel").read_text(encoding="utf-8") == "old B\n"
    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"
    assert not list((config.layout.local_dir / "slots").glob(".*.retired-*"))


def test_slot_completion_rejects_linked_evidence_ancestor(
    tmp_path,
    config_factory,
):
    """A completion record cannot authorize manifests reached through a link."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    slot = materialize_completed_slot(config, "a", "0.30.0")
    installed = slot / "node_modules" / "@openai" / "codex" / "package.json"
    installed.unlink()
    installed.parent.rmdir()
    installed.parent.parent.rmdir()
    outside_scope = tmp_path / "outside-scope"
    outside_manifest = outside_scope / "codex" / "package.json"
    outside_manifest.parent.mkdir(parents=True)
    outside_manifest.write_text('{"version":"0.30.0"}\n', encoding="utf-8")
    (slot / "node_modules" / "@openai").symlink_to(
        outside_scope, target_is_directory=True
    )

    assert read_slot_metadata(config.layout, "a") is None


def test_repair_replaces_single_corrupt_pointer_only_after_candidate_validates(
    monkeypatch,
    tmp_path,
    config_factory,
):
    """A malformed sole pointer is recoverable from surviving root evidence."""

    config = config_factory(
        tmp_path,
        operation="repair",
        repair_install=True,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        version_source="repair evidence: managed package.json",
    )
    materialize_legacy_install(config)
    (config.layout.local_dir / "active").symlink_to(
        "not-a-managed-slot", target_is_directory=True
    )
    configure_install_mocks(monkeypatch, config)
    monkeypatch.setattr(
        "codex_wrangler.operations.run_command",
        lambda command, *_args, **_kwargs: materialize_candidate(command, "0.30.0"),
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.verify_local_codex_binary",
        lambda _config, **_kwargs: None,
    )

    assert build_repair_plan(config.layout).codex_version == "0.30.0"
    assert install_like_operation(config) == 0

    assert str((config.layout.local_dir / "active").readlink()) == "slots/a"
    assert read_slot_metadata(config.layout, "a") is not None


def test_repair_refuses_symbolic_root_lockfile_projection(
    tmp_path,
    config_factory,
):
    """Repair preflights every root projection it would rewrite post-commit."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    materialize_legacy_install(config)
    outside = tmp_path / "outside-lock.json"
    outside.write_text("{}\n", encoding="utf-8")
    config.layout.local_package_lock_path.unlink()
    config.layout.local_package_lock_path.symlink_to(outside)

    with pytest.raises(CodexWranglerError, match="symbolic-link targets"):
        build_repair_plan(config.layout)


def remove_slot_path_tuple(prefix: Path) -> None:
    """Rewrite one completion record into its historical pathless shape."""

    record_path = prefix / ".codex-wrangler-slot.json"
    payload = json.loads(record_path.read_text(encoding="utf-8"))
    payload.pop("paths")
    record_path.write_text(json.dumps(payload), encoding="utf-8")


def test_canonical_slot_metadata_requires_complete_path_tuple(
    tmp_path,
    config_factory,
):
    """A pathless record cannot claim the newly introduced canonical layout."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    prefix = materialize_completed_slot(config, "a", "0.30.0")
    remove_slot_path_tuple(prefix)

    assert read_slot_metadata(config.layout, "a") is None


def test_historical_default_slot_metadata_may_omit_path_tuple(
    tmp_path,
    config_factory,
):
    """The complete old default layout remains valid migration evidence."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    prefix = materialize_completed_slot(legacy, "a", "0.30.0")
    remove_slot_path_tuple(prefix)

    assert read_slot_metadata(legacy.layout, "a") is not None


def test_pathless_legacy_slot_metadata_rejects_custom_home(
    tmp_path,
    config_factory,
):
    """An old runtime basename alone does not prove the historical layout."""

    mixed = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=".custom-codex-home",
        codex_selector="0.30.0",
        codex_version="0.30.0",
    )
    prefix = materialize_completed_slot(mixed, "a", "0.30.0")
    remove_slot_path_tuple(prefix)

    assert read_slot_metadata(mixed.layout, "a") is None


def test_isolated_historical_slot_alias_requires_home_compatibility(
    tmp_path,
    config_factory,
):
    """Only migration proof may trust an isolated record between exchanges."""

    legacy = config_factory(
        tmp_path,
        local_dir_raw=LEGACY_LOCAL_DIR,
        codex_home_raw=LEGACY_HOME_DIR,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    canonical = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        shared_home=False,
    )
    prefix = materialize_completed_slot(legacy, "a", "0.30.0")
    remove_slot_path_tuple(prefix)
    legacy.layout.codex_home_dir.mkdir()
    canonical.layout.local_dir.parent.mkdir(parents=True)
    legacy.layout.local_dir.rename(canonical.layout.local_dir)
    legacy.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )

    assert read_slot_metadata(canonical.layout, "a") is None
    assert (
        read_slot_metadata(
            canonical.layout,
            "a",
            allow_pending_legacy_home=True,
        )
        is not None
    )

    canonical.layout.codex_home_dir.mkdir()
    sentinel = canonical.layout.codex_home_dir / "unowned-context"
    sentinel.write_text("preserve\n", encoding="utf-8")
    assert (
        read_slot_metadata(
            canonical.layout,
            "a",
            allow_pending_legacy_home=True,
        )
        is None
    )
    sentinel.unlink()
    canonical.layout.codex_home_dir.rmdir()

    canonical.layout.codex_home_dir.symlink_to(
        DEFAULT_HOME_DIR,
        target_is_directory=True,
    )
    assert (
        read_slot_metadata(
            canonical.layout,
            "a",
            allow_pending_legacy_home=True,
        )
        is not None
    )
    canonical.layout.codex_home_dir.unlink()

    legacy.layout.codex_home_dir.rename(canonical.layout.codex_home_dir)
    legacy.layout.codex_home_dir.symlink_to(
        DEFAULT_HOME_DIR,
        target_is_directory=True,
    )

    assert read_slot_metadata(canonical.layout, "a") is not None
