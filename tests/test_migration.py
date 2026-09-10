"""Regression tests for canonical default-layout migration."""

from __future__ import annotations

from dataclasses import replace
import json
import os
import subprocess

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.constants import DEFAULT_HOME_DIR, DEFAULT_LOCAL_DIR
from codex_wrangler.migration import (
    _exchange_directory_with_compatibility_link,
    _external_exchange_python,
    apply_default_layout_migration,
    atomic_exchange_supported,
    build_default_layout_migration,
    discover_pending_legacy_home_mode,
    legacy_layout_from_canonical,
    prove_legacy_isolated_home,
)
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    gather_inspection_report,
    require_existing_managed_install,
)
from codex_wrangler.slots import (
    discover_active_runtime,
    layout_for_slot,
    read_slot_metadata,
    write_slot_metadata,
)
from codex_wrangler.rendering import (
    build_gitignore_block,
    build_local_package_json,
    build_metadata,
)


def materialize_legacy_defaults(config, *, include_home=True):
    """Create strong legacy package ownership and optional protected context."""

    legacy = legacy_layout_from_canonical(config.layout)
    legacy.local_dir.mkdir(parents=True)
    legacy.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    legacy.metadata_path.write_text(
        json.dumps(
            build_metadata(replace(config, layout=legacy)),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    package_sentinel = legacy.local_dir / "runtime-sentinel"
    package_sentinel.write_text("preserve runtime\n", encoding="utf-8")
    context_sentinel = None
    if include_home:
        context_sentinel = legacy.codex_home_dir / ".codex" / "sessions" / "sentinel"
        context_sentinel.parent.mkdir(parents=True)
        context_sentinel.write_text("preserve context\n", encoding="utf-8")
    return legacy, package_sentinel, context_sentinel


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_atomic_exchange_moves_real_directory_without_missing_legacy_name(tmp_path):
    """One exchange preserves inode/content and leaves the exact old-name link."""

    legacy = tmp_path / ".codex-home"
    canonical = tmp_path / ".local" / "codex-home"
    legacy.mkdir()
    sentinel = legacy / "sentinel"
    sentinel.write_text("context\n", encoding="utf-8")
    legacy_inode = legacy.stat().st_ino

    _exchange_directory_with_compatibility_link(
        legacy,
        canonical,
        DEFAULT_HOME_DIR,
        "test context",
    )

    assert canonical.stat().st_ino == legacy_inode
    assert (canonical / "sentinel").read_text(encoding="utf-8") == "context\n"
    assert legacy.is_symlink()
    assert os.readlink(legacy) == DEFAULT_HOME_DIR
    assert (legacy / "sentinel").samefile(canonical / "sentinel")


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_apply_migration_preserves_runtime_and_context_identity(
    tmp_path,
    config_factory,
):
    """A full legacy move preserves both directory identities and contents."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
    )
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(config)
    package_inode = legacy.local_dir.stat().st_ino
    home_inode = legacy.codex_home_dir.stat().st_ino
    plan = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=False,
    )
    assert plan is not None
    config.layout_migration = plan

    apply_default_layout_migration(config)

    assert config.layout.local_dir.stat().st_ino == package_inode
    assert config.layout.codex_home_dir.stat().st_ino == home_inode
    assert (config.layout.local_dir / package_sentinel.name).read_bytes() == (
        b"preserve runtime\n"
    )
    moved_context = config.layout.codex_home_dir / ".codex" / "sessions" / "sentinel"
    assert moved_context.read_bytes() == b"preserve context\n"
    assert context_sentinel is not None
    assert legacy.local_dir.is_symlink()
    assert os.readlink(legacy.local_dir) == DEFAULT_LOCAL_DIR
    assert legacy.codex_home_dir.is_symlink()
    assert os.readlink(legacy.codex_home_dir) == DEFAULT_HOME_DIR
    launcher = config.layout.launcher_path.read_text(encoding="utf-8")
    assert "canonical_local_root_staged" in launcher
    assert 'export CODEX_HOME="$HOME/.codex"' in launcher
    assert (
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )
        is None
    )


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_migration_completes_exact_preexchange_staged_link(
    tmp_path,
    config_factory,
):
    """A power-loss staging link can be consumed by the next locked attempt."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=True,
    )
    legacy, _, _ = materialize_legacy_defaults(config, include_home=False)
    config.layout.local_dir.parent.mkdir(parents=True)
    config.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )
    plan = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=True,
    )
    assert plan is not None
    assert plan.local_state == "staged"
    config.layout_migration = plan

    apply_default_layout_migration(config)

    assert config.layout.local_dir.is_dir()
    assert not config.layout.local_dir.is_symlink()
    assert legacy.local_dir.is_symlink()
    assert os.readlink(legacy.local_dir) == DEFAULT_LOCAL_DIR


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_migration_publishes_ignore_before_bridge_launcher(
    tmp_path,
    config_factory,
    monkeypatch,
):
    """A bridge-launcher failure leaves legacy runtime and context safely ignored."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=False,
    )
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(config)
    config.layout_migration = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=False,
    )

    def fail_bridge_launcher(*args, **kwargs):
        raise CodexWranglerError("simulated bridge launcher failure")

    monkeypatch.setattr(
        "codex_wrangler.migration.write_text_file",
        fail_bridge_launcher,
    )

    with pytest.raises(CodexWranglerError, match="bridge launcher failure"):
        apply_default_layout_migration(config)

    assert config.layout.gitignore_path.read_text(encoding="utf-8") == (
        build_gitignore_block(config.layout)
    )
    assert not os.path.lexists(config.layout.launcher_path)
    assert legacy.local_dir.is_dir()
    assert not legacy.local_dir.is_symlink()
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert context_sentinel is not None
    assert context_sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert legacy.codex_home_dir.is_dir()
    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)


def test_migration_refuses_two_real_runtime_trees(tmp_path, config_factory):
    """Automatic migration never merges two independently populated trees."""

    config = config_factory(tmp_path)
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        config,
        include_home=False,
    )
    config.layout.local_dir.mkdir(parents=True)
    canonical_sentinel = config.layout.local_dir / "canonical-sentinel"
    canonical_sentinel.write_text("canonical\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="ambiguous or conflicting"):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=True,
        )

    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert canonical_sentinel.read_text(encoding="utf-8") == "canonical\n"


def test_runtime_migration_refuses_nonempty_unbound_canonical_home(
    tmp_path,
    config_factory,
):
    """Legacy runtime authority cannot silently absorb an unrelated HOME."""

    config = config_factory(tmp_path)
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        config,
        include_home=False,
    )
    config.layout.codex_home_dir.mkdir(parents=True)
    canonical_sentinel = config.layout.codex_home_dir / "foreign-context"
    canonical_sentinel.write_text("preserve canonical context\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="canonical isolated HOME is nonempty"):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )
    with pytest.raises(CodexWranglerError, match="canonical isolated HOME is nonempty"):
        config_from_args(parse_args(["--force", str(tmp_path)]))

    assert legacy.local_dir.is_dir()
    assert not legacy.local_dir.is_symlink()
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert not os.path.lexists(config.layout.local_dir)
    assert canonical_sentinel.read_text(encoding="utf-8") == (
        "preserve canonical context\n"
    )
    assert not os.path.lexists(legacy.codex_home_dir)


@pytest.mark.parametrize("canonical_home_exists", [False, True])
def test_isolated_runtime_migration_requires_real_legacy_home(
    tmp_path,
    config_factory,
    canonical_home_exists,
):
    """Absent old HOME state cannot yield an unusable post-runtime midpoint."""

    config = config_factory(tmp_path)
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        config,
        include_home=False,
    )
    if canonical_home_exists:
        config.layout.codex_home_dir.mkdir(parents=True)
    runtime_inode = legacy.local_dir.stat().st_ino

    with pytest.raises(
        CodexWranglerError,
        match="no real legacy isolated HOME",
    ):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )

    assert legacy.local_dir.stat().st_ino == runtime_inode
    assert not legacy.local_dir.is_symlink()
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(legacy.codex_home_dir)
    assert os.path.lexists(config.layout.codex_home_dir) is canonical_home_exists


def test_runtime_only_partial_state_cannot_claim_unbound_canonical_home(
    tmp_path,
    config_factory,
):
    """Old metadata behind only the runtime link is not canonical authority."""

    config = config_factory(tmp_path)
    legacy = legacy_layout_from_canonical(config.layout)
    config.layout.local_dir.mkdir(parents=True)
    legacy.local_dir.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    config.layout.metadata_path.write_text(
        json.dumps(
            build_metadata(replace(config, layout=legacy)),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    metadata_before = config.layout.metadata_path.read_bytes()
    config.layout.codex_home_dir.mkdir()
    sentinel = config.layout.codex_home_dir / "foreign-context"
    sentinel.write_text("preserve\n", encoding="utf-8")

    resolved = config_from_args(parse_args(["--update", str(tmp_path)]))

    assert resolved.layout_migration is None
    with pytest.raises(CodexWranglerError, match="No exactly bound managed"):
        require_existing_managed_install(resolved)
    assert config.layout.metadata_path.read_bytes() == metadata_before
    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert not os.path.lexists(legacy.codex_home_dir)


def test_mutating_discovery_requires_legacy_ownership(tmp_path):
    """An arbitrary old-name directory is not silently adopted as managed."""

    (tmp_path / ".codex-local").mkdir()

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        config_from_args(parse_args(["--update", str(tmp_path)]))

    inspection = config_from_args(parse_args(["--inspect", str(tmp_path)]))
    assert inspection.layout.local_dir == tmp_path / ".codex-local"
    assert inspection.layout_migration is not None


def test_dry_run_keeps_legacy_paths_and_reports_pending_plan(
    tmp_path,
    config_factory,
):
    """Dry-run discovery remains entirely on the current legacy namespace."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
    )
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        config,
        include_home=False,
    )

    observed = config_from_args(parse_args(["--dry-run", str(tmp_path)]))

    assert observed.layout == legacy
    assert observed.layout_migration is not None
    assert package_sentinel.exists()
    assert not config.layout.local_dir.exists()


