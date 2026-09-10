from pathlib import Path
import importlib.util
import io
import os
import subprocess
from types import SimpleNamespace

from scripts.python_environment_bootstrap import (
    PythonContextConfig,
    context_requires_virtualenv,
    DIRENV_BEGIN,
    DIRENV_END,
    ENVRC_MARKER,
    build_direnv_hook_block,
    build_envrc_content,
    detect_shell_name,
    direnv_download_name,
    ensure_pyenv_installed,
    ensure_pyenv_virtualenv_plugin,
    load_python_environment_config,
    selection_name,
    shell_rc_path,
    upsert_managed_block,
)

ROOT = Path(__file__).resolve().parents[1]


def load_install_stage_2_module():
    module_path = ROOT / "scripts" / "install-stage-2.py"
    spec = importlib.util.spec_from_file_location("install_stage_2", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def run_fixture_git(repo_root, *args):
    """Run deterministic local Git commands for bootstrap integration fixtures."""

    return subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "-c",
            "user.name=Installer Test",
            "-c",
            "user.email=installer-test@example.invalid",
            *args,
        ],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )


def test_load_python_environment_config_reads_runtime_selection():
    config = load_python_environment_config(ROOT)

    assert config.bootstrap.required_version == "3.9"
    assert config.runtime.environment_name == "3.14.6"
    assert not context_requires_virtualenv(config.runtime)
    assert selection_name(config.runtime) == "3.14.6"


def test_existing_pyenv_checkout_is_reused_without_vcs_mutation(tmp_path):
    pyenv_root = tmp_path / ".pyenv"
    executable = pyenv_root / "bin" / "pyenv"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\n", encoding="utf-8")
    executable.chmod(0o755)

    def unexpected_runner(command):
        raise AssertionError("existing pyenv checkout must not be mutated")

    assert ensure_pyenv_installed(pyenv_root, unexpected_runner) == executable


def test_existing_pyenv_plugin_is_reused_without_vcs_mutation(tmp_path):
    pyenv_root = tmp_path / ".pyenv"
    plugin_root = pyenv_root / "plugins" / "pyenv-virtualenv"
    plugin_root.mkdir(parents=True)

    def unexpected_runner(command):
        raise AssertionError("existing pyenv plugin must not be mutated")

    assert ensure_pyenv_virtualenv_plugin(pyenv_root, unexpected_runner) == plugin_root


def test_runtime_context_setup_does_not_install_bootstrap_floor(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    pyenv_root = tmp_path / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    bootstrap = PythonContextConfig("3.9", "3.9.21", "3.9.21")
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=bootstrap, runtime=runtime)
    installed_contexts = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)

    def fake_ensure_context(root, context, runner):
        installed_contexts.append(context)
        return context.environment_name

    monkeypatch.setattr(module, "ensure_pyenv_context", fake_ensure_context)

    result = module.ensure_runtime_contexts(tmp_path)

    assert result == (pyenv_root, "3.14.6", runtime_python)
    assert installed_contexts == [runtime]
    assert (repo_root / ".python-version").read_text(encoding="utf-8") == "3.14.6\n"


