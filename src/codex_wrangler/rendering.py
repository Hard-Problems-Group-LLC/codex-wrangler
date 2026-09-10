"""Deterministic rendered content for managed files and reports."""

from __future__ import annotations

import json
from pathlib import Path
import shlex
from typing import Any, Dict, Optional

from .constants import (
    CODEX_CHANNELS,
    DEFAULT_HOME_DIR,
    DEFAULT_INSTALL_CODEX_SELECTOR,
    DEFAULT_LOCAL_DIR,
    DEFAULT_STABLE_CODEX_SELECTOR,
    GITIGNORE_BEGIN,
    GITIGNORE_END,
    LEGACY_HOME_DIR,
    LEGACY_LOCAL_DIR,
    MAINTENANCE_LOCK_FILENAME,
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
    """Return deterministic package.json content for the managed npm root."""

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
        "reasonable_permissions_enabled": config.reasonable_permissions_enabled,
        "version_source": config.version_source,
        "available_versions": dict(config.available_versions),
        "available_versions_updated_at": config.available_versions_updated_at,
        "active_slot": config.active_slot,
        "active_pointer_kind": config.active_pointer_kind,
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
        "# Local Codex package, home, wrapper, and sentinel artifacts.",
        "{}/".format(layout.local_dir_relative.rstrip("/")),
        "{}/".format(layout.codex_home_relative.rstrip("/")),
        LEGACY_LOCAL_DIR,
        LEGACY_HOME_DIR,
        ".codex",
        ".local/",
        MAINTENANCE_LOCK_FILENAME,
        layout.launcher_relative,
        layout.readme_relative,
        GITIGNORE_END,
        "",
    ]
    return "\n".join(lines)


def build_maintenance_lock_gitignore_block() -> str:
    """Return the minimal ignore block retained after uninstall."""

    return "\n".join(
        [
            GITIGNORE_BEGIN,
            "# Stable serialization inode retained after uninstall.",
            MAINTENANCE_LOCK_FILENAME,
            GITIGNORE_END,
            "",
        ]
    )


