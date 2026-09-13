"""Exercise receipt-less recovery without borrowing live operator state."""

from dataclasses import replace
import json
import os
import shutil

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.initialization import write_initial_install
from codex_wrangler.operations import (
    install_like_operation,
    require_safe_install_adoption,
    require_uninstall_ownership,
)
from codex_wrangler.rendering import build_local_package_json, build_metadata
from codex_wrangler.repair import build_repair_plan, prove_managed_install
from codex_wrangler.slots import MaintenanceLock


@pytest.fixture
def legacy_config(tmp_path, config_factory):
    """Materialize the old first-install namespace with no completion authority."""

    config = config_factory(tmp_path)
    runtime = config.layout.local_dir
    runtime.mkdir(parents=True)
    for name in (".npm-cache", ".maintenance"):
        (runtime / name).mkdir()
    config.layout.codex_home_dir.mkdir(parents=True)
    add_candidate(config)
    return config


def add_candidate(config, index=0, version="0.154.0"):
    """Create one deterministic generated manifest, without installed payload."""

    path = config.layout.local_dir / (".candidate-" + format(index, "032x"))
    path.mkdir()
    manifest = path / "package.json"
    manifest.write_text(build_local_package_json(version), encoding="utf-8")
    return manifest


def manifest_path(config):
    """Return the fixture's original candidate manifest."""

    return config.layout.local_dir / (".candidate-" + "0" * 32) / "package.json"


def test_legacy_repair_requires_home_and_preserves_version(legacy_config):
    """Manifest intent fills version evidence, not missing HOME authority."""

    root = str(legacy_config.project_root)
    with pytest.raises(CodexWranglerError, match="exactly one explicit"):
        config_from_args(parse_args(["--repair", root]))
    repaired = config_from_args(parse_args(["--repair", root, "--isolated-home"]))
    assert repaired.codex_version == "0.154.0"
    assert repaired.shared_home is False
    assert repaired.reasonable_permissions_enabled is False
    assert "legacy candidate" in repaired.version_source
    with pytest.raises(CodexWranglerError, match="without exact managed ownership"):
        require_safe_install_adoption(legacy_config)


def test_matching_candidates_are_recoverable(legacy_config):
    """Multiple old attempts are acceptable only when exact intent agrees."""

    add_candidate(legacy_config, 1)
    plan = build_repair_plan(legacy_config.layout)
    assert plan.codex_version == "0.154.0"
    assert len(plan.ownership_evidence) == 2


def test_conflicting_candidates_fail_before_home_prompt(legacy_config):
    """Never select the newest-looking candidate to resolve version conflicts."""

    add_candidate(legacy_config, 1, "0.153.0")
    with pytest.raises(CodexWranglerError, match="Conflicting exact Codex versions"):
        config_from_args(parse_args(["--repair", str(legacy_config.project_root)]))


@pytest.mark.parametrize(
    "kind",
    ["scripts", "dependency", "extra", "private", "selector", "malformed", "large"],
)
def test_non_generated_manifests_are_rejected(legacy_config, kind):
    """An ordinary npm project cannot masquerade as minimal wrangler intent."""

    path = manifest_path(legacy_config)
    payload = json.loads(path.read_text())
    if kind == "scripts":
        payload["scripts"] = {"install": "do-not-execute"}
    elif kind == "dependency":
        payload["devDependencies"]["unrelated"] = "1.0.0"
    elif kind == "extra":
        payload["extra"] = True
    elif kind == "private":
        payload["private"] = 1
    elif kind == "selector":
        payload["devDependencies"]["@openai/codex"] = "latest"
    path.write_text(json.dumps(payload), encoding="utf-8")
    if kind == "malformed":
        path.write_text("{", encoding="utf-8")
    elif kind == "large":
        path.write_text(" " * 4097, encoding="utf-8")
    with pytest.raises(
        CodexWranglerError, match="Legacy first-install recovery refused"
    ):
        build_repair_plan(legacy_config.layout)


