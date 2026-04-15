from codex_wrangler import __version__
from codex_wrangler.rendering import (
    build_available_versions_table,
    build_gitignore_block,
    build_launcher_content,
    build_local_readme_content,
    build_metadata,
)


def test_build_gitignore_block_includes_local_readme(tmp_path, config_factory):
    config = config_factory(tmp_path)
    block = build_gitignore_block(config.layout)
    assert "README-LOCAL-Start-Codex.md" in block


def test_build_local_readme_references_console_command(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    readme = build_local_readme_content(config)
    assert "codex-wrangler --inspect ." in readme
    assert "codex-wrangler --selftest ." in readme
    assert "~/bin" not in readme


def test_build_metadata_uses_single_source_script_version(
    tmp_path,
    config_factory,
):
    config = config_factory(tmp_path)
    metadata = build_metadata(config)
    assert metadata["script_version"] == __version__


def test_build_launcher_content_includes_runtime_preflight(tmp_path, config_factory):
    config = config_factory(tmp_path)

    launcher = build_launcher_content(config)

    assert "CODEX_LOCAL_PREFLIGHT:-warn" in launcher
    assert "command -v node" in launcher
    assert ".nvmrc" in launcher
    assert "handle_preflight_mismatch" in launcher
    assert "emit_update_notice" in launcher
    assert "available update on ${channel}" in launcher


def test_build_available_versions_table_lists_supported_channels():
    table = build_available_versions_table(
        {
            "stable": "0.30.0",
            "beta": "0.31.0-beta.2",
            "alpha": None,
        }
    )

    assert "Channel" in table
    assert "stable" in table
    assert "beta" in table
    assert "alpha" in table
    assert "unavailable" in table
