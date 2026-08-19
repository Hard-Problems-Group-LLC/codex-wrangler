import importlib.util
import os
from pathlib import Path


def load_dev_setup_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "dev_setup.py"
    spec = importlib.util.spec_from_file_location("dev_setup", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_tool_validation_profiles_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "tool_validation_profiles.py"
    spec = importlib.util.spec_from_file_location(
        "tool_validation_profiles", module_path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def load_timeout_wrapper_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "run_tool_with_timeout.py"
    spec = importlib.util.spec_from_file_location("run_tool_with_timeout", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_ensure_virtualenv_keeps_matching_existing_environment(tmp_path, monkeypatch):
    module = load_dev_setup_module()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    monkeypatch.setattr(module, "python_version", lambda executable: "3.12.11")
    monkeypatch.setattr(module, "run", lambda command: commands.append(list(command)))

    result = module.ensure_virtualenv("python3.12", tmp_path / "venv")

    assert result == venv_python
    assert commands == []


def test_ensure_virtualenv_rebuilds_when_python_version_differs(tmp_path, monkeypatch):
    module = load_dev_setup_module()
    venv_python = tmp_path / "venv" / "bin" / "python"
    venv_python.parent.mkdir(parents=True)
    venv_python.write_text("", encoding="utf-8")
    commands = []

    def fake_python_version(executable):
        if str(executable).endswith("python3.12"):
            return "3.12.11"
        return "3.13.7"

    monkeypatch.setattr(module, "python_version", fake_python_version)
    monkeypatch.setattr(module, "run", lambda command: commands.append(list(command)))

    result = module.ensure_virtualenv("python3.12", tmp_path / "venv")

    assert result == venv_python
    assert commands == [["python3.12", "-m", "venv", "--clear", tmp_path / "venv"]]


def test_explicit_runtime_override_can_skip_policy_modules(monkeypatch):
    module = load_tool_validation_profiles_module()
    repo_root = Path(__file__).resolve().parents[1]
    seen = {}

    def fake_probe(executable, required_modules):
        seen["executable"] = executable
        seen["required_modules"] = list(required_modules)
        return (3, 12, 12), True

    monkeypatch.setattr(module, "_probe_python_candidate", fake_probe)

    resolved = module.resolve_runtime_policy_executable(
        repo_root,
        "steady_state_python_tools",
        explicit_candidate="python3.12",
        required_modules=[],
    )

    assert resolved == "python3.12"
    assert seen["executable"] == "python3.12"
    assert seen["required_modules"] == []


def test_runtime_candidate_preserves_project_venv_symlink(tmp_path):
    module = load_tool_validation_profiles_module()
    repo_root = tmp_path / "repo"
    base_python = tmp_path / "pyenv" / "versions" / "3.14.6" / "bin" / "python"
    venv_python = repo_root / ".venv" / "bin" / "python"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("", encoding="utf-8")
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)

    normalized = module._normalize_executable_candidate(
        repo_root,
        ".venv/bin/python",
    )

    assert normalized == str(venv_python.absolute())
    assert normalized != str(base_python)


def test_timeout_wrapper_preserves_venv_path_for_nested_helpers(monkeypatch, tmp_path):
    module = load_timeout_wrapper_module()
    repo_root = tmp_path / "repo"
    base_python = tmp_path / "pyenv" / "versions" / "3.14.6" / "bin" / "python"
    venv_python = repo_root / ".venv" / "bin" / "python"
    base_python.parent.mkdir(parents=True)
    base_python.write_text("", encoding="utf-8")
    base_python.chmod(0o755)
    venv_python.parent.mkdir(parents=True)
    venv_python.symlink_to(base_python)
    monkeypatch.setattr(module, "REPO_ROOT", repo_root)
    monkeypatch.delenv("THEKNOWLEDGE_BLACK_PYTHON", raising=False)
    monkeypatch.delenv("THEKNOWLEDGE_PYTHON_TOOLS", raising=False)

    module.preserve_project_venv_runtime()

    assert os.environ["THEKNOWLEDGE_PYTHON_TOOLS"] == str(venv_python.absolute())


def test_timeout_wrapper_loads_project_validation_profile_overlay(monkeypatch):
    module = load_timeout_wrapper_module()
    monkeypatch.delenv("THEKNOWLEDGE_BLACK_PYTHON", raising=False)
    monkeypatch.delenv("THEKNOWLEDGE_PYTHON_TOOLS", raising=False)

    module.load_helper_main()

    loaded = importlib.import_module("tool_validation_profiles")
    expected = (
        Path(__file__).resolve().parents[1]
        / "scripts"
        / ("tool_validation_profiles.py")
    )
    assert Path(loaded.__file__).resolve() == expected