def test_runtime_context_runner_reasserts_selected_home_over_pyenv_environment(
    monkeypatch,
    tmp_path,
):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    selected_home = tmp_path / "operator"
    pyenv_root = selected_home / ".pyenv"
    runtime_python = pyenv_root / "versions" / "3.14.6" / "bin" / "python"
    runtime_python.parent.mkdir(parents=True)
    runtime_python.write_text("", encoding="utf-8")
    repo_root.mkdir()
    runtime = PythonContextConfig("3.12", "3.14.6", "3.14.6")
    config = SimpleNamespace(bootstrap=runtime, runtime=runtime)
    observed = {}
    selected_environment = module.user_scope_subprocess_environment(
        selected_home,
        {
            "HOME": str(tmp_path / "caller" / ".codex-home"),
            "CODEX_HOME": str(tmp_path / "caller" / ".codex-home" / ".codex"),
            "XDG_RUNTIME_DIR": str(tmp_path / "caller" / "runtime"),
            "PATH": "/usr/bin",
        },
    )

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module, "load_python_environment_config", lambda selected_root: config
    )
    monkeypatch.setattr(
        module, "default_install_pyenv_root", lambda user_home: pyenv_root
    )
    monkeypatch.setattr(module, "ensure_pyenv_installed", lambda root, runner: None)

    def fake_ensure_context(root, context, runner):
        runner(
            ["pyenv", "install"],
            env={
                "HOME": str(tmp_path / "caller" / ".codex-home"),
                "CODEX_HOME": str(tmp_path / "caller" / ".codex-home" / ".codex"),
                "XDG_CONFIG_HOME": str(tmp_path / "caller" / "config"),
                "XDG_RUNTIME_DIR": str(tmp_path / "caller" / "runtime"),
                "PIP_CACHE_DIR": str(tmp_path / "caller" / "pip"),
                "PYENV_ROOT": str(pyenv_root),
                "PATH": str(pyenv_root / "bin") + os.pathsep + "/usr/bin",
            },
        )
        return context.environment_name

    def fake_run(command, cwd=None, env=None, capture_output=False):
        observed.update(env)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(module, "ensure_pyenv_context", fake_ensure_context)
    monkeypatch.setattr(module, "run", fake_run)

    result = module.ensure_runtime_contexts(
        selected_home,
        env=selected_environment,
    )

    assert result == (pyenv_root, "3.14.6", runtime_python)
    assert observed["HOME"] == str(selected_home.resolve())
    assert observed["XDG_CONFIG_HOME"] == str(selected_home / ".config")
    assert observed["PIP_CACHE_DIR"] == str(selected_home / ".cache" / "pip")
    assert observed["PYENV_ROOT"] == str(pyenv_root)
    assert observed["PATH"].startswith(str(pyenv_root / "bin") + os.pathsep)
    assert "CODEX_HOME" not in observed
    assert "CLAUDE_CONFIG_DIR" not in observed
    assert "XDG_RUNTIME_DIR" not in observed


def test_standard_user_install_threads_selected_home_environment(
    monkeypatch,
    tmp_path,
):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    operator_home = tmp_path / "operator"
    venv_path = operator_home / ".local" / "share" / "tool" / "venv"
    venv_python = venv_path / "bin" / "python"
    captured = {}
    repo_root.mkdir()

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "ensure_submodules",
        lambda skip, env=None: captured.setdefault("submodule_env", env),
    )
    monkeypatch.setattr(
        module,
        "launcher_dir_for_scope",
        lambda scope, user_home: operator_home / ".local" / "bin",
    )

    def fake_ensure_venv(scope, user_home, env=None):
        captured["venv_env"] = env
        return venv_path, venv_python, Path("/usr/bin/python")

    def fake_install_project(
        candidate_python,
        candidate_venv,
        mode,
        scope,
        bin_dir,
        env=None,
    ):
        captured["project_env"] = env

    def fake_verify(candidate_python, env=None):
        captured["verify_env"] = env

    monkeypatch.setattr(module, "ensure_standard_install_venv", fake_ensure_venv)
    monkeypatch.setattr(module, "install_project", fake_install_project)
    monkeypatch.setattr(module, "verify_install", fake_verify)

    result = module.main(
        [
            "--force-direct-run",
            "--skip-submodule-init",
            "--user-home",
            str(operator_home),
        ]
    )

    assert result == 0
    for environment in captured.values():
        assert environment["HOME"] == str(operator_home.resolve())
        assert environment["XDG_CACHE_HOME"] == str(operator_home / ".cache")
        assert environment["PIP_CACHE_DIR"] == str(operator_home / ".cache" / "pip")


def test_repo_scope_install_uses_selected_home_for_subprocesses(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    operator_home = tmp_path / "operator"
    runtime_python = operator_home / ".pyenv" / "versions" / "runtime" / "python"
    venv_python = repo_root / ".venv" / "bin" / "python"
    captured = {}
    repo_root.mkdir()

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "ensure_submodules",
        lambda skip, env=None: captured.setdefault("submodule_env", env),
    )
    monkeypatch.setattr(
        module,
        "launcher_dir_for_scope",
        lambda scope, user_home: operator_home / ".local" / "bin",
    )

    def fake_runtime_contexts(user_home, env=None):
        captured["runtime_env"] = env
        return operator_home / ".pyenv", "runtime", runtime_python

    def fake_repo_venv(candidate_python, env=None):
        captured["venv_env"] = env
        return venv_python

    def fake_install_project(*args, env=None):
        captured["project_env"] = env

    def fake_install_hooks(candidate_python, env=None):
        captured["hooks_env"] = env

    def fake_verify(candidate_python, env=None):
        captured["verify_env"] = env

    monkeypatch.setattr(module, "ensure_runtime_contexts", fake_runtime_contexts)
    monkeypatch.setattr(module, "ensure_repo_venv", fake_repo_venv)
    monkeypatch.setattr(module, "install_project", fake_install_project)
    monkeypatch.setattr(module, "install_git_hooks", fake_install_hooks)
    monkeypatch.setattr(module, "verify_install", fake_verify)

    result = module.main(
        [
            "--force-direct-run",
            "--skip-submodule-init",
            "--skip-shell-init-update",
            "--mode",
            "venv-only",
            "--user-home",
            str(operator_home),
        ]
    )

    assert result == 0
    for environment in captured.values():
        assert environment["HOME"] == str(operator_home.resolve())
        assert environment["PIP_CACHE_DIR"] == str(operator_home / ".cache" / "pip")