def build_launcher_slot_record_validator_script() -> str:
    """Return the JavaScript validator used for active A/B authority."""

    return " ".join(
        [
            'const fs = require("fs");',
            'const path = require("path");',
            'const record = JSON.parse(fs.readFileSync(process.argv[1], "utf8"));',
            "const version = record.codex_version;",
            'const channels = ["stable", "beta", "alpha"];',
            r"const exactPattern = /^[0-9]+(?:\.[0-9]+){2}(?:[-+][0-9A-Za-z.-]+)?$/;",
            'const isExact = (value) => typeof value === "string" && exactPattern.test(value.trim());',
            'const inferChannel = (value) => { if (typeof value !== "string" || value.length === 0) return null; const normalized = value.trim().toLowerCase(); if (normalized === "latest" || normalized === "stable") return "stable"; if (normalized === "__preview__") return "alpha"; if (normalized === "alpha" || normalized === "beta") return normalized; if (normalized.includes("-alpha")) return "alpha"; if (normalized.includes("-beta")) return "beta"; return isExact(normalized) ? "stable" : null; };',
            'const directLegacy = process.argv[4] === process.argv[10] && ((record.shared_home === true && (process.argv[6] === process.argv[11] || process.argv[15] === "1")) || (record.shared_home === false && (process.argv[6] === process.argv[11] || (process.argv[6] === process.argv[12] && process.argv[13] === "1"))));',
            'const legacyHomeBound = record.shared_home === true || (record.shared_home === false && (process.argv[13] === "1" || process.argv[14] === "1"));',
            'const homeNamespaceBound = process.argv[6] === process.argv[12] || (process.argv[14] === "1" && process.argv[6] === process.argv[11]);',
            'const legacyAlias = process.argv[4] === process.argv[9] && homeNamespaceBound && record.local_dir === process.argv[10] && process.argv[5] === "1" && legacyHomeBound;',
            "const legacyBinding = directLegacy || legacyAlias;",
            "const localOk = record.local_dir === process.argv[4] || legacyAlias;",
            "const paths = record.paths;",
            'const pathsObject = paths !== null && typeof paths === "object" && !Array.isArray(paths);',
            "const pathlessLegacy = paths === undefined && record.local_dir === process.argv[10] && legacyBinding;",
            "const currentPaths = pathsObject && record.local_dir === process.argv[4] && paths.local_dir === process.argv[4] && paths.codex_home_dir === process.argv[6] && paths.launcher === process.argv[7] && paths.readme_local === process.argv[8];",
            "const legacyPaths = pathsObject && record.local_dir === process.argv[10] && paths.local_dir === process.argv[10] && paths.codex_home_dir === process.argv[11] && paths.launcher === process.argv[7] && paths.readme_local === process.argv[8] && legacyBinding;",
            "const pathsOk = pathlessLegacy || currentPaths || legacyPaths;",
            "const selectorChannel = inferChannel(record.codex_selector);",
            "const channelOk = record.codex_channel === null || channels.includes(record.codex_channel);",
            'const selectorChannelOk = selectorChannel !== null && (record.codex_selector === "latest" || record.codex_channel === null || record.codex_channel === selectorChannel);',
            "const catalog = record.available_versions;",
            'const catalogOk = catalog !== null && typeof catalog === "object" && !Array.isArray(catalog) && Object.entries(catalog).every(([name, value]) => channels.includes(name) && (value === null || isExact(value)));',
            'const updatedAtOk = record.available_versions_updated_at === undefined || record.available_versions_updated_at === null || typeof record.available_versions_updated_at === "string";',
            'const recordOk = Number.isInteger(record.schema_version) && record.schema_version === 1 && record.script_name === "codex-wrangler" && record.state === "complete" && typeof record.generated_at === "string" && record.generated_at.length > 0 && record.slot === process.argv[2] && record.project_root === process.argv[3] && localOk && pathsOk && typeof record.codex_selector === "string" && channelOk && selectorChannelOk && isExact(version) && typeof record.version_source === "string" && record.version_source.length > 0 && typeof record.shared_home === "boolean" && typeof record.reasonable_permissions_enabled === "boolean" && catalogOk && updatedAtOk;',
            "if (!recordOk) process.exit(1);",
            "const prefix = fs.realpathSync(path.dirname(process.argv[1]));",
            'const readEvidence = (relativePath) => { let candidate = prefix; for (let index = 0; index < relativePath.length; index += 1) { candidate = path.join(candidate, relativePath[index]); const status = fs.lstatSync(candidate); const finalComponent = index === relativePath.length - 1; if (status.isSymbolicLink() || (finalComponent ? !status.isFile() : !status.isDirectory())) process.exit(1); } const resolved = fs.realpathSync(candidate); if (!resolved.startsWith(prefix + path.sep)) process.exit(1); let descriptor; try { const flags = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0); descriptor = fs.openSync(candidate, flags); if (!fs.fstatSync(descriptor).isFile()) process.exit(1); return JSON.parse(fs.readFileSync(descriptor, "utf8")); } finally { if (typeof descriptor === "number") fs.closeSync(descriptor); } };',
            'const packageJson = readEvidence(["package.json"]);',
            'const lockJson = readEvidence(["package-lock.json"]);',
            'const installedJson = readEvidence(["node_modules", "@openai", "codex", "package.json"]);',
            'const evidenceOk = packageJson && packageJson.devDependencies && packageJson.devDependencies["@openai/codex"] === version && lockJson && lockJson.packages && lockJson.packages["node_modules/@openai/codex"] && lockJson.packages["node_modules/@openai/codex"].version === version && installedJson && installedJson.version === version;',
            "if (!evidenceOk) process.exit(1);",
            r'process.stdout.write([version, record.shared_home ? "1" : "0", record.reasonable_permissions_enabled ? "1" : "0"].join("\t"));',
        ]
    )


