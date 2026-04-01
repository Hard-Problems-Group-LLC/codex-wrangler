import importlib.util
from pathlib import Path


def load_dev_setup_module():
    repo_root = Path(__file__).resolve().parents[1]
    module_path = repo_root / "scripts" / "dev_setup.py"
    spec = importlib.util.spec_from_file_location("dev_setup", module_path)
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