@pytest.mark.parametrize(
    "kind", ["missing", "symlink", "hardlink", "fifo", "directory"]
)
def test_unsafe_manifest_files_are_rejected(legacy_config, kind):
    """Bounded recovery refuses link-based evidence and nonregular files."""

    path = manifest_path(legacy_config)
    original = path.read_bytes()
    path.unlink()
    target = legacy_config.project_root / "untouched-manifest"
    target.write_bytes(original)
    if kind == "symlink":
        path.symlink_to(target)
    elif kind == "hardlink":
        os.link(target, path)
    elif kind == "fifo":
        os.mkfifo(path)
    elif kind == "directory":
        path.mkdir()
    with pytest.raises(
        CodexWranglerError, match="Legacy first-install recovery refused"
    ):
        build_repair_plan(legacy_config.layout)
    assert target.read_bytes() == original


@pytest.mark.parametrize("kind", ["file", "directory", "cache-link", "candidate-link"])
def test_foreign_or_linked_runtime_entries_are_rejected(legacy_config, kind):
    """A valid candidate cannot authorize foreign siblings or linked namespaces."""

    runtime = legacy_config.layout.local_dir
    if kind == "file":
        (runtime / "foreign").write_text("preserve", encoding="utf-8")
    elif kind == "directory":
        (runtime / "foreign").mkdir()
    elif kind == "cache-link":
        (runtime / ".npm-cache").rmdir()
        (runtime / ".npm-cache").symlink_to(
            legacy_config.project_root, target_is_directory=True
        )
    else:
        (runtime / (".candidate-" + "1" * 32)).symlink_to(
            manifest_path(legacy_config).parent, target_is_directory=True
        )
    with pytest.raises(CodexWranglerError, match="foreign or linked"):
        build_repair_plan(legacy_config.layout)


def test_candidate_discovery_is_bounded(legacy_config):
    """An oversized namespace must stop instead of scanning arbitrarily far."""

    for index in range(1, 64):
        add_candidate(legacy_config, index)
    with pytest.raises(CodexWranglerError, match="more than 64"):
        build_repair_plan(legacy_config.layout)


def test_legacy_intent_does_not_authorize_migration_or_uninstall(legacy_config):
    """The shared proof helper must retain its stricter non-repair default."""

    with pytest.raises(CodexWranglerError, match="Cannot prove"):
        prove_managed_install(legacy_config.layout)
    with pytest.raises(CodexWranglerError):
        require_uninstall_ownership(legacy_config)


def test_duplicate_manifest_fields_are_rejected(legacy_config):
    """A conflicting JSON field cannot be hidden behind last-value semantics."""

    path = manifest_path(legacy_config)
    content = path.read_text().replace(
        '"private": true', '"private": false, "private": true'
    )
    path.write_text(content, encoding="utf-8")
    with pytest.raises(CodexWranglerError, match="duplicate manifest fields"):
        build_repair_plan(legacy_config.layout)


def test_completed_metadata_takes_precedence(legacy_config):
    """Stale candidate debris cannot roll a completed installation backward."""

    completed = replace(
        legacy_config, codex_selector="0.155.0", codex_version="0.155.0"
    )
    path = completed.layout.metadata_path
    path.write_text(json.dumps(build_metadata(completed)), encoding="utf-8")
    manifest_path(legacy_config).write_text("invalid old debris", encoding="utf-8")
    plan = build_repair_plan(legacy_config.layout)
    assert plan.codex_version == completed.codex_version
    assert plan.ownership_evidence == (str(path),)


def test_invalid_receipt_is_not_bypassed(legacy_config):
    """Legacy fallback cannot erase a newer but malformed authority claim."""

    (legacy_config.project_root / ".codex-wrangler-initial-install.json").write_text(
        "{}", encoding="utf-8"
    )
    with pytest.raises(
        CodexWranglerError, match="Invalid or stale first-install receipt"
    ):
        build_repair_plan(legacy_config.layout)