def test_submodule_init_preserves_advanced_checkout_and_initializes_missing_tree(
    monkeypatch,
    tmp_path,
):
    module = load_install_stage_2_module()
    leaf = tmp_path / "leaf"
    nested = tmp_path / "nested"
    container = tmp_path / "container"
    project = tmp_path / "project"
    for repo_root in (leaf, nested, container, project):
        repo_root.mkdir()
        run_fixture_git(repo_root, "init", "--quiet")

    (leaf / "version.txt").write_text("recorded\n", encoding="utf-8")
    run_fixture_git(leaf, "add", "version.txt")
    run_fixture_git(leaf, "commit", "--quiet", "-m", "recorded leaf")
    recorded_leaf = run_fixture_git(leaf, "rev-parse", "HEAD").stdout.strip()
    (leaf / "version.txt").write_text("advanced\n", encoding="utf-8")
    run_fixture_git(leaf, "commit", "--quiet", "-am", "advanced leaf")
    advanced_leaf = run_fixture_git(leaf, "rev-parse", "HEAD").stdout.strip()

    (nested / "nested.txt").write_text("nested\n", encoding="utf-8")
    run_fixture_git(nested, "add", "nested.txt")
    run_fixture_git(nested, "commit", "--quiet", "-m", "nested content")

    run_fixture_git(container, "submodule", "add", "--quiet", str(nested), "nested")
    run_fixture_git(container, "commit", "--quiet", "-am", "add nested module")

    run_fixture_git(project, "submodule", "add", "--quiet", str(leaf), "advanced")
    run_fixture_git(project / "advanced", "checkout", "--quiet", recorded_leaf)
    run_fixture_git(
        project,
        "submodule",
        "add",
        "--quiet",
        str(container),
        "missing",
    )
    run_fixture_git(project, "commit", "--quiet", "-am", "record submodules")
    run_fixture_git(project / "advanced", "checkout", "--quiet", advanced_leaf)
    run_fixture_git(project, "submodule", "deinit", "--force", "--", "missing")

    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setattr(module, "REPO_ROOT", project)

    module.ensure_submodules(skip=False)

    assert (
        run_fixture_git(project / "advanced", "rev-parse", "HEAD").stdout.strip()
        == advanced_leaf
    )
    assert (project / "missing" / "nested" / "nested.txt").read_text(
        encoding="utf-8"
    ) == "nested\n"


def build_submodule_replacement_fixture(tmp_path, relative_path):
    """Build a project plus an external repo with a nested submodule."""

    payload = tmp_path / "payload"
    external_repo = tmp_path / "outside" / "managed"
    project = tmp_path / "project"
    external_repo.parent.mkdir()
    for repo_root in (payload, external_repo, project):
        repo_root.mkdir()
        run_fixture_git(repo_root, "init", "--quiet")

    (payload / "payload.txt").write_text("payload\n", encoding="utf-8")
    run_fixture_git(payload, "add", "payload.txt")
    run_fixture_git(payload, "commit", "--quiet", "-m", "payload")

    run_fixture_git(
        external_repo,
        "submodule",
        "add",
        "--quiet",
        str(payload),
        "nested",
    )
    run_fixture_git(
        external_repo,
        "commit",
        "--quiet",
        "-am",
        "add nested module",
    )
    run_fixture_git(
        external_repo,
        "submodule",
        "deinit",
        "--force",
        "--",
        "nested",
    )

    run_fixture_git(
        project,
        "submodule",
        "add",
        "--quiet",
        str(external_repo),
        str(relative_path),
    )
    run_fixture_git(
        project,
        "commit",
        "--quiet",
        "-am",
        "add managed module",
    )
    run_fixture_git(
        project,
        "submodule",
        "deinit",
        "--force",
        "--",
        str(relative_path),
    )
    checkout_path = project / relative_path
    checkout_path.rmdir()
    return project, external_repo


