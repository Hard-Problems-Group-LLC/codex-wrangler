"""Release-channel helpers for resolving Codex package versions."""

from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
import re
from typing import Dict, Mapping, Optional

from .constants import (
    CODEX_CHANNELS,
    CODEX_PACKAGE_NAME,
    DEFAULT_PREVIEW_CODEX_SELECTOR,
)
from .models import CodexWranglerError, Config
from .runtime import run_command, utc_now_iso

CHANNEL_TO_SELECTOR = {
    "stable": "latest",
    "beta": "beta",
    "alpha": "alpha",
}
EXACT_VERSION_PATTERN = re.compile(r"^[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?$")


def looks_like_preview(version_or_selector: str) -> bool:
    """Return True when one selector or version string targets a preview build."""

    channel = infer_codex_channel(version_or_selector)
    return channel in ("alpha", "beta")


def is_exact_version(version_or_selector: str) -> bool:
    """Return True when one selector string looks like an exact package version."""

    return EXACT_VERSION_PATTERN.fullmatch(version_or_selector.strip()) is not None


def infer_codex_channel(version_or_selector: Optional[str]) -> Optional[str]:
    """Infer the Codex release channel from one selector or exact version string."""

    if not version_or_selector:
        return None
    normalized = version_or_selector.strip().lower()
    if normalized in ("latest", "stable"):
        return "stable"
    if normalized == DEFAULT_PREVIEW_CODEX_SELECTOR:
        return "alpha"
    if normalized in ("alpha", "beta"):
        return normalized
    if "-alpha" in normalized:
        return "alpha"
    if "-beta" in normalized:
        return "beta"
    if is_exact_version(normalized):
        return "stable"
    return None


def selector_for_channel(channel: str) -> str:
    """Return the npm selector associated with one supported channel."""

    try:
        return CHANNEL_TO_SELECTOR[channel]
    except KeyError as exc:
        raise CodexWranglerError(
            "Unsupported Codex channel: {}".format(channel)
        ) from exc


def parse_dist_tags(payload: str) -> Dict[str, str]:
    """Parse and validate one `npm view ... dist-tags --json` payload."""

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        raise CodexWranglerError(
            "Failed to parse npm dist-tags for {}.".format(CODEX_PACKAGE_NAME)
        ) from exc
    if not isinstance(data, dict):
        raise CodexWranglerError(
            "npm dist-tags for {} did not return a JSON object.".format(
                CODEX_PACKAGE_NAME
            )
        )
    tags: Dict[str, str] = {}
    for raw_tag, raw_version in data.items():
        if isinstance(raw_tag, str) and isinstance(raw_version, str):
            tags[raw_tag] = raw_version
    if not tags:
        raise CodexWranglerError(
            "npm dist-tags for {} did not include any usable selectors.".format(
                CODEX_PACKAGE_NAME
            )
        )
    return tags


def fetch_codex_dist_tags(
    npm_name: str,
    project_root: Path,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, str]:
    """Query npm for the published dist-tags of the Codex package."""

    completed = run_command(
        [npm_name, "view", CODEX_PACKAGE_NAME, "dist-tags", "--json"],
        cwd=str(project_root),
        capture_output=True,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    return parse_dist_tags(completed.stdout or "")


def fetch_available_codex_versions(
    npm_name: str,
    project_root: Path,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[int] = None,
) -> Dict[str, Optional[str]]:
    """Return the locally tracked latest versions for the supported channels."""

    dist_tags = fetch_codex_dist_tags(
        npm_name,
        project_root,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    available_versions: Dict[str, Optional[str]] = {}
    for channel, selector in CHANNEL_TO_SELECTOR.items():
        raw_version = dist_tags.get(selector)
        if raw_version is None:
            available_versions[channel] = None
            continue
        version = raw_version.strip()
        if not is_exact_version(version):
            raise CodexWranglerError(
                "npm dist-tag {!r} for {} did not resolve to an exact "
                "version: {!r}.".format(
                    selector,
                    CODEX_PACKAGE_NAME,
                    raw_version,
                )
            )
        available_versions[channel] = version
    return available_versions


def resolve_explicit_selector_version(
    selector: str,
    channel: Optional[str],
    available_versions: Dict[str, Optional[str]],
) -> str:
    """Resolve one explicit install selector against the available-version map."""

    if is_exact_version(selector):
        inferred_channel = infer_codex_channel(selector)
        if (
            channel is not None
            and inferred_channel is not None
            and inferred_channel != channel
        ):
            raise CodexWranglerError(
                "Exact version {} does not match the {} channel.".format(
                    selector,
                    channel,
                )
            )
        return selector
    inferred_channel = channel or infer_codex_channel(selector)
    if inferred_channel is None:
        raise CodexWranglerError(
            "Codex selector {!r} is not an exact version or a supported "
            "channel/dist-tag.".format(selector)
        )
    resolved_version = available_versions.get(inferred_channel)
    if not resolved_version:
        raise CodexWranglerError(
            "No published version is currently known for the {} channel. Run "
            "`codex-wrangler --update` again later.".format(inferred_channel)
        )
    if not is_exact_version(resolved_version):
        raise CodexWranglerError(
            "Recorded {} channel value is not an exact Codex version: {!r}. "
            "Run codex-wrangler --update to refresh trusted dist-tags.".format(
                inferred_channel,
                resolved_version,
            )
        )
    return resolved_version.strip()


def resolve_install_version(
    config: Config,
    npm_name: str,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[int] = None,
) -> Config:
    """Resolve an install-time selector into an exact version and catalog snapshot."""

    available_versions = fetch_available_codex_versions(
        npm_name,
        config.project_root,
        env=env,
        timeout_seconds=timeout_seconds,
    )
    resolved_version = resolve_explicit_selector_version(
        config.codex_selector,
        config.codex_channel,
        available_versions,
    )
    return replace(
        config,
        codex_version=resolved_version,
        codex_channel=config.codex_channel
        or infer_codex_channel(config.codex_selector),
        available_versions=available_versions,
        available_versions_updated_at=utc_now_iso(),
    )


def resolve_upgrade_version(config: Config) -> Config:
    """Resolve an upgrade request from the locally known version catalog."""

    channel = config.codex_channel
    if channel is None:
        raise CodexWranglerError(
            "Upgrade requires a Codex channel. Use `--channel <stable|beta|alpha>`."
        )
    if channel not in CODEX_CHANNELS:
        raise CodexWranglerError("Unsupported Codex channel: {}".format(channel))
    if config.codex_selector == "latest":
        resolved_version = config.available_versions.get(channel)
        if not resolved_version:
            raise CodexWranglerError(
                "No known version is recorded for the {} channel. Run "
                "`codex-wrangler --update` first.".format(channel)
            )
    else:
        resolved_version = resolve_explicit_selector_version(
            config.codex_selector,
            channel,
            config.available_versions,
        )
    if not is_exact_version(resolved_version):
        raise CodexWranglerError(
            "Recorded {} channel value is not an exact Codex version: {!r}. "
            "Run codex-wrangler --update to refresh trusted dist-tags.".format(
                channel,
                resolved_version,
            )
        )
    resolved_version = resolved_version.strip()
    return replace(
        config,
        codex_version=resolved_version,
        version_source=(
            "known {} channel from local metadata".format(channel)
            if config.codex_selector == "latest"
            else "explicit --version for {} channel".format(channel)
        ),
    )