def test_runtime_first_partial_rejects_forced_home_mode_transition(
    tmp_path,
    config_factory,
):
    """A crash after runtime exchange cannot hide the recorded isolated mode."""

    config = config_factory(tmp_path, shared_home=False)
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(config)
    config.layout.local_dir.parent.mkdir(parents=True)
    runtime_inode = legacy.local_dir.stat().st_ino
    legacy.local_dir.rename(config.layout.local_dir)
    legacy.local_dir.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    metadata_before = config.layout.metadata_path.read_bytes()

    recovered = config_from_args(parse_args([str(tmp_path)]))

    assert recovered.shared_home is False
    assert recovered.layout_migration is not None
    assert recovered.layout_migration.migrate_local is False
    assert recovered.layout_migration.migrate_home is True

    with pytest.raises(CodexWranglerError, match="Cannot change.*HOME"):
        config_from_args(parse_args(["--shared-home", "--force", str(tmp_path)]))

    assert config.layout.local_dir.stat().st_ino == runtime_inode
    assert legacy.local_dir.is_symlink()
    assert os.readlink(legacy.local_dir) == DEFAULT_LOCAL_DIR
    assert config.layout.metadata_path.read_bytes() == metadata_before
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert context_sentinel is not None
    assert context_sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert legacy.codex_home_dir.is_dir()
    assert not os.path.lexists(config.layout.codex_home_dir)


