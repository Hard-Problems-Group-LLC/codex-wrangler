import pytest

from codex_wrangler.filesystem import (
    remove_file_if_managed,
    remove_gitignore_block,
    remove_tree,
    upsert_gitignore_block,
)
from codex_wrangler.layout import build_layout
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.rendering import build_gitignore_block


def test_upsert_gitignore_block_replaces_managed_block_without_trailing_newline(
    tmp_path,
):
    layout = build_layout(
        tmp_path,
        ".codex-local",
        ".codex-home",
        "bin/codex-local",
        "README-LOCAL-Start-Codex.md",
    )
    gitignore_path = tmp_path / ".gitignore"
    gitignore_path.write_text(
        "keep-me\n# BEGIN managed by codex-wrangler\nold\n# END managed by codex-wrangler",
        encoding="utf-8",
    )

    upsert_gitignore_block(
        gitignore_path,
        build_gitignore_block(layout),
        dry_run=False,
    )

    text = gitignore_path.read_text(encoding="utf-8")
    assert text.count("# BEGIN managed by codex-wrangler") == 1
    assert "README-LOCAL-Start-Codex.md" in text
    assert ".codex" in text
    assert "bin/codex-local" in text


def test_upsert_gitignore_block_creates_missing_file(tmp_path):
    layout = build_layout(
        tmp_path,
        ".codex-local",
        ".codex-home",
        "bin/codex-local",
        "README-LOCAL-Start-Codex.md",
    )
    gitignore_path = tmp_path / ".gitignore"

    upsert_gitignore_block(
        gitignore_path,
        build_gitignore_block(layout),
        dry_run=False,
    )

    text = gitignore_path.read_text(encoding="utf-8")
    assert text.startswith("# BEGIN managed by codex-wrangler\n")
    assert "# Local Codex package, home, wrapper, and sentinel artifacts." in text
    assert ".codex\n" in text
    assert "bin/codex-local\n" in text


def test_upsert_gitignore_block_appends_rules_even_when_rules_exist_elsewhere(
    tmp_path,
):
    layout = build_layout(
        tmp_path,
        ".codex-local",
        ".codex-home",
        "bin/codex-local",
        "README-LOCAL-Start-Codex.md",
    )
    gitignore_path = tmp_path / ".gitignore"
    gitignore_path.write_text(".codex\nbin/codex-local\n", encoding="utf-8")

    upsert_gitignore_block(
        gitignore_path,
        build_gitignore_block(layout),
        dry_run=False,
    )

    text = gitignore_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines.count("# BEGIN managed by codex-wrangler") == 1
    assert lines.count(".codex") == 2
    assert lines.count("bin/codex-local") == 2


def test_remove_gitignore_block_handles_eof_without_trailing_newline(tmp_path):
    gitignore_path = tmp_path / ".gitignore"
    gitignore_path.write_text(
        "keep-me\n# BEGIN managed by codex-wrangler\nold\n# END managed by codex-wrangler",
        encoding="utf-8",
    )

    removed = remove_gitignore_block(gitignore_path, dry_run=False)

    assert removed is True
    assert gitignore_path.read_text(encoding="utf-8") == "keep-me\n"


def test_remove_file_if_managed_refuses_directory_even_with_force(tmp_path):
    managed_path = tmp_path / "bin" / "codex-local"
    managed_path.mkdir(parents=True)

    with pytest.raises(CodexWranglerError):
        remove_file_if_managed(
            managed_path,
            expected_content="ignored",
            force=True,
            dry_run=False,
            label="launcher",
        )


def test_remove_file_if_managed_refuses_unmanaged_content_without_force(tmp_path):
    managed_path = tmp_path / "README-LOCAL-Start-Codex.md"
    managed_path.write_text("user content\n", encoding="utf-8")

    with pytest.raises(CodexWranglerError):
        remove_file_if_managed(
            managed_path,
            expected_content="generated content\n",
            force=False,
            dry_run=False,
            label="local README",
        )


def test_remove_tree_refuses_file_path(tmp_path):
    managed_path = tmp_path / ".codex-local" / "node_modules"
    managed_path.parent.mkdir()
    managed_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(CodexWranglerError, match="not a directory"):
        remove_tree(
            managed_path,
            label="managed local node_modules",
            project_root=tmp_path,
            dry_run=False,
        )


def test_remove_tree_refuses_symlink_path(tmp_path):
    target_path = tmp_path / "target"
    target_path.mkdir()
    managed_path = tmp_path / ".codex-local" / "node_modules"
    managed_path.parent.mkdir()
    managed_path.symlink_to(target_path, target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="symbolic link"):
        remove_tree(
            managed_path,
            label="managed local node_modules",
            project_root=tmp_path,
            dry_run=False,
        )


def test_remove_tree_refuses_broken_symlink_path(tmp_path):
    managed_path = tmp_path / ".codex-local" / "node_modules"
    managed_path.parent.mkdir()
    managed_path.symlink_to(tmp_path / "missing", target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="symbolic link"):
        remove_tree(
            managed_path,
            label="managed local node_modules",
            project_root=tmp_path,
            dry_run=False,
        )
