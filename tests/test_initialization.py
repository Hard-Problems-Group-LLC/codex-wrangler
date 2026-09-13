"""Verify first-install proof cannot authorize foreign or replaced state."""

from dataclasses import replace
import json
import os
from pathlib import Path

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.constants import INITIAL_INSTALL_FILENAME
from codex_wrangler.initialization import (
    initial_install_path,
    read_initial_install,
    write_initial_install,
)
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    ensure_isolated_home_ready,
    install_like_operation,
    require_safe_install_adoption,
)
from codex_wrangler.slots import MaintenanceLock


@pytest.fixture
def initial_config(tmp_path, config_factory):
    """Build exact first-install inputs without marking a completed install."""

    config = config_factory(
        tmp_path,
        codex_selector="0.30.0",
        codex_version="0.30.0",
        codex_channel="stable",
        reasonable_permissions_enabled=True,
    )
    ensure_isolated_home_ready(config)
    return config


def record_intent(config):
    """Exercise the real receipt publisher with its required maintenance lock."""

    with MaintenanceLock(config.layout):
        write_initial_install(config)


def test_receipt_preserves_context_and_supports_exact_repair(initial_config):
    """An unfinished runtime records intent, not an implicitly usable install."""

    config = initial_config
    record_intent(config)
    sentinel = config.layout.codex_home_dir / "preserved-context"
    sentinel.write_bytes(b"context fixture\n")
    (config.layout.local_dir / ".candidate-old").mkdir()

    retry = config_from_args(
        parse_args(
            [
                str(config.project_root),
                "--set-reasonable-permissions",
            ]
        )
    )
    repair = config_from_args(parse_args(["--repair", str(config.project_root)]))

    assert retry.initial_install and repair.initial_install
    assert not retry.reconfigure_only
    assert retry.codex_version == repair.codex_version == "0.30.0"
    assert repair.shared_home is False
    assert repair.reasonable_permissions_enabled is True
    require_safe_install_adoption(retry)
    assert sentinel.read_bytes() == b"context fixture\n"


@pytest.mark.parametrize("directory", ["runtime", "home"])
def test_receipt_rejects_replaced_directory(initial_config, directory):
    """The same pathname is not proof for a new directory occupying that name."""

    config = initial_config
    record_intent(config)
    path = (
        config.layout.local_dir
        if directory == "runtime"
        else config.layout.codex_home_dir
    )
    path.rename(path.with_name(path.name + "-preserved"))
    path.mkdir()
    sentinel = path / "foreign-data"
    sentinel.write_bytes(b"leave alone\n")

    with pytest.raises(CodexWranglerError, match="identity changed"):
        read_initial_install(config.layout)

    assert sentinel.read_bytes() == b"leave alone\n"


def test_receipt_rejects_missing_home(initial_config):
    """Intent must never authorize recreating lost isolated context."""

    config = initial_config
    record_intent(config)
    config.layout.codex_home_dir.rmdir()

    with pytest.raises(CodexWranglerError, match="Invalid or stale"):
        config_from_args(parse_args(["--repair", str(config.project_root)]))
    assert not config.layout.codex_home_dir.exists()


@pytest.mark.parametrize(
    "kind", ["symlink", "dangling", "fifo", "hardlink", "json", "oversize"]
)
def test_receipt_rejects_unsafe_input(initial_config, kind):
    """Malformed or redirected sidecars fail promptly without modifying data."""

    config = initial_config
    record_intent(config)
    receipt = initial_install_path(config.layout)
    saved = receipt.with_name("preserved-receipt")
    receipt.rename(saved)
    if kind == "symlink":
        receipt.symlink_to(saved)
    elif kind == "dangling":
        receipt.symlink_to(saved.with_name("absent"))
    elif kind == "fifo":
        if not hasattr(os, "mkfifo"):
            pytest.skip("FIFO check requires POSIX")
        os.mkfifo(receipt)
    elif kind == "hardlink":
        os.link(saved, receipt)
    elif kind == "json":
        receipt.write_text("{", encoding="utf-8")
    else:
        receipt.write_bytes(b" " * 16385)
    original = saved.read_bytes()

    with pytest.raises(CodexWranglerError, match="Invalid or stale"):
        read_initial_install(config.layout)

    assert saved.read_bytes() == original