@pytest.mark.parametrize("pathless_slot_record", [False, True])
def test_runtime_first_partial_prefers_active_slot_over_stale_root_home_mode(
    tmp_path,
    config_factory,
    pathless_slot_record,
):
    """A committed slot recovers HOME migration despite a stale root projection."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=False,
    )
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(config)
    materialize_legacy_completed_slot(config, legacy, "a", "0.154.0")
    (legacy.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )

    slot_record = legacy.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    if pathless_slot_record:
        slot_payload = json.loads(slot_record.read_text(encoding="utf-8"))
        slot_payload.pop("paths")
        slot_record.write_text(json.dumps(slot_payload), encoding="utf-8")

    # Simulate a stale post-commit root projection from the formerly selected
    # shared-HOME state, then a power loss after only the runtime exchange.
    root_payload = json.loads(legacy.metadata_path.read_text(encoding="utf-8"))
    root_payload["shared_home"] = True
    legacy.metadata_path.write_text(json.dumps(root_payload), encoding="utf-8")
    config.layout.local_dir.parent.mkdir(parents=True)
    runtime_inode = legacy.local_dir.stat().st_ino
    legacy.local_dir.rename(config.layout.local_dir)
    legacy.local_dir.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)

    recovered = config_from_args(parse_args([str(tmp_path)]))

    assert recovered.shared_home is False
    assert recovered.layout_migration is not None
    assert recovered.layout_migration.migrate_local is False
    assert recovered.layout_migration.migrate_home is True
    assert recovered.layout_migration.state_layout == config.layout
    assert config.layout.local_dir.stat().st_ino == runtime_inode
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert context_sentinel is not None
    assert context_sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert legacy.codex_home_dir.is_dir()
    assert not os.path.lexists(config.layout.codex_home_dir)

    with pytest.raises(CodexWranglerError, match="Cannot change.*HOME"):
        config_from_args(parse_args(["--shared-home", "--force", str(tmp_path)]))


@pytest.mark.parametrize(
    ("old_shared_home", "mode_flag"),
    [(False, "--shared-home"), (True, "--isolated-home")],
)
def test_pending_migration_rejects_explicit_home_mode_transition(
    tmp_path,
    config_factory,
    old_shared_home,
    mode_flag,
):
    """A migration bridge never has to reinterpret an old slot's HOME mode."""

    config = config_factory(tmp_path, shared_home=old_shared_home)
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(
        config,
        include_home=not old_shared_home,
    )

    with pytest.raises(CodexWranglerError, match="Cannot change.*HOME"):
        config_from_args(parse_args([mode_flag, str(tmp_path)]))

    assert legacy.local_dir.is_dir()
    assert not legacy.local_dir.is_symlink()
    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert not os.path.lexists(config.layout.local_dir)
    if context_sentinel is not None:
        assert context_sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert not os.path.lexists(config.layout.codex_home_dir)


