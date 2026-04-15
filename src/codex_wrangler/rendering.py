"""Deterministic rendered content for managed files and reports."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from .constants import (
    CODEX_CHANNELS,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_STABLE_CODEX_SELECTOR,
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    PREVIEW_DIST_TAG_CANDIDATES,
    README_MARKER,
    SCHEMA_VERSION,
    SCRIPT_NAME,
    SCRIPT_VERSION,
    SCRIPT_MARKER,
)
from .models import Config, Layout
from .runtime import utc_now_iso


def build_local_package_json(codex_version: str) -> str:
    """Return the deterministic package.json content for `.codex-local/`."""

    payload = {
        "name": "codex-local-managed-install",
        "private": True,
        "devDependencies": {
            "@openai/codex": codex_version,
        },
    }
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def build_metadata(config: Config) -> Dict[str, Any]:
    """Build the metadata document recorded under the managed local directory."""

    return {
        "schema_version": SCHEMA_VERSION,
        "script_name": SCRIPT_NAME,
        "script_version": SCRIPT_VERSION,
        "generated_at": utc_now_iso(),
        "project_root": str(config.project_root),
        "codex_selector": config.codex_selector,
        "codex_channel": config.codex_channel,
        "codex_version": config.codex_version,
        "shared_home": config.shared_home,
        "version_source": config.version_source,
        "available_versions": dict(config.available_versions),
        "available_versions_updated_at": config.available_versions_updated_at,
        "paths": {
            "local_dir": config.layout.local_dir_relative,
            "codex_home_dir": config.layout.codex_home_relative,
            "launcher": config.layout.launcher_relative,
            "readme_local": config.layout.readme_relative,
        },
    }


def build_gitignore_block(layout: Layout) -> str:
    """Return the managed `.gitignore` block."""

    lines = [
        GITIGNORE_BEGIN,
        "{}/".format(layout.local_dir_relative.rstrip("/")),
        "{}/".format(layout.codex_home_relative.rstrip("/")),
        layout.readme_relative,
        GITIGNORE_END,
        "",
    ]
    return "\n".join(lines)


def build_launcher_preflight_lines() -> list[str]:
    """Return the shell preflight block embedded in the launcher."""

    return [
        'preflight_mode="${CODEX_LOCAL_PREFLIGHT:-warn}"',
        'preflight_prefix="[codex-local] preflight"',
        "",
        "emit_preflight_warning() {",
        '  printf "%s: %s\\n" "$preflight_prefix" "$1" >&2',
        "}",
        "",
        "preflight_fail() {",
        '  emit_preflight_warning "$1"',
        "  exit 1",
        "}",
        "",
        "handle_preflight_mismatch() {",
        '  if [[ "$preflight_mode" == "strict" ]]; then',
        '    preflight_fail "$1"',
        "  fi",
        '  emit_preflight_warning "$1"',
        "}",
        "",
        "normalize_node_version() {",
        '  local raw_value="${1:-}"',
        '  raw_value="${raw_value#v}"',
        '  printf "%s" "$raw_value"',
        "}",
        "",
        "is_literal_node_version() {",
        '  [[ "${1:-}" =~ ^v?[0-9]+(\\.[0-9]+){0,2}$ ]]',
        "}",
        "",
        'case "$preflight_mode" in',
        "  warn|strict|off) ;;",
        "  *)",
        '    emit_preflight_warning "Unknown CODEX_LOCAL_PREFLIGHT value \\"$preflight_mode\\"; defaulting to warn."',
        '    preflight_mode="warn"',
        "    ;;",
        "esac",
        "",
        'if [[ "$preflight_mode" != "off" ]]; then',
        '  node_path="$(command -v node || true)"',
        '  npm_path="$(command -v npm || true)"',
        '  npx_path="$(command -v npx || true)"',
        '  if [[ -z "$node_path" || -z "$npm_path" || -z "$npx_path" ]]; then',
        '    preflight_fail "Required node/npm/npx commands are not all available in PATH."',
        "  fi",
        "",
        '  selector_path=""',
        '  selector_value=""',
        '  if [[ -f "$repo_root/.nvmrc" ]]; then',
        '    selector_path="$repo_root/.nvmrc"',
        '    selector_value="$(sed -n \'1{s/\\r$//;s/^[[:space:]]*//;s/[[:space:]]*$//;p;}\' "$selector_path" 2>/dev/null)"',
        '  elif [[ -f "$repo_root/.node-version" ]]; then',
        '    selector_path="$repo_root/.node-version"',
        '    selector_value="$(sed -n \'1{s/\\r$//;s/^[[:space:]]*//;s/[[:space:]]*$//;p;}\' "$selector_path" 2>/dev/null)"',
        '  elif [[ -f "$repo_root/.tool-versions" ]]; then',
        '    selector_path="$repo_root/.tool-versions"',
        '    selector_value="$(awk \'/^[[:space:]]*nodejs[[:space:]]+/ { print $2; exit }\' "$selector_path" 2>/dev/null)"',
        "    selector_value=\"${selector_value%$'\\r'}\"",
        "  fi",
        "",
        '  if [[ -n "$selector_path" && -n "$selector_value" ]]; then',
        '    selector_label="${selector_path#"$repo_root/"}"',
        '    actual_node_version="$(node --version 2>/dev/null || true)"',
        '    if [[ -z "$actual_node_version" ]]; then',
        '      handle_preflight_mismatch "Project declares Node.js selector ${selector_label}=${selector_value}, but node --version could not be resolved."',
        '    elif is_literal_node_version "$selector_value"; then',
        '      expected_node_version="$(normalize_node_version "$selector_value")"',
        '      actual_node_version_normalized="$(normalize_node_version "$actual_node_version")"',
        '      if [[ "$expected_node_version" != "$actual_node_version_normalized" ]]; then',
        '        handle_preflight_mismatch "Project declares Node.js ${selector_value} in ${selector_label}, but PATH resolves node ${actual_node_version} at ${node_path}."',
        "      fi",
        "    else",
        '      emit_preflight_warning "Project declares Node.js selector ${selector_label}=${selector_value}. Launch codex-local from the project-activated shell for best parity."',
        "    fi",
        "  fi",
        "fi",
    ]


def build_launcher_update_notice_lines(layout: Layout) -> list[str]:
    """Return the shell block that prints a known-update notice before launch."""

    metadata_path = "{}/.codex-wrangler.json".format(layout.local_dir_relative)
    return [
        "emit_update_notice() {",
        "  node - \"$repo_root/{}\" <<'NODE'".format(metadata_path),
        'const fs = require("fs");',
        "const metadataPath = process.argv[2];",
        "let metadata;",
        "try {",
        '  metadata = JSON.parse(fs.readFileSync(metadataPath, "utf8"));',
        "} catch (error) {",
        "  process.exit(0);",
        "}",
        'const installedVersion = typeof metadata.codex_version === "string" ? metadata.codex_version : null;',
        'const channel = typeof metadata.codex_channel === "string" ? metadata.codex_channel.toLowerCase() : null;',
        'const availableVersions = metadata && typeof metadata.available_versions === "object" && metadata.available_versions !== null ? metadata.available_versions : {};',
        'const availableVersion = typeof availableVersions[channel] === "string" ? availableVersions[channel] : null;',
        "if (!installedVersion || !channel || !availableVersion) {",
        "  process.exit(0);",
        "}",
        "function parseVersion(raw) {",
        '  const normalized = String(raw).trim().replace(/^v/, "");',
        '  const pieces = normalized.split("-", 2);',
        '  const main = pieces[0].split(".").map((part) => Number.parseInt(part, 10) || 0);',
        "  while (main.length < 3) {",
        "    main.push(0);",
        "  }",
        '  const prerelease = pieces.length > 1 ? pieces[1].split(".") : [];',
        "  return { main, prerelease };",
        "}",
        "function compareIdentifier(left, right) {",
        "  const leftIsNumeric = /^[0-9]+$/.test(left);",
        "  const rightIsNumeric = /^[0-9]+$/.test(right);",
        "  if (leftIsNumeric && rightIsNumeric) {",
        "    return Number(left) - Number(right);",
        "  }",
        "  if (leftIsNumeric) {",
        "    return -1;",
        "  }",
        "  if (rightIsNumeric) {",
        "    return 1;",
        "  }",
        "  if (left < right) {",
        "    return -1;",
        "  }",
        "  if (left > right) {",
        "    return 1;",
        "  }",
        "  return 0;",
        "}",
        "function comparePrerelease(left, right) {",
        "  if (left.length === 0 && right.length === 0) {",
        "    return 0;",
        "  }",
        "  if (left.length === 0) {",
        "    return 1;",
        "  }",
        "  if (right.length === 0) {",
        "    return -1;",
        "  }",
        "  const count = Math.max(left.length, right.length);",
        "  for (let index = 0; index < count; index += 1) {",
        "    const leftValue = left[index];",
        "    const rightValue = right[index];",
        "    if (leftValue === undefined) {",
        "      return -1;",
        "    }",
        "    if (rightValue === undefined) {",
        "      return 1;",
        "    }",
        "    const compared = compareIdentifier(leftValue, rightValue);",
        "    if (compared !== 0) {",
        "      return compared;",
        "    }",
        "  }",
        "  return 0;",
        "}",
        "function compareVersions(leftRaw, rightRaw) {",
        "  const left = parseVersion(leftRaw);",
        "  const right = parseVersion(rightRaw);",
        "  for (let index = 0; index < 3; index += 1) {",
        "    const delta = left.main[index] - right.main[index];",
        "    if (delta !== 0) {",
        "      return delta;",
        "    }",
        "  }",
        "  return comparePrerelease(left.prerelease, right.prerelease);",
        "}",
        "if (compareVersions(installedVersion, availableVersion) >= 0) {",
        "  process.exit(0);",
        "}",
        "console.error(`NOTE: @openai/codex has an available update on ${channel} (installed ${installedVersion}; available ${availableVersion}). Run \\`codex-wrangler --update\\` to refresh version information and \\`codex-wrangler --upgrade --channel ${channel}\\` to install it.`);",
        "NODE",
        "}",
        "",
        "emit_update_notice",
    ]


def build_launcher_content(config: Config) -> str:
    """Build the generated shell launcher."""

    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# {}".format(SCRIPT_MARKER),
        "# Generated by {}".format(SCRIPT_NAME),
        "#",
        "# This launcher keeps the Codex npm package local to the repository by",
        "# invoking npx with --prefix against the managed local directory.",
        "#",
        "# In default mode it also redirects HOME into the managed project Codex",
        "# home directory so auth, sessions, logs, and other Codex state remain",
        "# isolated per project.",
        "",
        'repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"',
        'local_prefix="$repo_root/{}"'.format(config.layout.local_dir_relative),
        'export NPM_CONFIG_CACHE="$local_prefix/.npm-cache"',
        'export CODEX_LOCAL_MANAGED_BY="{}"'.format(SCRIPT_NAME),
    ]

    if config.shared_home:
        lines.extend(
            [
                "# Shared-home mode leaves HOME unchanged so the operator's",
                "# normal ~/.codex state is reused across projects.",
            ]
        )
    else:
        lines.extend(
            [
                'export HOME="$repo_root/{}"'.format(config.layout.codex_home_relative),
                'export XDG_CONFIG_HOME="$HOME/.config"',
                'export XDG_CACHE_HOME="$HOME/.cache"',
                'export XDG_STATE_HOME="$HOME/.local/state"',
                'export XDG_DATA_HOME="$HOME/.local/share"',
            ]
        )

    lines.extend([""])
    lines.extend(build_launcher_preflight_lines())
    lines.extend([""])
    lines.extend(build_launcher_update_notice_lines(config.layout))
    lines.extend(["", 'exec npx --prefix "$local_prefix" codex "$@"', ""])
    return "\n".join(lines)


def build_local_readme_content(config: Config) -> str:
    """Build the generated project-local operator README."""

    lines = [
        README_MARKER,
        "# Local Codex Start Guide",
        "",
        "This file was generated by `{}` and is intended for local operator use.".format(
            SCRIPT_NAME
        ),
        "It is ignored on purpose and should not be committed.",
        "",
        "## Managed Paths",
        "",
        "- Local install: `{}`".format(config.layout.local_dir_relative),
        "- Local Codex home: `{}`".format(config.layout.codex_home_relative),
        "- Launcher: `{}`".format(config.layout.launcher_relative),
        "- This README: `{}`".format(config.layout.readme_relative),
        "",
        "## Installed Version",
        "",
        "- Channel: `{}`".format(config.codex_channel or "unknown"),
        "- Requested version: `{}`".format(config.codex_selector),
        "- Resolved Codex version: `{}`".format(config.codex_version),
        "- Version source: `{}`".format(config.version_source),
        "- HOME mode: `{}`".format(
            "shared user HOME" if config.shared_home else "project-local isolated HOME"
        ),
        "- Known stable version: `{}`".format(
            config.available_versions.get("stable") or "unavailable"
        ),
        "- Known beta version: `{}`".format(
            config.available_versions.get("beta") or "unavailable"
        ),
        "- Known alpha version: `{}`".format(
            config.available_versions.get("alpha") or "unavailable"
        ),
        "",
        "## Start Codex",
        "",
        "```bash",
        "./{}".format(config.layout.launcher_relative),
        "```",
        "",
        "## Launcher Preflight",
        "",
        "The launcher runs a cheap runtime preflight before it starts Codex.",
        "By default it warns when the project declares a checked-in Node.js",
        "selector but the active shell appears inconsistent with it.",
        "",
        "Use one of these modes when needed:",
        "",
        "- `CODEX_LOCAL_PREFLIGHT=warn`",
        "- `CODEX_LOCAL_PREFLIGHT=strict`",
        "- `CODEX_LOCAL_PREFLIGHT=off`",
        "",
        "## Resume Codex",
        "",
        "```bash",
        "./{} resume".format(config.layout.launcher_relative),
        "./{} resume --last".format(config.layout.launcher_relative),
        "```",
        "",
        "## Audit",
        "",
        "```bash",
        "npm audit --prefix ./{}".format(config.layout.local_dir_relative),
        "```",
        "",
        "## Inspect Managed State",
        "",
        "```bash",
        "{} --inspect .".format(SCRIPT_NAME),
        "```",
        "",
        "## Self-Test Managed State",
        "",
        "```bash",
        "{} --selftest .".format(SCRIPT_NAME),
        "```",
        "",
        "## Refresh Known Versions",
        "",
        "```bash",
        "{} --update .".format(SCRIPT_NAME),
        "```",
        "",
        "## Upgrade To Latest Known Channel Version",
        "",
        "```bash",
        "{} --upgrade --channel stable .".format(SCRIPT_NAME),
        "{} --upgrade --channel beta .".format(SCRIPT_NAME),
        "{} --upgrade --channel alpha .".format(SCRIPT_NAME),
        "```",
        "",
        "## Upgrade To One Explicit Version",
        "",
        "```bash",
        "{} --upgrade --channel beta --version 0.31.0-beta.2 .".format(SCRIPT_NAME),
        "```",
        "",
        "## Uninstall Managed Setup",
        "",
        "```bash",
        "{} --uninstall .".format(SCRIPT_NAME),
        "```",
        "",
    ]
    return "\n".join(lines)


def local_package_json_looks_managed(content: str) -> bool:
    """Return True when package.json content matches the managed shape."""

    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        return False

    if not isinstance(payload, dict):
        return False
    if payload.get("name") != "codex-local-managed-install":
        return False
    if payload.get("private") is not True:
        return False

    dev_dependencies = payload.get("devDependencies")
    if not isinstance(dev_dependencies, dict):
        return False
    version = dev_dependencies.get("@openai/codex")
    return isinstance(version, str) and bool(version)


def build_install_summary(config: Config) -> str:
    """Return the operator summary shown after install and upgrade operations."""

    lines = [
        "Operation complete.",
        "",
        "Project root: {}".format(config.project_root),
        "Operation: {}".format(config.operation),
        "Channel: {}".format(config.codex_channel or "unknown"),
        "Requested version: {}".format(config.codex_selector),
        "Resolved Codex version: {}".format(config.codex_version),
        "Version source: {}".format(config.version_source),
        "Local package manifest: {}".format(config.layout.local_package_json_path),
        "Metadata file: {}".format(config.layout.metadata_path),
        "Launcher: {}".format(config.layout.launcher_path),
        "Local README: {}".format(config.layout.readme_path),
        "HOME mode: {}".format(
            "shared user HOME" if config.shared_home else "project-local isolated HOME"
        ),
        "",
        "Recommended usage:",
        "  {}".format(config.layout.launcher_path),
        "  {} resume".format(config.layout.launcher_path),
        "  {} resume --last".format(config.layout.launcher_path),
        "",
        "Audit command:",
        "  npm audit --prefix {}".format(config.layout.local_dir),
    ]
    return "\n".join(lines)


def build_available_versions_table(
    available_versions: Dict[str, Optional[str]],
) -> str:
    """Render the known channel versions as a simple fixed-width table."""

    rows = [("Channel", "Selector", "Known version")]
    rows.extend(
        (
            channel,
            DEFAULT_STABLE_CODEX_SELECTOR if channel == "stable" else channel,
            available_versions.get(channel) or "unavailable",
        )
        for channel in CODEX_CHANNELS
    )
    widths = [
        max(len(str(row[column_index])) for row in rows)
        for column_index in range(len(rows[0]))
    ]
    rendered_rows = []
    for row_index, row in enumerate(rows):
        rendered_rows.append(
            "  ".join(
                str(value).ljust(widths[column_index])
                for column_index, value in enumerate(row)
            )
        )
        if row_index == 0:
            rendered_rows.append(
                "  ".join("-" * widths[column_index] for column_index in range(3))
            )
    return "\n".join(rendered_rows)


def build_known_versions(
    available_versions: Dict[str, Optional[str]],
    available_versions_updated_at: Optional[str],
) -> Dict[str, Any]:
    """Return the install-channel defaults and locally known versions."""

    return {
        "default_install_selector": DEFAULT_INSTALL_CODEX_SELECTOR,
        "stable_dist_tag": DEFAULT_STABLE_CODEX_SELECTOR,
        "preview_dist_tag_candidates": ", ".join(PREVIEW_DIST_TAG_CANDIDATES),
        "available_versions": dict(available_versions),
        "available_versions_updated_at": available_versions_updated_at,
    }