def test_receipt_cannot_be_copied_to_another_project(initial_config, config_factory):
    """A copied sidecar is not cross-project installation authority."""

    record_intent(initial_config)
    target = initial_config.project_root / "other-fixture"
    target.mkdir()
    config = config_factory(target)
    initial_install_path(config.layout).write_bytes(
        initial_install_path(initial_config.layout).read_bytes()
    )

    with pytest.raises(CodexWranglerError, match="do not bind this project"):
        read_initial_install(config.layout)
    assert not config.layout.local_dir.exists()


def test_receipt_write_failure_keeps_fresh_install_retryable(
    initial_config, monkeypatch
):
    """A failed receipt publication cannot leave unowned nonempty runtime data."""

    config = initial_config
    real_replace = os.replace

    def fail_receipt_replace(source, target):
        """Inject only the atomic-publication failure, not owned application logic."""
        if Path(target) == initial_install_path(config.layout):
            raise OSError("fixture receipt publication failure")
        return real_replace(source, target)

    with monkeypatch.context() as patch:
        patch.setattr(os, "replace", fail_receipt_replace)
        with pytest.raises(CodexWranglerError, match="receipt publication failure"):
            record_intent(config)

    assert list(config.layout.local_dir.iterdir()) == []
    require_safe_install_adoption(config)
    record_intent(config)
    assert read_initial_install(config.layout) is not None


def test_killed_receipt_publication_debris_does_not_poison_runtime(initial_config):
    """An unrenamed sidecar temp from power loss is outside runtime adoption."""

    config = initial_config
    temp = config.project_root / ("." + INITIAL_INSTALL_FILENAME + ".crashed.tmp")
    temp.write_text("incomplete fixture", encoding="utf-8")

    require_safe_install_adoption(config)
    record_intent(config)

    assert read_initial_install(config.layout) is not None
    assert temp.read_text(encoding="utf-8") == "incomplete fixture"


@pytest.mark.parametrize(
    "overrides",
    [
        {"shared_home": True},
        {"reasonable_permissions_enabled": False},
        {"codex_version": "0.31.0"},
    ],
)
def test_pending_install_refuses_selection_changes(initial_config, overrides):
    """Unfinished intent is not permission to change version or context scope."""

    record_intent(initial_config)
    with pytest.raises(CodexWranglerError, match="Unfinished first install records"):
        require_safe_install_adoption(replace(initial_config, **overrides))


def test_lookup_cannot_silently_adopt_new_foreign_data(initial_config, monkeypatch):
    """Ownership must be rechecked under lock after an external version lookup."""

    config = initial_config
    sentinel = config.layout.local_dir / "arrived-during-lookup"

    def resolve_after_foreign_write(observed, _npm, **_kwargs):
        """Simulate a concurrent writer at the external registry-lookup boundary."""
        sentinel.parent.mkdir(parents=True)
        sentinel.write_bytes(b"foreign fixture\n")
        return observed

    monkeypatch.setattr(
        "codex_wrangler.operations.detect_npm_binaries", lambda: ("npm", "npx")
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.ensure_command_exists", lambda _name: None
    )
    monkeypatch.setattr(
        "codex_wrangler.operations.resolve_install_version", resolve_after_foreign_write
    )

    with pytest.raises(CodexWranglerError, match="without exact managed ownership"):
        install_like_operation(config)

    assert sentinel.read_bytes() == b"foreign fixture\n"
    assert not initial_install_path(config.layout).exists()


def test_receipt_boolean_identity_is_not_integer_proof(initial_config):
    """Python bool/int equality must not accept malformed identity fields."""

    record_intent(initial_config)
    path = initial_install_path(initial_config.layout)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["identities"]["project"] = [True, False]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CodexWranglerError, match="identity changed"):
        read_initial_install(initial_config.layout)


@pytest.mark.parametrize(
    "flag", ["--local-dir", "--codex-home-dir", "--launcher", "--readme-local"]
)
def test_initial_receipt_path_is_reserved(tmp_path, flag):
    """Configurable managed outputs cannot collide with the recovery receipt."""

    with pytest.raises(CodexWranglerError, match="overlap"):
        config_from_args(parse_args([str(tmp_path), flag, INITIAL_INSTALL_FILENAME]))


