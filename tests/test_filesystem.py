import pytest

from codex_wrangler.filesystem import (
    atomic_write_text,
    fsync_directory,
    remove_file_if_managed,
    remove_gitignore_block,
    remove_tree,
    upsert_gitignore_block,
)
from codex_wrangler.layout import build_layout
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.rendering import build_gitignore_block


def test_atomic_write_failure_preserves_existing_file(monkeypatch, tmp_path):
    path = tmp_path / "managed.txt"
    path.write_text("old\n", encoding="utf-8")

    def fail_replace(_source, _destination):
        raise OSError("simulated replacement failure")

    monkeypatch.setattr("codex_wrangler.filesystem.os.replace", fail_replace)

    with pytest.raises(CodexWranglerError, match="atomically replace"):
        atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "old\n"
    assert not list(tmp_path.glob(".managed.txt.*.tmp"))


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
    assert ".local/" in text
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
    assert ".local/\n" in text
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


def test_remove_tree_refuses_path_reached_through_symlinked_parent(tmp_path):
    """A linked ancestor cannot redirect cleanup into protected project data."""

    protected_tree = tmp_path / ".local" / "codex-home" / "cache-target"
    protected_tree.mkdir(parents=True)
    sentinel = protected_tree / "session-history"
    sentinel.write_text("preserve\n", encoding="utf-8")
    cache_root = tmp_path / ".local" / "codex" / ".npm-cache"
    cache_root.mkdir(parents=True)
    (cache_root / "_cacache").symlink_to(
        tmp_path / ".local" / "codex-home",
        target_is_directory=True,
    )

    with pytest.raises(CodexWranglerError, match="symbolic link component"):
        remove_tree(
            cache_root / "_cacache" / "cache-target",
            label="managed local npm cache temp files",
            project_root=tmp_path,
            dry_run=False,
        )

    assert sentinel.read_text(encoding="utf-8") == "preserve\n"


def test_remove_tree_flushes_parent_after_directory_entry_removal(
    monkeypatch,
    tmp_path,
):
    """Ignore narrowing can rely on the removed tree entry being durable."""

    managed_path = tmp_path / ".local" / "codex"
    managed_path.mkdir(parents=True)
    events = []
    real_rmtree = __import__("shutil").rmtree

    def record_rmtree(target):
        events.append(("rmtree", target))
        real_rmtree(target)

    monkeypatch.setattr("codex_wrangler.filesystem.shutil.rmtree", record_rmtree)
    monkeypatch.setattr(
        "codex_wrangler.filesystem.fsync_directory",
        lambda target: events.append(("fsync", target)),
    )

    remove_tree(
        managed_path,
        label="managed local directory",
        project_root=tmp_path,
        dry_run=False,
    )

    assert events == [
        ("rmtree", managed_path),
        ("fsync", managed_path.parent),
    ]


def test_atomic_write_applies_mode_before_flushing_file(monkeypatch, tmp_path):
    """Executable mode is part of the file state made durable before replace."""

    path = tmp_path / "managed.sh"
    events = []
    real_chmod = __import__("os").chmod
    real_replace = __import__("os").replace

    def record_chmod(target, mode):
        events.append("chmod")
        real_chmod(target, mode)

    def record_fsync(_descriptor):
        events.append("file-fsync")

    def record_replace(source, destination):
        events.append("replace")
        real_replace(source, destination)

    monkeypatch.setattr("codex_wrangler.filesystem.os.chmod", record_chmod)
    monkeypatch.setattr("codex_wrangler.filesystem.os.fsync", record_fsync)
    monkeypatch.setattr("codex_wrangler.filesystem.os.replace", record_replace)
    monkeypatch.setattr(
        "codex_wrangler.filesystem.fsync_directory",
        lambda _path: events.append("directory-fsync"),
    )

    atomic_write_text(path, "#!/bin/sh\n", executable=True)

    assert events == ["chmod", "file-fsync", "replace", "directory-fsync"]


def test_atomic_write_reports_post_replace_directory_flush_failure(
    monkeypatch,
    tmp_path,
):
    """A visible new projection is not mislabeled as a failed replacement."""

    path = tmp_path / "managed.txt"
    path.write_text("old\n", encoding="utf-8")
    monkeypatch.setattr(
        "codex_wrangler.filesystem.fsync_directory",
        lambda _path: (_ for _ in ()).throw(OSError("directory flush failed")),
    )

    with pytest.raises(CodexWranglerError, match="new file is visible"):
        atomic_write_text(path, "new\n")

    assert path.read_text(encoding="utf-8") == "new\n"


def test_fsync_directory_propagates_posix_open_failure(monkeypatch, tmp_path):
    """POSIX durability failures cannot be silently treated as unsupported."""

    monkeypatch.setattr(
        "codex_wrangler.filesystem.os.open",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("open failed")),
    )

    with pytest.raises(OSError, match="open failed"):
        fsync_directory(tmp_path)


def test_build_layout_rejects_existing_symlink_component(tmp_path):
    """Lexical link evidence is checked before canonical resolution erases it."""

    outside = tmp_path / "outside"
    outside.mkdir()
    (tmp_path / "managed").symlink_to(outside, target_is_directory=True)

    with pytest.raises(CodexWranglerError, match="symbolic-link components"):
        build_layout(
            tmp_path,
            "managed/.codex-local",
            ".codex-home",
            "bin/codex-local",
            "README-LOCAL-Start-Codex.md",
        )
