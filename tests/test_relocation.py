"""Exercise explicit and automatic origin recovery using disposable directories."""

from dataclasses import replace
import json
import os
import shutil

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.initialization import write_initial_install
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import gather_inspection_report, install_like_operation
from codex_wrangler.relocation import relocation_inspection
from codex_wrangler.rendering import build_local_package_json, build_metadata
from codex_wrangler.slots import (
    config_for_slot,
    promote_active_slot,
    write_slot_metadata,
)


def materialize_slot(config, slot, version):
    """Create concordant real slot evidence without executing a package."""

    source = replace(config, codex_selector=version, codex_version=version)
    prefix = config_for_slot(source, slot).layout.local_dir
    prefix.mkdir(parents=True)
    (prefix / "package.json").write_text(build_local_package_json(version))
    (prefix / "package-lock.json").write_text(
        json.dumps({"packages": {"node_modules/@openai/codex": {"version": version}}})
    )
    installed = prefix / "node_modules/@openai/codex/package.json"
    installed.parent.mkdir(parents=True)
    installed.write_text(json.dumps({"version": version}))
    write_slot_metadata(source, slot)


@pytest.fixture
def moved_install(tmp_path, config_factory):
    """Rename a completed two-slot install, retaining the original bindings."""

    original = tmp_path / "original checkout"
    original.mkdir()
    source = config_factory(
        original,
        codex_selector="0.154.0",
        codex_channel="stable",
        codex_version="0.154.0",
        reasonable_permissions_enabled=True,
    )
    source.layout.codex_home_dir.mkdir(parents=True)
    materialize_slot(source, "a", "0.153.0")
    materialize_slot(source, "b", "0.154.0")
    promote_active_slot(source.layout, "b")
    source.layout.metadata_path.write_text(json.dumps(build_metadata(source)))
    source.layout.local_package_json_path.write_text(
        build_local_package_json("0.154.0")
    )
    retained = tmp_path / "retained checkout"
    original.rename(retained)
    return original, retained


@pytest.mark.parametrize("mode", ["explicit", "automatic"])
def test_moved_active_record_preserves_all_selection(moved_install, mode):
    """Use the selected slot rather than confusing its rollback with a conflict."""

    original, retained = moved_install
    modifier = (
        ["--repair-auto"]
        if mode == "automatic"
        else ["--repair-from-root", str(original)]
    )
    config = config_from_args(parse_args(["--repair", str(retained), *modifier]))
    assert config.codex_version == "0.154.0"
    assert config.shared_home is False
    assert config.reasonable_permissions_enabled is True
    assert config.active_slot == "b"
    assert config.layout.repair_from_root == original
    assert "active slot completion record" in config.version_source


@pytest.mark.parametrize(
    "arguments",
    [
        ["--repair-auto"],
        ["--inspect", "--repair-auto"],
        ["--repair-from-root", "/old"],
        ["--uninstall", "--repair-from-root", "/old"],
        ["--repair", "/current", "--repair-from-root", "relative"],
        ["--repair", "/current", "--repair-auto", "--repair-from-root", "/old"],
    ],
)
def test_relocation_modifiers_are_explicitly_scoped(arguments):
    """Never introduce automatic adoption into an ordinary maintenance command."""

    with pytest.raises(SystemExit) as failure:
        parse_args(arguments)
    assert failure.value.code == 2


def test_previous_root_does_not_require_a_surviving_directory(moved_install):
    """A retained image can be recovered after its old location disappears."""

    original, retained = moved_install
    assert not original.exists()
    config = config_from_args(
        parse_args(
            ["--repair", str(retained), "--repair-from-root", str(original) + "/"]
        )
    )
    assert config.project_root == retained


def test_previous_root_is_not_resolved_or_used_as_a_source(moved_install, tmp_path):
    """A reused old pathname cannot redirect current-root recovery."""

    original, retained = moved_install
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    sentinel = unrelated / "preserve"
    sentinel.write_bytes(b"foreign fixture\n")
    original.symlink_to(unrelated, target_is_directory=True)
    config = config_from_args(
        parse_args(["--repair", str(retained), "--repair-from-root", str(original)])
    )
    assert config.layout.repair_from_root == original
    assert config.project_root == retained
    assert sentinel.read_bytes() == b"foreign fixture\n"