def assert_submodule_replacement_is_refused(
    module,
    monkeypatch,
    project,
    external_repo,
    replaced_path,
):
    """Prove one path replacement cannot trigger work in an external repo."""

    external_commands = []
    submodule_commands = []
    original_run = module.run

    def recording_run(command, cwd=None, env=None, capture_output=False):
        if list(command[:2]) == ["git", "submodule"]:
            submodule_commands.append(list(command))
        if cwd is not None:
            resolved_cwd = Path(cwd).resolve()
            try:
                resolved_cwd.relative_to(external_repo)
            except ValueError:
                pass
            else:
                external_commands.append(list(command))
        return original_run(
            command,
            cwd=cwd,
            env=env,
            capture_output=capture_output,
        )

    sentinel = external_repo / "external-sentinel.txt"
    sentinel.write_text("unchanged\n", encoding="utf-8")
    external_payload = external_repo / "nested" / "payload.txt"
    assert not external_payload.exists()

    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setattr(module, "REPO_ROOT", project)
    monkeypatch.setattr(module, "run", recording_run)

    try:
        module.ensure_submodules(skip=False)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected replaced submodule path to be refused.")

    assert "must be a real directory" in message
    assert str(replaced_path) in message
    assert submodule_commands == []
    assert external_commands == []
    assert sentinel.read_text(encoding="utf-8") == "unchanged\n"
    assert not external_payload.exists()


def test_submodule_init_rejects_final_path_symlink_to_external_repo(
    monkeypatch,
    tmp_path,
):
    module = load_install_stage_2_module()
    relative_path = Path("managed")
    project, external_repo = build_submodule_replacement_fixture(
        tmp_path,
        relative_path,
    )
    replaced_path = project / relative_path
    replaced_path.symlink_to(external_repo, target_is_directory=True)

    assert_submodule_replacement_is_refused(
        module,
        monkeypatch,
        project,
        external_repo,
        replaced_path,
    )


def test_submodule_init_rejects_ancestor_symlink_to_external_repo(
    monkeypatch,
    tmp_path,
):
    module = load_install_stage_2_module()
    relative_path = Path("modules") / "managed"
    project, external_repo = build_submodule_replacement_fixture(
        tmp_path,
        relative_path,
    )
    replaced_path = project / "modules"
    replaced_path.rmdir()
    replaced_path.symlink_to(external_repo.parent, target_is_directory=True)

    assert_submodule_replacement_is_refused(
        module,
        monkeypatch,
        project,
        external_repo,
        replaced_path,
    )


def test_standard_virtualenv_keeps_matching_base_interpreter(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    base_python = tmp_path / "pyenv" / "bin" / "python"
    venv_path = tmp_path / "venv"
    venv_python = venv_path / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(
        module,
        "interpreter_base_identity",
        lambda executable, env=None: Path("/managed/python"),
    )
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd, env=None: commands.append((list(command), cwd)),
    )

    assert module.ensure_virtualenv(base_python, venv_path) == venv_python
    assert commands == []


def test_standard_virtualenv_rebuilds_after_base_interpreter_drift(
    monkeypatch, tmp_path
):
    module = load_install_stage_2_module()
    base_python = tmp_path / "pyenv" / "bin" / "python"
    venv_path = tmp_path / "venv"
    venv_python = venv_path / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    def fake_identity(executable, env=None):
        if executable == base_python:
            return Path("/managed/python")
        return Path("/system/python")

    monkeypatch.setattr(module, "interpreter_base_identity", fake_identity)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd, env=None: commands.append((list(command), cwd)),
    )

    assert module.ensure_virtualenv(base_python, venv_path) == venv_python
    assert commands == [
        (
            [str(base_python), "-m", "venv", "--clear", str(venv_path)],
            module.REPO_ROOT,
        )
    ]


def test_detect_shell_name_defaults_to_bash_when_empty():
    assert detect_shell_name("") == "bash"


def test_shell_rc_path_supports_bash_and_zsh(tmp_path):
    assert shell_rc_path(tmp_path, "bash") == tmp_path / ".bashrc"
    assert shell_rc_path(tmp_path, "zsh") == tmp_path / ".zshrc"
    assert shell_rc_path(tmp_path, "fish") is None