def test_actual_update_targets_canonical_layout_from_legacy_evidence(
    tmp_path,
    config_factory,
):
    """A mutating default-layout operation carries legacy authority forward."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=True,
    )
    legacy, _, _ = materialize_legacy_defaults(config, include_home=False)

    resolved = config_from_args(parse_args(["--update", str(tmp_path)]))

    assert resolved.layout.local_dir_relative == DEFAULT_LOCAL_DIR
    assert resolved.layout.codex_home_relative == DEFAULT_HOME_DIR
    assert resolved.layout_migration is not None
    assert resolved.layout_migration.state_layout == legacy


def test_unsupported_exchange_changes_no_managed_path(
    tmp_path,
    config_factory,
    monkeypatch,
):
    """A host without atomic exchange support fails before bridge publication."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
    )
    legacy, package_sentinel, context_sentinel = materialize_legacy_defaults(config)
    plan = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=False,
    )
    assert plan is not None
    config.layout_migration = plan
    monkeypatch.setattr(
        "codex_wrangler.migration.atomic_exchange_supported",
        lambda: False,
    )

    with pytest.raises(CodexWranglerError, match="no path was changed"):
        apply_default_layout_migration(config)

    assert package_sentinel.read_bytes() == b"preserve runtime\n"
    assert context_sentinel is not None and context_sentinel.exists()
    assert not config.layout.local_dir.exists()
    assert not config.layout.codex_home_dir.exists()
    assert not config.layout.launcher_path.exists()
    assert not config.layout.gitignore_path.exists()