def build_launcher_preflight_lines() -> list[str]:
    """Return the shell preflight block embedded in the launcher."""

    repair_guidance = (
        'Run a known-good {} command with --repair \\"$repo_root\\".'
    ).format(SCRIPT_NAME)
    slot_record_validator = shlex.quote(build_launcher_slot_record_validator_script())
    return [
        'preflight_mode="${CODEX_LOCAL_PREFLIGHT:-warn}"',
        'preflight_prefix="[codex-local] preflight"',
        'local_codex_bin="$local_prefix/node_modules/.bin/codex"',
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
        'node_path="$(command -v node || true)"',
        'if [[ -z "$node_path" ]]; then',
        '  preflight_fail "Required node command is not available in PATH."',
        "fi",
        "",
        'if [[ ! -x "$local_codex_bin" ]]; then',
        '  preflight_fail "Local Codex executable is missing or not executable: $local_codex_bin. Re-run codex-wrangler install or upgrade for this project."',
        "fi",
        "",
        'canonical_local_root="$(node -e \'const fs = require("fs"); process.stdout.write(fs.realpathSync(process.argv[1]));\' "$local_root" 2>/dev/null || true)"',
        'canonical_local_prefix="$(node -e \'const fs = require("fs"); process.stdout.write(fs.realpathSync(process.argv[1]));\' "$local_prefix" 2>/dev/null || true)"',
        'if [[ -z "$canonical_local_root" || -z "$canonical_local_prefix" ]]; then',
        '  preflight_fail "Managed local root or selected prefix could not be resolved."',
        "fi",
        'if [[ "$canonical_local_prefix" != "$canonical_local_root" ]]; then',
        '  case "$canonical_local_prefix" in',
        '    "$canonical_local_root"/*) ;;',
        "    *)",
        '      preflight_fail "Selected Codex prefix resolves outside the managed local root."',
        "      ;;",
        "  esac",
        "fi",
        "",
        'slot_record_version=""',
        'if [[ -n "$selected_slot" ]]; then',
        '  slot_record="$local_prefix/.codex-wrangler-slot.json"',
        '  if [[ ! -f "$slot_record" || -L "$slot_record" ]]; then',
        '    preflight_fail "Active slot completion record is missing or linked: $slot_record"',
        "  fi",
        '  slot_record_payload="$(node -e {} "$slot_record" "$selected_slot" "$repo_root" "$local_root_relative" "$legacy_runtime_compat" "$codex_home_relative" "$launcher_relative" "$readme_relative" "$canonical_default_local_relative" "$legacy_default_local_relative" "$legacy_default_home_relative" "$canonical_default_home_relative" "$legacy_home_compat" "$legacy_home_pending" "$migration_bridge_enabled" 2>/dev/null || true)"'.format(
            slot_record_validator
        ),
        "  IFS=$'\\t' read -r slot_record_version effective_shared_home reasonable_permissions_enabled <<< \"$slot_record_payload\"",
        '  if [[ -z "$slot_record_version" ]]; then',
        '    preflight_fail "Active slot completion record is invalid: $slot_record"',
        "  fi",
        "fi",
        "",
        'if [[ "$effective_shared_home" == "1" ]]; then',
        "  # Preserve the caller's HOME and XDG selection in shared mode.",
        ":",
        "else",
        '  if [[ "$canonical_home_untrusted" == "1" ]]; then',
        '    preflight_fail "Canonical managed HOME is an untrusted symbolic link: $managed_home"',
        "  fi",
        '  managed_home_cursor="$repo_root"',
        "  IFS='/' read -r -a managed_home_components <<< \"$codex_home_relative\"",
        '  for managed_home_component in "${managed_home_components[@]}"; do',
        '    [[ -z "$managed_home_component" || "$managed_home_component" == "." ]] && continue',
        '    managed_home_cursor="$managed_home_cursor/$managed_home_component"',
        '    if [[ -L "$managed_home_cursor" ]]; then',
        '      preflight_fail "Managed HOME path may not contain symbolic links: $managed_home_cursor"',
        "    fi",
        '    if [[ -e "$managed_home_cursor" && ! -d "$managed_home_cursor" ]]; then',
        '      preflight_fail "Managed HOME path component is not a directory: $managed_home_cursor"',
        "    fi",
        "  done",
        '  managed_home="$managed_home_cursor"',
        '  export HOME="$managed_home"',
        '  export XDG_CONFIG_HOME="$HOME/.config"',
        '  export XDG_CACHE_HOME="$HOME/.cache"',
        '  export XDG_STATE_HOME="$HOME/.local/state"',
        '  export XDG_DATA_HOME="$HOME/.local/share"',
        "fi",
        'export CODEX_HOME="$HOME/.codex"',
        "",
        'canonical_local_codex_bin="$(node -e \'const fs = require("fs"); process.stdout.write(fs.realpathSync(process.argv[1]));\' "$local_codex_bin" 2>/dev/null || true)"',
        'case "$canonical_local_codex_bin" in',
        '  "$canonical_local_prefix"/*) ;;',
        "  *)",
        '    preflight_fail "Local Codex executable resolves outside the selected prefix: $local_codex_bin"',
        "    ;;",
        "esac",
        "",
        'local_codex_health_output=""',
        'local_codex_health_status="0"',
        'local_codex_health_output="$(node -e \'const child = require("child_process"); const result = child.spawnSync(process.argv[1], ["--version"], { encoding: "utf8", timeout: 30000 }); process.stdout.write((result.stdout || "") + (result.stderr || "")); if (result.error && result.error.code === "ETIMEDOUT") process.exit(124); process.exit(Number.isInteger(result.status) ? result.status : 1);\' "$local_codex_bin")" || local_codex_health_status="$?"',
        'if [[ "$local_codex_health_status" == "124" ]]; then',
        '  preflight_fail "Local Codex health check timed out after 30s. {}"'.format(
            repair_guidance
        ),
        'elif [[ "$local_codex_health_status" != "0" ]]; then',
        '  preflight_fail "Local Codex health check failed before launch. {}"'.format(
            repair_guidance
        ),
        "fi",
        'local_codex_health_matches="0"',
        'while IFS= read -r local_codex_health_line || [[ -n "$local_codex_health_line" ]]; do',
        "  local_codex_health_line=\"${local_codex_health_line%$'\\r'}\"",
        '  if [[ -n "$slot_record_version" ]]; then',
        '    if [[ "$local_codex_health_line" == "codex-cli $slot_record_version" ]]; then',
        '      local_codex_health_matches="$((local_codex_health_matches + 1))"',
        "    fi",
        '  elif [[ "$local_codex_health_line" =~ ^codex-cli[[:space:]]+[^[:space:]]+$ ]]; then',
        '    local_codex_health_matches="$((local_codex_health_matches + 1))"',
        "  fi",
        'done <<< "$local_codex_health_output"',
        'if [[ "$local_codex_health_matches" -ne 1 ]]; then',
        '  preflight_fail "Local Codex health check returned unexpected version output. {}"'.format(
            repair_guidance
        ),
        "fi",
        "",
        'if [[ "$preflight_mode" != "off" ]]; then',
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


def build_launcher_slot_selection_lines() -> list[str]:
    """Return strict active-pointer selection with a legacy fallback."""

    return [
        'local_root_cursor="$repo_root"',
        "IFS='/' read -r -a local_root_components <<< \"$local_root_relative\"",
        'for local_root_component in "${local_root_components[@]}"; do',
        '  [[ -z "$local_root_component" || "$local_root_component" == "." ]] && continue',
        '  local_root_cursor="$local_root_cursor/$local_root_component"',
        '  if [[ -L "$local_root_cursor" ]]; then',
        '    printf "%s\\n" "[codex-local] preflight: Managed local root path may not contain symbolic links: $local_root_cursor" >&2',
        "    exit 1",
        "  fi",
        "done",
        'if [[ -L "$local_root" ]]; then',
        '  printf "%s\\n" "[codex-local] preflight: Managed local root may not be a symbolic link: $local_root" >&2',
        "  exit 1",
        "fi",
        'if [[ -e "$local_root/slots" && ( -L "$local_root/slots" || ! -d "$local_root/slots" ) ]]; then',
        '  printf "%s\\n" "[codex-local] preflight: Managed slots path is not a real directory: $local_root/slots" >&2',
        "  exit 1",
        "fi",
        'active_link="$local_root/active"',
        'active_file="$local_root/active-slot"',
        'selected_slot=""',
        "active_link_present=0",
        "active_file_present=0",
        'if [[ -e "$active_link" || -L "$active_link" ]]; then active_link_present=1; fi',
        'if [[ -e "$active_file" || -L "$active_file" ]]; then active_file_present=1; fi',
        'if [[ "$active_link_present" == "1" && "$active_file_present" == "1" ]]; then',
        '  printf "%s\\n" "[codex-local] preflight: Both managed active pointer forms exist; refusing ambiguous state." >&2',
        "  exit 1",
        "fi",
        'if [[ "$active_link_present" == "1" ]]; then',
        '  if [[ ! -L "$active_link" ]]; then',
        '    printf "%s\\n" "[codex-local] preflight: Managed active pointer is not a symbolic link: $active_link" >&2',
        "    exit 1",
        "  fi",
        '  active_target="$(readlink "$active_link")"',
        '  case "$active_target" in',
        "    slots/a|slots/b) ;;",
        "    *)",
        '      printf "%s\\n" "[codex-local] preflight: Invalid managed active pointer target: $active_target" >&2',
        "      exit 1",
        "      ;;",
        "  esac",
        '  selected_slot="${active_target#slots/}"',
        '  local_prefix="$local_root/$active_target"',
        'elif [[ "$active_file_present" == "1" ]]; then',
        '  if [[ ! -f "$active_file" || -L "$active_file" || "$(wc -c < "$active_file")" -ne 2 ]]; then',
        '    printf "%s\\n" "[codex-local] preflight: Invalid managed active-slot file: $active_file" >&2',
        "    exit 1",
        "  fi",
        '  IFS= read -r active_slot < "$active_file"',
        '  case "$active_slot" in',
        "    a|b) ;;",
        "    *)",
        '      printf "%s\\n" "[codex-local] preflight: Invalid managed active slot: $active_slot" >&2',
        "      exit 1",
        "      ;;",
        "  esac",
        '  selected_slot="$active_slot"',
        '  local_prefix="$local_root/slots/$active_slot"',
        "else",
        "  # Legacy installs remain usable until the first validated slot promotion.",
        '  local_prefix="$local_root"',
        "fi",
        'if [[ -L "$local_prefix" ]]; then',
        '  printf "%s\\n" "[codex-local] preflight: Managed slot paths may not be symbolic links." >&2',
        "  exit 1",
        "fi",
        'if [[ -n "$selected_slot" && ! -d "$local_prefix" ]]; then',
        '  printf "%s\\n" "[codex-local] preflight: Active pointer selects a missing slot: $local_prefix" >&2',
        "  exit 1",
        "fi",
    ]


def build_launcher_update_notice_lines(layout: Layout) -> list[str]:
    """Return the shell block that prints a known-update notice before launch."""

    return [
        "emit_update_notice() {",
        "  node - \"$local_root/.codex-wrangler.json\" <<'NODE'",
        'const fs = require("fs");',
        "const metadataPath = process.argv[2];",
        "let metadata;",
        "let metadataDescriptor;",
        "try {",
        "  const pathStatus = fs.lstatSync(metadataPath);",
        "  if (!pathStatus.isFile() || pathStatus.isSymbolicLink()) throw new Error();",
        "  const openFlags = fs.constants.O_RDONLY | (fs.constants.O_NOFOLLOW || 0) | (fs.constants.O_NONBLOCK || 0);",
        "  metadataDescriptor = fs.openSync(metadataPath, openFlags);",
        "  if (!fs.fstatSync(metadataDescriptor).isFile()) throw new Error();",
        '  const metadataText = fs.readFileSync(metadataDescriptor, "utf8");',
        "  fs.closeSync(metadataDescriptor);",
        "  metadataDescriptor = undefined;",
        "  metadata = JSON.parse(metadataText);",
        "} catch (error) {",
        '  if (typeof metadataDescriptor === "number") {',
        "    try { fs.closeSync(metadataDescriptor); } catch (closeError) {}",
        "  }",
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


def build_launcher_reasonable_permissions_lines(config: Config) -> list[str]:
    """Return the shell block that injects managed approval defaults."""

    return [
        "default_codex_args=()",
        "",
        'if [[ "$reasonable_permissions_enabled" == "1" ]]; then',
        "  explicit_permission_choice=0",
        '  for arg in "$@"; do',
        '    case "$arg" in',
        "      -a|--ask-for-approval|-s|--sandbox|--full-auto|--dangerously-bypass-approvals-and-sandbox)",
        "        explicit_permission_choice=1",
        "        ;;",
        "      --ask-for-approval=*|--sandbox=*)",
        "        explicit_permission_choice=1",
        "        ;;",
        "    esac",
        '    if [[ "$explicit_permission_choice" == "1" ]]; then',
        "      break",
        "    fi",
        "  done",
        '  if [[ "$explicit_permission_choice" == "0" ]]; then',
        '    default_codex_args+=("-a" "on-request" "-s" "workspace-write")',
        "  fi",
        "fi",
    ]


def build_launcher_content(config: Config) -> str:
    """Build the generated shell launcher."""

    launcher_parent = Path(config.layout.launcher_relative).parent
    launcher_parent_parts = [
        part for part in launcher_parent.parts if part not in ("", ".")
    ]
    root_hops = "/".join(".." for _ in launcher_parent_parts) or "."
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# {}".format(SCRIPT_MARKER),
        "# Generated by {}".format(SCRIPT_NAME),
        "#",
        "# This launcher keeps the Codex npm package local to the repository by",
        "# executing the managed local Codex binary directly. It must not fall",
        "# back to a registry package named codex.",
        "#",
        "# In default mode it also redirects HOME into the managed project Codex",
        "# home directory so auth, sessions, logs, and other Codex state remain",
        "# isolated per project.",
        "",
        'repo_root="$(cd "$(dirname "${{BASH_SOURCE[0]}}")/{}" && pwd -P)"'.format(
            root_hops
        ),
        "local_root_relative={}".format(shlex.quote(config.layout.local_dir_relative)),
        "codex_home_relative={}".format(shlex.quote(config.layout.codex_home_relative)),
        "launcher_relative={}".format(shlex.quote(config.layout.launcher_relative)),
        "readme_relative={}".format(shlex.quote(config.layout.readme_relative)),
        "canonical_default_local_relative={}".format(shlex.quote(DEFAULT_LOCAL_DIR)),
        "canonical_default_home_relative={}".format(shlex.quote(DEFAULT_HOME_DIR)),
        "legacy_default_local_relative={}".format(shlex.quote(LEGACY_LOCAL_DIR)),
        "legacy_default_home_relative={}".format(shlex.quote(LEGACY_HOME_DIR)),
        'local_root="$repo_root/$local_root_relative"',
        "legacy_runtime_compat=0",
        "legacy_home_compat=0",
        "legacy_home_pending=0",
        "migration_bridge_enabled={}".format(
            "1" if config.layout_migration is not None else "0"
        ),
        'effective_shared_home="{}"'.format("1" if config.shared_home else "0"),
        'reasonable_permissions_enabled="{}"'.format(
            "1" if config.reasonable_permissions_enabled else "0"
        ),
        "canonical_home_untrusted=0",
    ]
    if config.layout.local_dir_relative == DEFAULT_LOCAL_DIR:
        lines.extend(
            [
                'legacy_local_root="$repo_root/{}"'.format(LEGACY_LOCAL_DIR),
                "canonical_local_root_staged=0",
                'if [[ -L "$local_root" ]]; then',
                '  if [[ "$migration_bridge_enabled" == "1" && "$(readlink "$local_root")" == "{}" && -d "$legacy_local_root" && ! -L "$legacy_local_root" ]]; then'.format(
                    DEFAULT_LOCAL_DIR
                ),
                "    canonical_local_root_staged=1",
                "  else",
                '    printf "%s\n" "[codex-local] preflight: Canonical managed runtime is an untrusted symbolic link: $local_root" >&2',
                "    exit 1",
                "  fi",
                "fi",
                'if [[ "$migration_bridge_enabled" == "1" ]] && { [[ ! -e "$local_root" && ! -L "$local_root" ]] || [[ "$canonical_local_root_staged" == "1" ]]; } && [[ -d "$legacy_local_root" && ! -L "$legacy_local_root" ]]; then',
                "  local_root_relative={}".format(shlex.quote(LEGACY_LOCAL_DIR)),
                '  local_root="$legacy_local_root"',
                "fi",
                'if [[ "$local_root_relative" == "{}" && -d "$local_root" && ! -L "$local_root" && -L "$legacy_local_root" && "$(readlink "$legacy_local_root")" == "{}" ]]; then'.format(
                    DEFAULT_LOCAL_DIR,
                    DEFAULT_LOCAL_DIR,
                ),
                "  legacy_runtime_compat=1",
                "fi",
            ]
        )
    lines.extend(
        [
            'export NPM_CONFIG_CACHE="$local_root/.npm-cache"',
            'export CODEX_LOCAL_MANAGED_BY="{}"'.format(SCRIPT_NAME),
        ]
    )

    if config.layout.codex_home_relative == DEFAULT_HOME_DIR:
        lines.extend(
            [
                'managed_home="$repo_root/{}"'.format(DEFAULT_HOME_DIR),
                'legacy_managed_home="$repo_root/{}"'.format(LEGACY_HOME_DIR),
                "canonical_home_staged=0",
                'if [[ -L "$managed_home" ]]; then',
                '  if [[ "$migration_bridge_enabled" == "1" && "{}" == "0" && "$(readlink "$managed_home")" == "{}" && -d "$legacy_managed_home" && ! -L "$legacy_managed_home" ]]; then'.format(
                    "1" if config.shared_home else "0",
                    DEFAULT_HOME_DIR,
                ),
                "    canonical_home_staged=1",
                "  else",
                "    canonical_home_untrusted=1",
                "  fi",
                "fi",
            ]
        )
        if not config.shared_home:
            lines.extend(
                [
                    'if [[ "$migration_bridge_enabled" == "1" ]] && { [[ ! -e "$managed_home" && ! -L "$managed_home" ]] || [[ "$canonical_home_staged" == "1" ]]; } && [[ -d "$legacy_managed_home" && ! -L "$legacy_managed_home" ]]; then',
                    "  codex_home_relative={}".format(shlex.quote(LEGACY_HOME_DIR)),
                    '  managed_home="$legacy_managed_home"',
                    "  legacy_home_pending=1",
                    "fi",
                ]
            )
        lines.extend(
            [
                'if [[ "$codex_home_relative" == "{}" && -d "$managed_home" && ! -L "$managed_home" && -L "$legacy_managed_home" && "$(readlink "$legacy_managed_home")" == "{}" ]]; then'.format(
                    DEFAULT_HOME_DIR,
                    DEFAULT_HOME_DIR,
                ),
                "  legacy_home_compat=1",
                "fi",
            ]
        )
    else:
        lines.append('managed_home="$repo_root/$codex_home_relative"')

    lines.extend([""])
    lines.extend(build_launcher_slot_selection_lines())
    lines.extend([""])
    lines.extend(build_launcher_preflight_lines())
    lines.extend([""])
    lines.extend(build_launcher_update_notice_lines(config.layout))
    lines.extend([""])
    lines.extend(build_launcher_reasonable_permissions_lines(config))
    lines.extend(
        [
            "",
            'exec "$local_codex_bin" "${default_codex_args[@]}" "$@"',
            "",
        ]
    )
    return "\n".join(lines)


def build_local_readme_content(config: Config) -> str:
    """Build the generated project-local operator README."""

    audit_prefix = config.layout.local_dir_relative
    if config.active_slot:
        audit_prefix = "{}/slots/{}".format(audit_prefix, config.active_slot)
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
        "- Reasonable permissions default: `{}`".format(
            "enabled" if config.reasonable_permissions_enabled else "disabled"
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
        "## Managed Approval Default",
        "",
        "The launcher can store one opt-in reasonable-permissions default.",
        "Current state: `{}`.".format(
            "enabled" if config.reasonable_permissions_enabled else "disabled"
        ),
        "",
        "When enabled, the wrapper adds `-a on-request` and",
        "`-s workspace-write` only when you did not already provide an",
        "explicit approval or sandbox choice. This keeps ordinary",
        "workspace edits flowing while allowing Codex to request",
        "escalation for ACP operations such as commit and push.",
        "Operators can approve local add and commit work as",
        "maintenance activity while treating push as the explicit",
        "remote publication step.",
        "",
        "Explicit `-a`, `--ask-for-approval`, `-s`, `--sandbox`,",
        "`--full-auto`, and",
        "`--dangerously-bypass-approvals-and-sandbox` always win.",
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
        "NPM_CONFIG_CACHE=./{}/.npm-cache npm audit --prefix ./{}".format(
            config.layout.local_dir_relative,
            audit_prefix,
        ),
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
        "## Repair The Managed Install",
        "",
        "If the local Codex executable or npm package tree is damaged, run a",
        "known-good `{}` command from any other directory. Repair infers the".format(
            SCRIPT_NAME
        ),
        "surviving exact Codex version, rebuilds only managed npm artifacts,",
        "and preserves project-local context and history under `{}`.".format(
            config.layout.codex_home_relative
        ),
        "",
        "```bash",
        "{} --repair {}".format(
            SCRIPT_NAME,
            shlex.quote(str(config.project_root)),
        ),
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
        "## Toggle Managed Approval Defaults",
        "",
        "```bash",
        "{} --set-reasonable-permissions .".format(SCRIPT_NAME),
        "{} --clear-reasonable-permissions .".format(SCRIPT_NAME),
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

    audit_prefix = config.layout.local_dir
    if config.active_slot:
        audit_prefix = config.layout.local_dir / "slots" / config.active_slot
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
        "Reasonable permissions default: {}".format(
            "enabled" if config.reasonable_permissions_enabled else "disabled"
        ),
        "HOME mode: {}".format(
            "shared user HOME" if config.shared_home else "project-local isolated HOME"
        ),
        "Package install: {}".format(
            "unchanged existing managed install"
            if config.reconfigure_only
            else (
                "skipped by request"
                if config.skip_install
                else (
                    "inactive-slot repair install required"
                    if config.repair_install and config.dry_run
                    else (
                        "inactive-slot repair install completed"
                        if config.repair_install
                        else (
                            "inactive-slot npm install required"
                            if config.dry_run
                            else "inactive-slot npm install completed"
                        )
                    )
                )
            )
        ),
        "",
        "Recommended usage:",
        "  {}".format(config.layout.launcher_path),
        "  {} resume".format(config.layout.launcher_path),
        "  {} resume --last".format(config.layout.launcher_path),
        "",
        "Audit command:",
        "  NPM_CONFIG_CACHE={}/.npm-cache npm audit --prefix {}".format(
            config.layout.local_dir,
            audit_prefix,
        ),
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