def test_build_direnv_hook_block_contains_managed_markers():
    block = build_direnv_hook_block("bash")

    assert DIRENV_BEGIN in block
    assert '*":$HOME/.local/bin:"*) ;;' in block
    assert 'eval "$(direnv hook bash)"' in block
    assert DIRENV_END in block


def test_upsert_managed_block_replaces_existing_block():
    original = "\n".join([DIRENV_BEGIN, "old", DIRENV_END, "tail", ""])
    replacement = "\n".join([DIRENV_BEGIN, "new", DIRENV_END, ""])

    updated = upsert_managed_block(original, DIRENV_BEGIN, DIRENV_END, replacement)

    assert "old" not in updated
    assert "new" in updated
    assert updated.endswith("tail\n")


def test_build_envrc_content_sources_repo_venv():
    content = build_envrc_content()

    assert ENVRC_MARKER in content
    assert 'source "$PWD/.venv/bin/activate"' in content


def test_direnv_download_name_supports_linux_targets():
    assert direnv_download_name("linux", "x86_64") == "direnv.linux-amd64"
    assert direnv_download_name("linux", "aarch64") == "direnv.linux-arm64"


def test_install_git_hooks_prefers_repo_local_installer(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    installer = repo_root / "scripts" / "install_git_hooks.py"
    fallback = repo_root / "TheKnowledge" / "scripts" / "install_git_hooks.py"
    installer.parent.mkdir(parents=True)
    fallback.parent.mkdir(parents=True)
    installer.write_text("", encoding="utf-8")
    fallback.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd, env=None: commands.append((list(command), cwd)),
    )

    module.install_git_hooks(Path("/tmp/venv/bin/python"))

    assert commands == [
        (
            ["/tmp/venv/bin/python", str(installer)],
            repo_root,
        )
    ]


def test_install_git_hooks_fallback_targets_repo_root(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    repo_root = tmp_path / "repo"
    installer = repo_root / "TheKnowledge" / "scripts" / "install_git_hooks.py"
    installer.parent.mkdir(parents=True)
    installer.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.setattr(
        module,
        "run",
        lambda command, cwd, env=None: commands.append((list(command), cwd)),
    )

    module.install_git_hooks(Path("/tmp/venv/bin/python"))

    assert commands == [
        (
            [
                "/tmp/venv/bin/python",
                str(installer),
                "--repo-root",
                str(repo_root),
            ],
            repo_root,
        )
    ]


def test_ensure_direnv_downloads_with_timeout(monkeypatch, tmp_path):
    module = load_install_stage_2_module()
    requested = {}

    class FakeResponse(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            self.close()
            return False

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        module, "default_user_bin_dir", lambda user_home: tmp_path / "bin"
    )
    monkeypatch.setattr(
        module,
        "direnv_download_name",
        lambda platform_name, machine: "direnv.linux-amd64",
    )

    def fake_urlopen(url, timeout):
        requested["url"] = url
        requested["timeout"] = timeout
        return FakeResponse(b"#!/bin/sh\nexit 0\n")

    monkeypatch.setattr(module, "urlopen", fake_urlopen)

    installed = module.ensure_direnv(auto_install=True, user_home=tmp_path)

    assert installed == tmp_path / "bin" / "direnv"
    assert installed.read_bytes() == b"#!/bin/sh\nexit 0\n"
    assert requested["timeout"] == module.DIRENV_DOWNLOAD_TIMEOUT_SECONDS
    assert os.access(installed, os.X_OK)


def test_ensure_direnv_reports_download_failure(monkeypatch, tmp_path):
    module = load_install_stage_2_module()

    monkeypatch.setattr(module.shutil, "which", lambda name: None)
    monkeypatch.setattr(
        module, "default_user_bin_dir", lambda user_home: tmp_path / "bin"
    )
    monkeypatch.setattr(
        module,
        "direnv_download_name",
        lambda platform_name, machine: "direnv.linux-amd64",
    )
    monkeypatch.setattr(
        module,
        "urlopen",
        lambda url, timeout: (_ for _ in ()).throw(OSError("timed out")),
    )

    try:
        module.ensure_direnv(auto_install=True, user_home=tmp_path)
    except RuntimeError as error:
        message = str(error)
    else:
        raise AssertionError("Expected direnv download to fail.")

    assert "Failed to download direnv" in message
    assert "within 30 seconds" in message