def test_receipt_version_conflict_cannot_be_silently_upgraded(initial_config):
    """New root package evidence cannot override the pending exact version."""

    from codex_wrangler.rendering import build_local_package_json

    record_intent(initial_config)
    initial_config.layout.local_package_json_path.write_text(
        build_local_package_json("0.31.0"), encoding="utf-8"
    )
    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        config_from_args(parse_args(["--repair", str(initial_config.project_root)]))


def test_repair_discovers_complete_custom_layout(tmp_path, config_factory):
    """A receipt can supply all custom paths without new repair overrides."""

    config = config_factory(
        tmp_path,
        local_dir_raw="tools/runtime",
        codex_home_raw="state/home",
        launcher_raw="tools/bin/codex",
        readme_raw="docs/LOCAL.md",
        codex_version="0.30.0",
    )
    ensure_isolated_home_ready(config)
    record_intent(config)

    repaired = config_from_args(parse_args(["--repair", str(tmp_path)]))

    assert repaired.layout == config.layout
    assert repaired.codex_version == "0.30.0"
    assert repaired.initial_install


def test_receipt_visible_after_failed_directory_flush_is_retryable(
    initial_config, monkeypatch
):
    """A visible atomically committed receipt remains usable after fsync fails."""

    from codex_wrangler import filesystem

    config = initial_config
    real_flush = filesystem.fsync_directory

    def fail_receipt_flush(path):
        """Fail only the final sidecar-parent flush, after atomic replacement."""
        if initial_install_path(config.layout).exists():
            raise OSError("fixture receipt directory flush failure")
        return real_flush(path)

    with monkeypatch.context() as patch:
        patch.setattr(filesystem, "fsync_directory", fail_receipt_flush)
        with pytest.raises(CodexWranglerError, match="uncertain crash durability"):
            record_intent(config)

    require_safe_install_adoption(config)
    record_intent(config)
    assert read_initial_install(config.layout) is not None


def test_projection_dry_run_does_not_remove_receipt(initial_config):
    """A dry-run support refresh cannot retire unfinished recovery evidence."""

    from codex_wrangler.operations import publish_projection_state

    record_intent(initial_config)
    path = initial_install_path(initial_config.layout)
    before = path.read_bytes()

    publish_projection_state(replace(initial_config, dry_run=True))

    assert path.read_bytes() == before
    assert not initial_config.layout.metadata_path.exists()


def test_completed_authority_overrides_pending_intent(initial_config):
    """A leftover receipt cannot roll completed metadata back to old defaults."""

    from codex_wrangler.filesystem import write_metadata
    from codex_wrangler.layout import read_existing_state
    from codex_wrangler.rendering import build_metadata

    record_intent(initial_config)
    completed = replace(
        initial_config,
        codex_version="0.31.0",
        shared_home=True,
        reasonable_permissions_enabled=False,
    )
    write_metadata(
        completed.layout.metadata_path, build_metadata(completed), dry_run=False
    )

    state = read_existing_state(completed.layout)

    assert state.pinned_codex_version == "0.31.0"
    assert state.shared_home is True
    assert state.reasonable_permissions_enabled is False
    assert state.initial_install_receipt is None


def test_owned_uninstall_retires_receipt_and_keeps_debris_ignored(initial_config):
    """Uninstall cannot leave live intent authorizing a later replacement root."""

    from codex_wrangler.operations import (
        uninstall_operation,
        write_managed_supporting_files,
    )

    config = initial_config
    record_intent(config)
    write_managed_supporting_files(config)
    temporary = config.project_root / ("." + INITIAL_INSTALL_FILENAME + ".old.tmp")
    temporary.write_bytes(b"old partial fixture\n")

    assert uninstall_operation(replace(config, operation="uninstall")) == 0

    assert not initial_install_path(config.layout).exists()
    assert not config.layout.local_dir.exists()
    assert temporary.read_bytes() == b"old partial fixture\n"
    assert INITIAL_INSTALL_FILENAME in config.layout.gitignore_path.read_text(
        encoding="utf-8"
    )