def test_legacy_repair_dry_run_preserves_all_evidence(legacy_config):
    """Planning recovery cannot write a root projection or alter candidate intent."""

    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("dry-run requires installed node/npm commands")
    config = config_from_args(
        parse_args(
            [
                "--repair",
                str(legacy_config.project_root),
                "--isolated-home",
                "--dry-run",
            ]
        )
    )
    before = manifest_path(config).read_bytes()
    assert install_like_operation(config) == 0
    assert manifest_path(config).read_bytes() == before
    assert not config.layout.metadata_path.exists()
    assert not config.layout.gitignore_path.exists()


@pytest.mark.parametrize("mutation", ["foreign", "version"])
def test_legacy_evidence_is_rechecked_under_lock(legacy_config, monkeypatch, mutation):
    """An out-of-band namespace change at lock acquisition must stop all writes."""

    fcntl = pytest.importorskip("fcntl")
    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("repair prerequisite checks require installed node/npm commands")
    config = config_from_args(
        parse_args(["--repair", str(legacy_config.project_root), "--isolated-home"])
    )
    real_flock = fcntl.flock

    def change_at_lock(descriptor, flags):
        """Inject the unsafe external timing boundary, keeping the real lock."""

        real_flock(descriptor, flags)
        if flags & fcntl.LOCK_EX:
            if mutation == "foreign":
                (config.layout.local_dir / "foreign").write_bytes(b"preserve\n")
            else:
                manifest_path(config).write_text(
                    build_local_package_json("0.153.0"), encoding="utf-8"
                )

    monkeypatch.setattr(fcntl, "flock", change_at_lock)
    with pytest.raises(
        CodexWranglerError, match="foreign or linked|locked target evidence"
    ):
        install_like_operation(config)
    assert not config.layout.metadata_path.exists()
    assert not config.layout.gitignore_path.exists()


def test_missing_home_is_not_recreated(legacy_config):
    """Explicit isolated recovery still refuses an absent protected HOME."""

    if shutil.which("node") is None or shutil.which("npm") is None:
        pytest.skip("repair prerequisite checks require installed node/npm commands")
    config = config_from_args(
        parse_args(["--repair", str(legacy_config.project_root), "--isolated-home"])
    )
    config.layout.codex_home_dir.rmdir()
    with pytest.raises(CodexWranglerError, match="will not recreate missing"):
        install_like_operation(config)
    assert not config.layout.codex_home_dir.exists()
    assert not config.layout.gitignore_path.exists()


def test_legacy_repair_receipt_survives_pre_manifest_crash(legacy_config):
    """A second interrupted initialization must not recreate the ownership gap."""

    config = config_from_args(
        parse_args(["--repair", str(legacy_config.project_root), "--isolated-home"])
    )
    assert build_repair_plan(config.layout).legacy_first_install
    with MaintenanceLock(config.layout):
        write_initial_install(config, allow_legacy_repair=True)
    (config.layout.local_dir / (".candidate-" + "1" * 32)).mkdir()

    retry = config_from_args(parse_args([str(config.project_root)]))
    require_safe_install_adoption(retry)
    assert retry.codex_version == "0.154.0"
    assert retry.initial_install
    repaired = config_from_args(parse_args(["--repair", str(config.project_root)]))
    assert repaired.shared_home is False
    assert not build_repair_plan(config.layout).legacy_first_install


@pytest.mark.parametrize("mutation", ["foreign", "version", "operation"])
def test_legacy_receipt_transition_revalidates_authority(legacy_config, mutation):
    """Receipt publication cannot mint authority from a stale repair plan."""

    config = config_from_args(
        parse_args(["--repair", str(legacy_config.project_root), "--isolated-home"])
    )
    if mutation == "foreign":
        (config.layout.local_dir / "foreign").write_bytes(b"preserve\n")
    elif mutation == "version":
        manifest_path(config).write_text(
            build_local_package_json("0.153.0"), encoding="utf-8"
        )
    else:
        config = replace(config, operation="install")
    with MaintenanceLock(config.layout), pytest.raises(CodexWranglerError):
        write_initial_install(config, allow_legacy_repair=True)
    assert not (config.project_root / ".codex-wrangler-initial-install.json").exists()