def materialize_legacy_completed_slot(config, legacy, slot_name, version):
    """Create one complete historical A/B slot with exact package evidence."""

    slot_config = replace(
        config,
        layout=legacy,
        codex_selector=version,
        codex_version=version,
    )
    slot_layout = layout_for_slot(legacy, slot_name)
    slot_layout.local_dir.mkdir(parents=True)
    slot_layout.local_package_json_path.write_text(
        build_local_package_json(version),
        encoding="utf-8",
    )
    slot_layout.local_package_lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "node_modules/@openai/codex": {
                        "version": version,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    installed = (
        slot_layout.local_node_modules_dir / "@openai" / "codex" / "package.json"
    )
    installed.parent.mkdir(parents=True)
    installed.write_text(
        json.dumps({"version": version}),
        encoding="utf-8",
    )
    write_slot_metadata(slot_config, slot_name)


def test_pending_migration_rejects_conflicting_home_evidence_without_pointer(
    tmp_path,
    config_factory,
):
    """Root projection cannot outvote completed slots when no pointer survives."""

    root_config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=True,
    )
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        root_config,
        include_home=False,
    )
    isolated_config = replace(root_config, shared_home=False)
    materialize_legacy_completed_slot(
        isolated_config,
        legacy,
        "a",
        "0.154.0",
    )

    with pytest.raises(
        CodexWranglerError,
        match="Conflicting legacy HOME-mode recovery evidence",
    ):
        discover_pending_legacy_home_mode(root_config.layout, legacy)

    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert not os.path.lexists(root_config.layout.local_dir)
    assert not os.path.lexists(root_config.layout.launcher_path)


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_apply_migration_rechecks_home_authority_after_lock_wait(
    tmp_path,
    config_factory,
):
    """A stale migration plan cannot outvote a newly committed active slot."""

    planned_config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=True,
    )
    legacy, package_sentinel, _ = materialize_legacy_defaults(
        planned_config,
        include_home=False,
    )
    planned_config.layout_migration = build_default_layout_migration(
        planned_config.layout,
        legacy,
        shared_home=True,
    )

    isolated_config = replace(planned_config, shared_home=False)
    materialize_legacy_completed_slot(
        isolated_config,
        legacy,
        "a",
        "0.154.0",
    )
    (legacy.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    legacy.codex_home_dir.mkdir()
    context_sentinel = legacy.codex_home_dir / "protected-context"
    context_sentinel.write_text("preserve context\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="HOME-mode authority changed"):
        apply_default_layout_migration(planned_config)

    assert package_sentinel.read_text(encoding="utf-8") == "preserve runtime\n"
    assert context_sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert legacy.local_dir.is_dir()
    assert not legacy.local_dir.is_symlink()
    assert not os.path.lexists(planned_config.layout.local_dir)
    assert not os.path.lexists(planned_config.layout.codex_home_dir)
    assert not os.path.lexists(planned_config.layout.launcher_path)
    assert not os.path.lexists(planned_config.layout.gitignore_path)


@pytest.mark.parametrize("root_metadata_state", ["absent", "corrupt"])
def test_pathless_active_slot_proves_home_without_valid_root_metadata(
    tmp_path,
    config_factory,
    root_metadata_state,
):
    """A validated active old slot can prove its historical isolated HOME."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=False,
    )
    legacy = legacy_layout_from_canonical(config.layout)
    materialize_legacy_completed_slot(config, legacy, "a", "0.154.0")
    slot_record = legacy.local_dir / "slots" / "a" / ".codex-wrangler-slot.json"
    slot_payload = json.loads(slot_record.read_text(encoding="utf-8"))
    slot_payload.pop("paths")
    slot_record.write_text(json.dumps(slot_payload), encoding="utf-8")
    (legacy.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    legacy.codex_home_dir.mkdir()
    sentinel = legacy.codex_home_dir / "preserved-context"
    sentinel.write_text("preserve context\n", encoding="utf-8")
    if root_metadata_state == "corrupt":
        legacy.metadata_path.write_text("{not-json", encoding="utf-8")

    evidence = prove_legacy_isolated_home(config.layout, legacy)
    plan = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=False,
    )

    assert evidence == (str(slot_record),)
    assert plan is not None
    assert plan.migrate_local is True
    assert plan.migrate_home is True
    assert sentinel.read_text(encoding="utf-8") == "preserve context\n"
    assert not os.path.lexists(config.layout.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_migration_preserves_legacy_ab_pointer_and_rollback_slot(
    tmp_path,
    config_factory,
):
    """The runtime tree moves as one inode with both complete slots intact."""

    config = config_factory(
        tmp_path,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        shared_home=True,
    )
    legacy, _, _ = materialize_legacy_defaults(config, include_home=False)
    materialize_legacy_completed_slot(config, legacy, "a", "0.154.0")
    materialize_legacy_completed_slot(config, legacy, "b", "0.153.0")
    (legacy.local_dir / "active").symlink_to(
        "slots/a",
        target_is_directory=True,
    )
    original_inode = legacy.local_dir.stat().st_ino
    config.layout_migration = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=True,
    )

    apply_default_layout_migration(config)

    assert config.layout.local_dir.stat().st_ino == original_inode
    assert discover_active_runtime(config.layout).slot_name == "a"
    assert read_slot_metadata(config.layout, "a")["codex_version"] == "0.154.0"
    assert read_slot_metadata(config.layout, "b")["codex_version"] == "0.153.0"


def test_home_only_migration_requires_exact_managed_layout_metadata(
    tmp_path,
    config_factory,
):
    """An unrelated old-name HOME is never inferred from package ownership."""

    config = config_factory(tmp_path)
    config.layout.local_dir.mkdir(parents=True)
    config.layout.local_package_json_path.write_text(
        build_local_package_json(config.codex_version),
        encoding="utf-8",
    )
    legacy = legacy_layout_from_canonical(config.layout)
    legacy.codex_home_dir.mkdir()

    with pytest.raises(CodexWranglerError, match="exact historical"):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )

    assert legacy.codex_home_dir.is_dir()
    assert not config.layout.codex_home_dir.exists()


def test_home_only_migration_rejects_unbound_old_metadata_in_canonical_runtime(
    tmp_path,
    config_factory,
):
    """Copied old metadata cannot authorize HOME without the runtime link."""

    config = config_factory(tmp_path)
    legacy = legacy_layout_from_canonical(config.layout)
    config.layout.local_dir.mkdir(parents=True)
    config.layout.metadata_path.write_text(
        json.dumps(
            build_metadata(replace(config, layout=legacy)),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    legacy.codex_home_dir.mkdir()
    sentinel = legacy.codex_home_dir / "protected-context"
    sentinel.write_text("preserve\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="exact historical"):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"
    assert config.layout.local_dir.is_dir()
    assert not os.path.lexists(legacy.local_dir)
    assert not os.path.lexists(config.layout.codex_home_dir)


@pytest.mark.skipif(
    not atomic_exchange_supported(),
    reason="Linux renameat2 exchange is unavailable",
)
def test_home_only_partial_migration_uses_full_legacy_metadata(
    tmp_path,
    config_factory,
):
    """A previously moved runtime can authorize its still-legacy isolated HOME."""

    config = config_factory(tmp_path)
    legacy = legacy_layout_from_canonical(config.layout)
    config.layout.local_dir.mkdir(parents=True)
    legacy.local_dir.symlink_to(DEFAULT_LOCAL_DIR, target_is_directory=True)
    config.layout.metadata_path.write_text(
        json.dumps(
            build_metadata(replace(config, layout=legacy)),
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    legacy.codex_home_dir.mkdir()
    sentinel = legacy.codex_home_dir / "sentinel"
    sentinel.write_text("preserve\n", encoding="utf-8")
    plan = build_default_layout_migration(
        config.layout,
        legacy,
        shared_home=False,
    )
    assert plan is not None
    assert plan.migrate_local is False
    assert plan.migrate_home is True
    config.layout_migration = plan

    apply_default_layout_migration(config)

    assert (
        config.layout.codex_home_dir.joinpath("sentinel").read_text(encoding="utf-8")
        == "preserve\n"
    )
    assert legacy.codex_home_dir.is_symlink()
    assert os.readlink(legacy.codex_home_dir) == DEFAULT_HOME_DIR


def test_config_recovers_exact_preexchange_staged_runtime_link(
    tmp_path,
    config_factory,
):
    """Normal CLI discovery can resume after power loss before exchange."""

    config = config_factory(tmp_path, shared_home=True)
    legacy, _, _ = materialize_legacy_defaults(config, include_home=False)
    config.layout.local_dir.parent.mkdir(parents=True)
    config.layout.local_dir.symlink_to(
        DEFAULT_LOCAL_DIR,
        target_is_directory=True,
    )

    recovered = config_from_args(parse_args(["--update", str(tmp_path)]))

    assert recovered.layout_migration is not None
    assert recovered.layout_migration.local_state == "staged"
    assert recovered.layout_migration.state_layout == legacy


def test_exchange_timeout_before_commit_removes_only_its_staged_link(
    tmp_path,
    monkeypatch,
):
    """A timed-out helper is classified before cleanup or error reporting."""

    legacy = tmp_path / ".codex-local"
    canonical = tmp_path / ".local" / "codex"
    legacy.mkdir()
    sentinel = legacy / "sentinel"
    sentinel.write_text("runtime\n", encoding="utf-8")

    def time_out(_left, _right):
        raise subprocess.TimeoutExpired("renameat2", 15)

    monkeypatch.setattr("codex_wrangler.migration._rename_exchange", time_out)

    with pytest.raises(CodexWranglerError, match="did not commit"):
        _exchange_directory_with_compatibility_link(
            legacy,
            canonical,
            DEFAULT_LOCAL_DIR,
            "runtime",
        )

    assert sentinel.read_text(encoding="utf-8") == "runtime\n"
    assert not os.path.lexists(canonical)


def test_exchange_timeout_after_commit_preserves_committed_state(
    tmp_path,
    monkeypatch,
):
    """Ambiguous timeout never rolls back an exchange already observed committed."""

    legacy = tmp_path / ".codex-local"
    canonical = tmp_path / ".local" / "codex"
    legacy.mkdir()
    sentinel = legacy / "sentinel"
    sentinel.write_text("runtime\n", encoding="utf-8")

    def commit_then_time_out(left, right):
        target = os.readlink(right)
        right.unlink()
        left.rename(right)
        left.symlink_to(target, target_is_directory=True)
        raise subprocess.TimeoutExpired("renameat2", 15)

    monkeypatch.setattr(
        "codex_wrangler.migration._rename_exchange",
        commit_then_time_out,
    )

    with pytest.raises(CodexWranglerError, match="committed"):
        _exchange_directory_with_compatibility_link(
            legacy,
            canonical,
            DEFAULT_LOCAL_DIR,
            "runtime",
        )

    assert legacy.is_symlink()
    assert os.readlink(legacy) == DEFAULT_LOCAL_DIR
    assert canonical.joinpath("sentinel").read_text(encoding="utf-8") == "runtime\n"


def test_external_exchange_probe_is_isolated_from_project_imports(
    tmp_path,
    monkeypatch,
):
    """The system-Python fallback cannot import a hostile project ctypes module."""

    marker = tmp_path / "imported-hostile-ctypes"
    tmp_path.joinpath("ctypes.py").write_text(
        "from pathlib import Path\n"
        "Path({!r}).write_text('imported', encoding='utf-8')\n"
        "raise RuntimeError('hostile project module imported')\n".format(str(marker)),
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)

    helper = _external_exchange_python()

    if helper is None:
        pytest.skip("No external Python provides renameat2")
    assert helper.startswith("/usr/bin/")
    assert not marker.exists()


def test_inspection_reports_structured_pending_layout_migration(
    tmp_path,
    config_factory,
):
    """Machine-readable inspection exposes both old and canonical path states."""

    source = config_factory(tmp_path, shared_home=True)
    materialize_legacy_defaults(source, include_home=False)
    inspected = config_from_args(parse_args(["--inspect", str(tmp_path)]))

    report = gather_inspection_report(inspected)

    assert report["layout_migration"] == {
        "pending": True,
        "local_state": "legacy",
        "home_state": "shared",
        "migrate_local": True,
        "migrate_home": False,
        "legacy_paths": {
            "local_dir": str(tmp_path / ".codex-local"),
            "codex_home_dir": str(tmp_path / ".codex-home"),
        },
        "canonical_paths": {
            "local_dir": str(tmp_path / ".local" / "codex"),
            "codex_home_dir": str(tmp_path / ".local" / "codex-home"),
        },
    }
    assert any(
        "Pending canonical Codex layout migration" in warning
        for warning in report["warnings"]
    )


def test_home_migration_rejects_linked_root_metadata(
    tmp_path,
    config_factory,
):
    """External metadata cannot authorize moving protected old-name HOME state."""

    config = config_factory(tmp_path)
    legacy = legacy_layout_from_canonical(config.layout)
    config.layout.local_dir.mkdir(parents=True)
    outside_metadata = tmp_path / "outside-metadata.json"
    outside_metadata.write_text(
        json.dumps(build_metadata(replace(config, layout=legacy))),
        encoding="utf-8",
    )
    config.layout.metadata_path.symlink_to(outside_metadata)
    legacy.codex_home_dir.mkdir()

    with pytest.raises(CodexWranglerError, match="exact historical"):
        build_default_layout_migration(
            config.layout,
            legacy,
            shared_home=False,
        )

    assert legacy.codex_home_dir.is_dir()
    assert config.layout.metadata_path.is_symlink()
