"""Evidence gathering for context-preserving managed install repair."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import stat
from typing import Any, Dict, List, Optional, Tuple

from .layout import (
    infer_installed_version_from_lockfile,
    metadata_looks_managed,
    read_json_file,
)
from .models import CodexWranglerError, Layout
from .initialization import initial_install_path, read_initial_install
from .releases import is_exact_version
from .rendering import local_package_json_looks_managed
from .slots import (
    layout_for_slot,
    observe_repair_runtime,
    read_slot_metadata,
    regular_contained_file,
    validate_managed_root,
)


@dataclass(frozen=True)
class RepairPlan:
    """Validated ownership and exact-version evidence for one repair."""

    codex_version: str
    ownership_evidence: Tuple[str, ...]
    version_evidence: Tuple[str, ...]
    legacy_first_install: bool = False

    @property
    def version_source(self) -> str:
        """Return a concise metadata-safe description of version evidence."""

        return "repair evidence: {}".format(", ".join(self.version_evidence))


def read_json_object_if_valid(path: Path) -> Optional[Dict[str, Any]]:
    """Read a JSON object, returning ``None`` for absent or damaged input."""

    try:
        return read_json_file(path)
    except CodexWranglerError:
        return None


def package_manifest_looks_managed(path: Path) -> bool:
    """Return whether one local package manifest has the managed shape."""

    if path.is_symlink() or not path.is_file():
        return False
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return False
    return local_package_json_looks_managed(content)


def unique_manifest_object(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """Reject duplicate JSON fields instead of resolving ambiguous old intent."""

    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate manifest fields are not accepted")
        result[key] = value
    return result


def legacy_candidate_version(layout: Layout, candidate: Path) -> str:
    """Read only narrowly generated intent, never the old executable payload."""

    manifest = candidate / "package.json"
    try:
        if not regular_contained_file(layout.local_dir, manifest):
            raise ValueError("manifest is missing, linked, or not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
        flags |= getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(manifest, flags), "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise ValueError("manifest must be a single-link regular file")
            raw = handle.read(4097)
            if len(raw) > 4096:
                raise ValueError("manifest exceeds its size bound")
        payload = json.loads(raw, object_pairs_hook=unique_manifest_object)
        dependencies = (
            payload.get("devDependencies") if isinstance(payload, dict) else None
        )
        version = (
            dependencies.get("@openai/codex")
            if isinstance(dependencies, dict)
            else None
        )
        if (
            not isinstance(version, str)
            or version != version.strip()
            or not is_exact_version(version)
            or payload.get("private") is not True
            or payload
            != {
                "name": "codex-local-managed-install",
                "private": True,
                "devDependencies": {"@openai/codex": version},
            }
        ):
            raise ValueError(
                "manifest does not exactly match generated version-pinned intent"
            )
        return version
    except (OSError, ValueError, RecursionError) as exc:
        raise CodexWranglerError(
            "Legacy first-install recovery refused candidate {}: {}. "
            "Nothing was changed; preserve it for inspection.".format(candidate, exc)
        ) from exc


def legacy_candidate_versions(layout: Layout) -> List[Tuple[Path, str]]:
    """Find bounded legacy repair evidence without authorizing ordinary adoption."""

    validate_managed_root(layout)
    if not layout.local_dir.is_dir():
        return []
    entries = []
    try:
        with os.scandir(layout.local_dir) as iterator:
            for entry in iterator:
                entries.append(Path(entry.path))
                if len(entries) > 64:
                    raise CodexWranglerError(
                        "Legacy first-install recovery refuses more than 64 runtime entries; "
                        "preserve the runtime for inspection."
                    )
        candidates = [
            path
            for path in entries
            if re.fullmatch(r"\.candidate-[0-9a-f]{32}", path.name)
        ]
        if not candidates:
            return []
        for path in entries:
            if (
                path not in candidates
                and path.name not in (".npm-cache", ".maintenance")
            ) or not stat.S_ISDIR(path.lstat().st_mode):
                raise CodexWranglerError(
                    "Legacy first-install recovery refuses foreign or linked runtime "
                    "entry {}; nothing was changed.".format(path)
                )
        return [
            (candidate / "package.json", legacy_candidate_version(layout, candidate))
            for candidate in sorted(candidates)
        ]
    except OSError as exc:
        raise CodexWranglerError(
            "Cannot inspect legacy first-install evidence: {}".format(exc)
        ) from exc


def prove_managed_install(
    layout: Layout, *, allow_legacy_candidates: bool = False
) -> Tuple[str, ...]:
    """Return strong ownership evidence or reject repair before mutation."""

    evidence: List[str] = []
    active = observe_repair_runtime(layout)
    if active.slot_name is not None:
        slot_metadata = read_slot_metadata(layout, active.slot_name)
        if slot_metadata is not None:
            evidence.append(
                str(layout_for_slot(layout, active.slot_name).metadata_path)
            )
    for slot_name in ("a", "b"):
        if slot_name == active.slot_name:
            continue
        if read_slot_metadata(layout, slot_name) is not None:
            evidence.append(str(layout_for_slot(layout, slot_name).metadata_path))
    metadata = read_json_object_if_valid(layout.metadata_path)
    if not layout.metadata_path.is_symlink() and metadata_looks_managed(
        metadata, layout
    ):
        evidence.append(str(layout.metadata_path))
    if package_manifest_looks_managed(layout.local_package_json_path):
        evidence.append(str(layout.local_package_json_path))
    if evidence:
        return tuple(evidence)
    if read_initial_install(layout) is not None:
        return (str(initial_install_path(layout)),)
    # Migration also calls this proof helper. Candidate intent authorizes only
    # explicit repair, never implicit relocation of a protected namespace.
    if allow_legacy_candidates:
        legacy_candidates = legacy_candidate_versions(layout)
        if legacy_candidates:
            return tuple(str(path) for path, _ in legacy_candidates)
    raise CodexWranglerError(
        "Cannot prove a codex-wrangler-managed install under {}. Repair did "
        "not remove or rewrite anything; expected valid managed metadata at "
        "{}, a valid managed package manifest at {}, a completed slot record, "
        "a valid first-install receipt, or strictly validated legacy candidate "
        "manifests. An explicit HOME choice cannot "
        "establish ownership. This may be foreign data or an older interrupted "
        "first install without recovery evidence; preserve it for inspection "
        "rather than automatically applying --force.".format(
            layout.project_root,
            layout.metadata_path,
            layout.local_package_json_path,
        )
    )


def installed_package_manifest_path(layout: Layout) -> Path:
    """Return the installed cross-platform Codex package manifest path."""

    return layout.local_node_modules_dir / "@openai" / "codex" / "package.json"


def exact_version_from_package_manifest(prefix: Path, path: Path) -> Optional[str]:
    """Read one exact ``@openai/codex`` package version when available."""

    if not regular_contained_file(prefix, path):
        return None
    payload = read_json_object_if_valid(path)
    if not payload:
        return None
    version = payload.get("version")
    if not isinstance(version, str):
        return None
    normalized = version.strip()
    return normalized if is_exact_version(normalized) else None


def exact_requested_version_from_managed_manifest(
    prefix: Path,
    path: Path,
) -> Optional[str]:
    """Read an exact requested version from a proven managed package file."""

    if not regular_contained_file(prefix, path) or not package_manifest_looks_managed(
        path
    ):
        return None
    payload = read_json_object_if_valid(path)
    if not payload:
        return None
    version = payload["devDependencies"]["@openai/codex"].strip()
    return version if is_exact_version(version) else None


def append_exact_version(
    candidates: List[Tuple[str, str]],
    source: str,
    raw_version: object,
) -> None:
    """Append one normalized exact version candidate when usable."""

    if not isinstance(raw_version, str):
        return
    version = raw_version.strip()
    if is_exact_version(version):
        candidates.append((source, version))


def append_prefix_version_evidence(
    candidates: List[Tuple[str, str]],
    evidence_layout: Layout,
    source_prefix: str,
) -> None:
    """Append contained exact package evidence from one npm prefix."""

    prefix = evidence_layout.local_dir
    manifest_version = exact_requested_version_from_managed_manifest(
        prefix,
        evidence_layout.local_package_json_path,
    )
    append_exact_version(
        candidates,
        "{} package.json".format(source_prefix),
        manifest_version,
    )
    lockfile_version = None
    if regular_contained_file(prefix, evidence_layout.local_package_lock_path):
        try:
            lockfile_version = infer_installed_version_from_lockfile(
                evidence_layout.local_package_lock_path
            )
        except CodexWranglerError:
            pass
    append_exact_version(
        candidates,
        "{} package-lock.json".format(source_prefix),
        lockfile_version,
    )
    installed_version = exact_version_from_package_manifest(
        prefix,
        installed_package_manifest_path(evidence_layout),
    )
    append_exact_version(
        candidates,
        "{} @openai/codex package.json".format(source_prefix),
        installed_version,
    )


def collect_exact_version_evidence(layout: Layout) -> List[Tuple[str, str]]:
    """Collect exact Codex versions from all surviving managed records."""

    candidates: List[Tuple[str, str]] = []
    active = observe_repair_runtime(layout)
    if active.slot_name is not None:
        evidence_layout = layout_for_slot(layout, active.slot_name)
        slot_metadata = read_slot_metadata(layout, active.slot_name)
        if slot_metadata is not None:
            append_exact_version(
                candidates,
                "active slot completion record",
                slot_metadata.get("codex_version"),
            )
            append_prefix_version_evidence(
                candidates,
                evidence_layout,
                "active slot",
            )
            return candidates
        append_prefix_version_evidence(
            candidates,
            evidence_layout,
            "incomplete selected slot",
        )

    metadata = read_json_object_if_valid(layout.metadata_path)
    if not layout.metadata_path.is_symlink() and metadata_looks_managed(
        metadata, layout
    ):
        assert metadata is not None
        append_exact_version(
            candidates,
            "metadata codex_version",
            metadata.get("codex_version"),
        )
        append_exact_version(
            candidates,
            "metadata codex_selector",
            metadata.get("codex_selector"),
        )

    append_prefix_version_evidence(
        candidates,
        layout,
        "managed root",
    )
    for slot_name in ("a", "b"):
        slot_metadata = read_slot_metadata(layout, slot_name)
        if slot_metadata is not None:
            append_exact_version(
                candidates,
                "completed slot {} record".format(slot_name),
                slot_metadata.get("codex_version"),
            )
    if not metadata_looks_managed(metadata, layout) and not any(
        read_slot_metadata(layout, name) is not None for name in ("a", "b")
    ):
        receipt = read_initial_install(layout)
        if receipt is not None:
            append_exact_version(
                candidates,
                "first-install receipt",
                receipt["codex_version"],
            )
        elif not package_manifest_looks_managed(layout.local_package_json_path):
            for path, version in legacy_candidate_versions(layout):
                append_exact_version(
                    candidates,
                    "legacy candidate {}".format(path.parent.name),
                    version,
                )
    return candidates


def build_repair_plan(layout: Layout) -> RepairPlan:
    """Prove ownership and select one unambiguous exact recovery version."""

    repair_write_targets = (
        layout.local_package_json_path,
        layout.local_package_lock_path,
        layout.metadata_path,
        layout.launcher_path,
        layout.readme_path,
        layout.gitignore_path,
    )
    symbolic_targets = [str(path) for path in repair_write_targets if path.is_symlink()]
    if symbolic_targets:
        raise CodexWranglerError(
            "Repair will not rewrite symbolic-link targets: {}.".format(
                ", ".join(symbolic_targets)
            )
        )
    ownership_evidence = prove_managed_install(layout, allow_legacy_candidates=True)
    candidates = collect_exact_version_evidence(layout)
    versions = sorted({version for _, version in candidates})
    if not versions:
        raise CodexWranglerError(
            "The managed install under {} has no surviving exact Codex "
            "version. Repair will not select `latest` or implicitly upgrade; "
            "review the damaged records and choose an explicit install or "
            "upgrade workflow instead.".format(layout.project_root)
        )
    if len(versions) > 1:
        details = ", ".join(
            "{}={}".format(source, version) for source, version in candidates
        )
        raise CodexWranglerError(
            "Conflicting exact Codex versions were found under {}: {}. "
            "Repair did not remove or rewrite anything; resolve the evidence "
            "before retrying.".format(layout.project_root, details)
        )
    selected_version = versions[0]
    selected_sources = tuple(
        source for source, version in candidates if version == selected_version
    )
    return RepairPlan(
        codex_version=selected_version,
        ownership_evidence=ownership_evidence,
        version_evidence=selected_sources,
        legacy_first_install=all(
            Path(source).name == "package.json"
            and Path(source).parent.parent == layout.local_dir
            for source in ownership_evidence
        ),
    )