@pytest.mark.parametrize("origin", ["wrong", "current"])
def test_explicit_origin_requires_matching_records(moved_install, origin):
    """An operator assertion cannot mint provenance over unrelated records."""

    original, retained = moved_install
    value = original.parent / "wrong" if origin == "wrong" else retained
    with pytest.raises(CodexWranglerError, match="No validated|different absolute"):
        config_from_args(
            parse_args(["--repair", str(retained), "--repair-from-root", str(value)])
        )


def test_plain_repair_retains_strict_binding(moved_install):
    """Moving a tree does not silently opt ordinary repair into relocation."""

    _, retained = moved_install
    with pytest.raises(CodexWranglerError, match="HOME.*surviving authority"):
        config_from_args(parse_args(["--repair", str(retained)]))


def test_automatic_repair_rejects_conflicting_origins(moved_install):
    """Do not guess which of two otherwise valid source installations moved."""

    original, retained = moved_install
    record = retained / ".local/codex/slots/a/.codex-wrangler-slot.json"
    payload = json.loads(record.read_text())
    payload["project_root"] = str(original.parent / "another source")
    record.write_text(json.dumps(payload))
    with pytest.raises(CodexWranglerError, match="conflicting recorded project roots"):
        config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    explicit = config_from_args(
        parse_args(["--repair", str(retained), "--repair-from-root", str(original)])
    )
    assert explicit.active_slot == "b"


def test_automatic_repair_accepts_interrupted_current_and_old_records(moved_install):
    """A committed current slot can coexist with its old-root projections."""

    _, retained = moved_install
    record = retained / ".local/codex/slots/b/.codex-wrangler-slot.json"
    payload = json.loads(record.read_text())
    payload["project_root"] = str(retained)
    record.write_text(json.dumps(payload))
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    assert config.codex_version == "0.154.0"
    assert config.active_slot == "b"


def test_relocation_inspection_explains_unknown_source(moved_install):
    """Inspection identifies a valid source and emits a quoted recovery command."""

    original, retained = moved_install
    config = config_from_args(parse_args(["--inspect", str(retained)]))
    report = gather_inspection_report(config)["repair"]
    assert report["recorded_project_roots"] == [str(original)]
    assert report["previous_project_root"] == str(original)
    assert report["automatic_repair_possible"] is True
    assert report["relocation_required"] is True
    assert "'" in report["suggested_command"]
    assert "--repair-auto" in report["suggested_command"]
    assert len(report["validated_records"]) == 3


def test_inspection_reports_conflict_without_choosing(moved_install):
    """A source conflict remains visible without hiding valid records."""

    original, retained = moved_install
    metadata = retained / ".local/codex/.codex-wrangler.json"
    payload = json.loads(metadata.read_text())
    payload["project_root"] = str(original.parent / "different")
    metadata.write_text(json.dumps(payload))
    report = relocation_inspection(
        config_from_args(parse_args(["--inspect", str(retained)])).layout
    )
    assert report["automatic_repair_possible"] is False
    assert "conflicting recorded" in report["blocker"]
    assert report["suggested_command"] is None


@pytest.mark.parametrize("kind", ["duplicate", "large", "symlink", "hardlink", "fifo"])
def test_unsafe_origin_evidence_is_not_used(tmp_path, config_factory, kind):
    """A malformed single record cannot supply an automatic origin."""

    source = tmp_path / "source"
    source.mkdir()
    target = tmp_path / "target"
    target.mkdir()
    config = config_factory(source, codex_selector="0.154.0", codex_version="0.154.0")
    data = json.dumps(build_metadata(config))
    runtime = target / ".local/codex"
    runtime.mkdir(parents=True)
    path = runtime / ".codex-wrangler.json"
    if kind == "duplicate":
        path.write_text(
            data.replace('"project_root":', '"project_root": "/wrong", "project_root":')
        )
    elif kind == "large":
        path.write_text(data + " " * 16385)
    else:
        foreign = source / "record"
        foreign.write_text(data)
        if kind == "symlink":
            path.symlink_to(foreign)
        elif kind == "hardlink":
            os.link(foreign, path)
        else:
            os.mkfifo(path)
    with pytest.raises(CodexWranglerError):
        config_from_args(parse_args(["--repair", str(target), "--repair-auto"]))
    assert not (target / ".gitignore").exists()


@pytest.fixture
def moved_receipt(tmp_path, config_factory):
    """Rename an unfinished custom install while retaining identity proof."""

    original = tmp_path / "unfinished"
    original.mkdir()
    config = config_factory(
        original,
        codex_selector="0.154.0",
        codex_version="0.154.0",
        local_dir_raw="runtime/codex",
        codex_home_raw="context/home",
        launcher_raw="tools/codex",
        readme_raw="START.md",
    )
    config.layout.local_dir.mkdir(parents=True)
    config.layout.codex_home_dir.mkdir(parents=True)
    write_initial_install(config)
    retained = tmp_path / "retained unfinished"
    original.rename(retained)
    return original, retained


def test_moved_receipt_keeps_identity_checks_and_recovers_custom_paths(moved_receipt):
    """A same-directory rename supplies the recorded complete custom layout."""

    original, retained = moved_receipt
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    assert config.layout.repair_from_root == original
    assert config.layout.local_dir == retained / "runtime/codex"
    assert config.layout.codex_home_dir == retained / "context/home"
    assert config.layout.launcher_path == retained / "tools/codex"
    assert config.initial_install is True
    write_initial_install(config)
    payload = json.loads(
        (retained / ".codex-wrangler-initial-install.json").read_text()
    )
    assert payload["project_root"] == str(retained)
    retry = config_from_args(parse_args(["--repair", str(retained)]))
    assert retry.codex_version == "0.154.0"
    assert retry.layout.codex_home_dir == config.layout.codex_home_dir


def test_copied_receipt_cannot_rebind_directory_identities(moved_receipt, tmp_path):
    """Explicit origin selection never relaxes identity-bound unfinished intent."""

    original, retained = moved_receipt
    copied = tmp_path / "copied"
    shutil.copytree(retained, copied)
    with pytest.raises(CodexWranglerError, match="No validated relocation"):
        config_from_args(
            parse_args(["--repair", str(copied), "--repair-from-root", str(original)])
        )
    config = config_from_args(parse_args(["--inspect", str(copied)]))
    assert (
        gather_inspection_report(config)["repair"]["automatic_repair_possible"] is False
    )


def test_inspection_survives_a_moved_receipt(moved_receipt):
    """Read-only diagnosis must not fail at the initial stale-root check."""

    original, retained = moved_receipt
    config = config_from_args(parse_args(["--inspect", str(retained)]))
    report = gather_inspection_report(config)["repair"]
    assert report["previous_project_root"] == str(original)
    assert report["automatic_repair_possible"] is True


def test_automatic_repair_does_not_recreate_missing_home(moved_install):
    """Automatic origin discovery does not imply automatic history replacement."""

    _, retained = moved_install
    home = retained / ".local/codex-home"
    home.rmdir()
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    with pytest.raises(CodexWranglerError, match="will not recreate missing"):
        install_like_operation(config)
    assert not home.exists()
    assert not (retained / ".gitignore").exists()


def test_auto_mode_is_useful_on_an_unmoved_install(moved_install):
    """The same rescue command works after the operator already restored paths."""

    original, retained = moved_install
    retained.rename(original)
    config = config_from_args(parse_args(["--repair", str(original), "--repair-auto"]))
    assert config.layout.repair_from_root is None
    assert config.codex_version == "0.154.0"


def test_changed_origin_is_rejected_before_writes(moved_install):
    """Planning does not authorize an origin that changes before execution."""

    original, retained = moved_install
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    record = retained / ".local/codex/.codex-wrangler.json"
    payload = json.loads(record.read_text())
    payload["project_root"] = str(original.parent / "changed")
    record.write_text(json.dumps(payload))
    with pytest.raises(CodexWranglerError, match="conflicting recorded project roots"):
        install_like_operation(config)
    assert not (retained / ".gitignore").exists()


def test_relocation_authority_cannot_leak_into_install(moved_install):
    """Direct library callers receive the same explicit-operation boundary."""

    _, retained = moved_install
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    with pytest.raises(CodexWranglerError, match="only for explicit repair"):
        install_like_operation(replace(config, operation="install"))
