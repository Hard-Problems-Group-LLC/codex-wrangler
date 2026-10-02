"""Exercise first-launch HOME initialization through real install transactions."""

import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import textwrap

import pytest

from codex_wrangler.config import config_from_args, parse_args
from codex_wrangler.models import CodexWranglerError
from codex_wrangler.operations import (
    expected_codex_platform_details,
    install_like_operation,
)
from codex_wrangler.rendering import build_launcher_content
from codex_wrangler.slots import read_slot_metadata

REPO_ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.30.0"
FAKE_CODEX = f"#!{sys.executable}\n" + textwrap.dedent(f"""\
    import json
    import os
    from pathlib import Path
    import sys

    if sys.argv[1:] == ["--version"]:
        print("codex-cli {VERSION}")
        sys.exit(0)
    home = Path(os.environ["CODEX_HOME"])
    if not home.is_dir():
        sys.exit("Error finding codex home: CODEX_HOME path does not exist")
    print(json.dumps({{"home": os.environ["HOME"], "codex_home": str(home),
                      "args": sys.argv[1:]}}))
    """)


@pytest.fixture(params=["isolated", "custom", "shared"])
def fresh_project(tmp_path, request):
    """Prepare a clean target with deterministic external npm/Codex commands."""

    if shutil.which("node") is None:
        pytest.skip("generated launchers require Node.js")
    project = tmp_path / "clean project"
    project.mkdir()
    operator_home = tmp_path / "operator home"
    operator_home.mkdir()
    tools = tmp_path / "tools"
    tools.mkdir()
    npm = tools / "npm"
    npm.write_text(
        f"#!{sys.executable}\n" + textwrap.dedent(f"""\
            import json
            import os
            from pathlib import Path
            import shutil
            import signal
            import sys
            import time

            if sys.argv[1:] == ["view", "@openai/codex", "dist-tags", "--json"]:
                print(json.dumps({{"latest": "{VERSION}"}}))
            elif sys.argv[1] == "install":
                prefix = Path(sys.argv[sys.argv.index("--prefix") + 1])
                failure = os.environ.get("WRANGLER_TEST_FAILURE")
                if failure == "npm":
                    sys.exit("fixture npm failure")
                if failure == "timeout":
                    time.sleep(10)
                if failure == "interrupt":
                    os.kill(os.getppid(), signal.SIGINT)
                    time.sleep(10)
                if failure == "kill":
                    os.kill(os.getppid(), signal.SIGKILL)
                    sys.exit(9)
                version = json.loads((prefix / "package.json").read_text())["devDependencies"]["@openai/codex"]
                manifest = {{"version": version}}
                (prefix / "package-lock.json").write_text(json.dumps(
                    {{"packages": {{"node_modules/@openai/codex": manifest}}}}))
                package = prefix / "node_modules/@openai/codex"
                package.mkdir(parents=True)
                (package / "package.json").write_text(json.dumps(manifest))
                platform_details = {expected_codex_platform_details()!r}
                if platform_details is not None:
                    name, triple, suffix = platform_details
                    platform_package = prefix / "node_modules" / name
                    native = platform_package / "vendor" / triple / "bin" / ("codex" + suffix)
                    native.parent.mkdir(parents=True)
                    shutil.copyfile(sys.executable, native)
                    (platform_package / "package.json").write_text(json.dumps(
                        {{"version": version + "-" + name.rsplit("codex-", 1)[1]}}))
                shim = prefix / "node_modules/.bin/codex"
                shim.parent.mkdir()
                shim.write_text({FAKE_CODEX!r})
                if failure == "validation":
                    shim.write_text("#!/bin/sh\\nprintf 'codex-cli wrong-version\\n'\\n")
                shim.chmod(0o755)
            else:
                sys.exit("Unexpected fake npm invocation: " + repr(sys.argv))
            """),
        encoding="utf-8",
    )
    npm.chmod(0o755)
    env = os.environ.copy()
    env.update(
        PATH=str(tools) + os.pathsep + env.get("PATH", ""),
        HOME=str(operator_home),
        CODEX_HOME=str(tmp_path / "unrelated context"),
        CODEX_LOCAL_PREFLIGHT="off",
    )
    args = ["--set-reasonable-permissions"]
    if request.param == "shared":
        args.append("--shared-home")
        home = operator_home
    elif request.param == "custom":
        args.extend(["--codex-home-dir", "state/custom-home"])
        home = project / "state/custom-home"
    else:
        home = project / ".local/codex-home"
    return project, home, env, args


def run_wrangler(project, env, args):
    """Run the utility CLI with a bounded lifetime in a disposable target."""

    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "codex-wrangler.py"), *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


@pytest.fixture
def installed_project(fresh_project):
    """Install using real owned code and fake npm/Codex."""

    project, home, env, args = fresh_project
    completed = run_wrangler(project, env, args)
    assert completed.returncode == 0, completed.stderr
    assert not (project / ".codex-wrangler-initial-install.json").exists()
    assert home.is_dir()
    assert list(home.iterdir()) == []
    return project, home, env


@pytest.mark.parametrize(
    "failure", ["npm", "validation", "timeout", "interrupt", "kill"]
)
def test_first_install_failure_can_retry(fresh_project, failure):
    """A failed first transaction must not lock out its own unchanged retry."""

    project, home, env, args = fresh_project
    failed = run_wrangler(
        project,
        {**env, "WRANGLER_TEST_FAILURE": failure},
        [*args, "--npm-timeout-seconds", "1"],
    )
    assert failed.returncode != 0
    assert (
        "not promoted" in failed.stderr
        or "interrupted" in failed.stderr
        or failed.returncode == -signal.SIGKILL
    )
    assert not (project / ".local/codex/active").exists()

    retried = run_wrangler(project, env, args)

    assert retried.returncode == 0, retried.stderr
    assert "npm view" not in retried.stderr
    assert not (project / ".codex-wrangler-initial-install.json").exists()
    assert (project / ".local/codex/active").exists()
    assert home.is_dir()


def test_failed_first_install_supports_repair(fresh_project):
    """Absolute-path repair can use intent, including a recorded custom HOME."""

    project, home, env, args = fresh_project
    failed = run_wrangler(project, {**env, "WRANGLER_TEST_FAILURE": "npm"}, args)
    assert failed.returncode != 0
    sentinel = home / "context-sentinel"
    sentinel.write_bytes(b"preserved fixture context\n")
    before = home.stat().st_ino

    repaired = run_wrangler(project, env, ["--repair", str(project)])

    assert repaired.returncode == 0, repaired.stderr
    assert sentinel.read_bytes() == b"preserved fixture context\n"
    assert home.stat().st_ino == before


def test_failed_first_install_inspection_is_read_only(fresh_project):
    """Inspection distinguishes pending intent from a completed installation."""

    project, home, env, args = fresh_project
    failed = run_wrangler(project, {**env, "WRANGLER_TEST_FAILURE": "npm"}, args)
    assert failed.returncode != 0
    receipt = project / ".codex-wrangler-initial-install.json"
    before = receipt.read_bytes()
    inspect_args = [*args, "--inspect"]
    inspect_args.remove("--set-reasonable-permissions")

    inspected = run_wrangler(project, env, inspect_args)

    report = json.loads(inspected.stdout)
    assert inspected.returncode == 1
    assert report["state"]["initial_install_pending"] is True
    assert report["state"]["active_slot"] is None
    assert receipt.read_bytes() == before
    assert list(home.iterdir()) == []


@pytest.mark.parametrize("failure", ["npm", "validation"])
def test_receiptless_legacy_candidate_repair_preserves_state(fresh_project, failure):
    """Rebuild known layouts but never guess an unrecorded custom HOME binding."""

    project, home, env, args = fresh_project
    failed = run_wrangler(project, {**env, "WRANGLER_TEST_FAILURE": failure}, args)
    assert failed.returncode != 0
    (project / ".codex-wrangler-initial-install.json").unlink()
    original_ignores = (project / ".gitignore").read_bytes()
    runtime = project / ".local/codex"
    candidate = next(runtime.glob(".candidate-*"))
    manifest = candidate / "package.json"
    before = manifest.read_bytes()
    identity = candidate.stat().st_ino
    sentinel = home / "preserved-context"
    sentinel.write_bytes(b"legacy fixture context\n")
    home_identity = home.stat().st_ino

    ordinary = run_wrangler(project, env, args)
    assert ordinary.returncode == 1
    assert "without exact managed ownership evidence" in ordinary.stderr
    missing_choice = run_wrangler(project, env, ["--repair", str(project)])
    assert missing_choice.returncode == 1
    assert "Rerun with exactly one explicit" in missing_choice.stderr
    choice = "--shared-home" if "--shared-home" in args else "--isolated-home"
    if "--codex-home-dir" not in args:
        interrupted_repair = run_wrangler(
            project,
            {**env, "WRANGLER_TEST_FAILURE": failure},
            ["--repair", str(project), choice],
        )
        assert interrupted_repair.returncode != 0
        receipt = project / ".codex-wrangler-initial-install.json"
        assert receipt.is_file()
        assert json.loads(receipt.read_text())["codex_version"] == VERSION
    repaired = run_wrangler(project, env, ["--repair", str(project), choice])

    assert "npm view" not in repaired.stderr
    assert candidate.stat().st_ino == identity
    assert manifest.read_bytes() == before
    assert home.stat().st_ino == home_identity
    assert sentinel.read_bytes() == b"legacy fixture context\n"
    if "--codex-home-dir" in args:
        assert repaired.returncode == 1
        assert "will not recreate missing managed isolated HOME" in repaired.stderr
        assert not (project / ".local/codex-home").exists()
        assert not (runtime / ".codex-wrangler.json").exists()
        assert (project / ".gitignore").read_bytes() == original_ignores
        return
    assert repaired.returncode == 0, repaired.stderr
    assert not (project / ".codex-wrangler-initial-install.json").exists()
    metadata = json.loads((runtime / ".codex-wrangler.json").read_text())
    assert metadata["codex_version"] == VERSION
    assert metadata["reasonable_permissions_enabled"] is False


def test_repair_reports_missing_ownership_before_home_choice(tmp_path):
    """Unknown runtime data must not elicit an ineffective HOME-mode override."""

    runtime = tmp_path / ".local/codex"
    runtime.mkdir(parents=True)
    sentinel = runtime / "unowned-data"
    sentinel.write_bytes(b"preserve this fixture\n")

    completed = run_wrangler(tmp_path, os.environ.copy(), ["--repair", str(tmp_path)])

    assert completed.returncode == 1
    assert "Cannot prove a codex-wrangler-managed install" in completed.stderr
    assert "Rerun with exactly one explicit" not in completed.stderr
    assert sentinel.read_bytes() == b"preserve this fixture\n"


def launch(installed_project, *args):
    """Execute the installed launcher without touching any operator project."""

    project, _home, env = installed_project
    return subprocess.run(
        [str(project / "bin/codex-local"), *args],
        cwd=project,
        env=env,
        capture_output=True,
        text=True,
        timeout=10,
    )


def test_fresh_install_launch_initializes_codex_home(installed_project):
    """A successful install must support first launch, not just --version."""

    _project, home, env = installed_project
    home_identity = home.stat().st_ino

    completed = launch(installed_project)

    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout) == {
        "home": str(home),
        "codex_home": str(home / ".codex"),
        "args": ["-a", "on-request", "-s", "workspace-write"],
    }
    assert stat.S_IMODE((home / ".codex").stat().st_mode) == 0o700
    assert home.stat().st_ino == home_identity
    assert not Path(env["CODEX_HOME"]).exists()


def retain_installed_project(installed_project, *, copy=False):
    """Materialize a retained image and a synthetic context preservation marker."""

    original, home, env = installed_project
    context = home / ".codex"
    context.mkdir()
    sentinel = context / "preserved-context"
    sentinel.write_bytes(b"retained fixture context\n")
    retained = original.parent / "retained project"
    if copy:
        shutil.copytree(original, retained, symlinks=True)
    else:
        original.rename(retained)
    moved_home = (
        retained / home.relative_to(original) if home.is_relative_to(original) else home
    )
    return original, retained, moved_home, env


@pytest.mark.parametrize("copy", [False, True])
def test_relocated_cli_repair_restores_launcher_and_rollback(installed_project, copy):
    """Recover unknown origins through the real CLI and preserve existing context."""

    original, retained, home, env = retain_installed_project(
        installed_project, copy=copy
    )
    sentinel = home / ".codex/preserved-context"
    before = sentinel.stat()
    home_before = home.stat()
    active_record = retained / ".local/codex/slots/a/.codex-wrangler-slot.json"
    original_record = active_record.read_bytes()
    inspection = run_wrangler(retained, env, ["--inspect", str(retained)])
    report = json.loads(inspection.stdout)["repair"]
    assert report["previous_project_root"] == str(original)
    assert report["automatic_repair_possible"] is True
    dry_run = run_wrangler(
        retained, env, ["--repair", str(retained), "--repair-auto", "--dry-run"]
    )
    assert dry_run.returncode == 0, dry_run.stderr
    assert active_record.read_bytes() == original_record
    assert not (retained / ".local/codex/slots/b").exists()

    repaired = run_wrangler(retained, env, ["--repair", str(retained), "--repair-auto"])
    assert repaired.returncode == 0, repaired.stderr
    assert "npm view" not in repaired.stderr
    assert sentinel.read_bytes() == b"retained fixture context\n"
    assert sentinel.stat().st_ino == before.st_ino
    assert sentinel.stat().st_mtime_ns == before.st_mtime_ns
    assert home.stat().st_ino == home_before.st_ino
    config = config_from_args(parse_args(["--repair", str(retained)]))
    assert config.layout.repair_from_root is None
    assert config.reasonable_permissions_enabled is True
    assert read_slot_metadata(config.layout, "a") is not None
    assert read_slot_metadata(config.layout, "b") is not None
    launched = launch((retained, home, env), "resume", "--last")
    assert launched.returncode == 0, launched.stderr
    assert json.loads(launched.stdout)["codex_home"] == str(home / ".codex")


@pytest.mark.parametrize("failure", ["npm", "validation"])
def test_relocated_cli_repair_failure_is_retryable(installed_project, failure):
    """Before promotion, a failed replacement leaves original slots intact."""

    _original, retained, home, env = retain_installed_project(installed_project)
    record = retained / ".local/codex/slots/a/.codex-wrangler-slot.json"
    before = record.read_bytes()
    failed = run_wrangler(
        retained,
        {**env, "WRANGLER_TEST_FAILURE": failure},
        ["--repair", str(retained), "--repair-auto"],
    )
    assert failed.returncode == 1
    assert record.read_bytes() == before
    assert os.readlink(retained / ".local/codex/active") == "slots/a"
    assert not (retained / ".local/codex/slots/b").exists()
    assert (
        home / ".codex/preserved-context"
    ).read_bytes() == b"retained fixture context\n"
    repaired = run_wrangler(retained, env, ["--repair", str(retained), "--repair-auto"])
    assert repaired.returncode == 0, repaired.stderr


def test_relocated_cli_repair_retains_displaced_slot(installed_project):
    """An occupied inactive slot remains available as moved-install evidence."""

    original, _home, env = installed_project
    second = run_wrangler(original, env, ["--repair", str(original)])
    assert second.returncode == 0, second.stderr
    inactive = original / ".local/codex/slots/a"
    sentinel = inactive / "retained-runtime-evidence"
    sentinel.write_bytes(b"original inactive fixture runtime\n")
    identity = inactive.stat().st_ino
    _original, retained, _home, env = retain_installed_project(installed_project)

    repaired = run_wrangler(retained, env, ["--repair", str(retained), "--repair-auto"])

    assert repaired.returncode == 0, repaired.stderr
    assert os.readlink(retained / ".local/codex/active") == "slots/a"
    retired = list((retained / ".local/codex/slots").glob(".a.retired-*"))
    assert len(retired) == 1
    assert retired[0].stat().st_ino == identity
    assert (
        retired[0] / sentinel.name
    ).read_bytes() == b"original inactive fixture runtime\n"


def test_relocated_receipt_cli_failure_can_retry_without_origin(fresh_project):
    """Once renamed intent is rebound, another failed attempt remains actionable."""

    original, home, env, args = fresh_project
    failed = run_wrangler(original, {**env, "WRANGLER_TEST_FAILURE": "npm"}, args)
    assert failed.returncode == 1
    retained = original.parent / "retained unfinished"
    original.rename(retained)
    moved_home = (
        retained / home.relative_to(original) if home.is_relative_to(original) else home
    )
    sentinel = moved_home / "preserved-context"
    sentinel.write_bytes(b"unfinished fixture context\n")
    failed_again = run_wrangler(
        retained,
        {**env, "WRANGLER_TEST_FAILURE": "npm"},
        ["--repair", str(retained), "--repair-auto"],
    )
    assert failed_again.returncode == 1
    receipt = retained / ".codex-wrangler-initial-install.json"
    assert json.loads(receipt.read_text())["project_root"] == str(retained)
    repaired = run_wrangler(retained, env, ["--repair", str(retained)])
    assert repaired.returncode == 0, repaired.stderr
    assert not receipt.exists()
    assert sentinel.read_bytes() == b"unfinished fixture context\n"


def test_relocated_post_promotion_failure_can_resume(installed_project, monkeypatch):
    """A real filesystem publication failure must not hide the committed slot."""

    _original, retained, _home, env = retain_installed_project(installed_project)
    monkeypatch.setenv("PATH", env["PATH"])
    monkeypatch.setenv("HOME", env["HOME"])
    config = config_from_args(parse_args(["--repair", str(retained), "--repair-auto"]))
    real_replace = os.replace

    def fail_root_publication(source, destination):
        """Inject one external I/O failure after the active pointer commits."""

        if Path(destination) == config.layout.metadata_path:
            raise OSError("fixture root metadata publication failure")
        return real_replace(source, destination)

    monkeypatch.setattr(os, "replace", fail_root_publication)
    with pytest.raises(CodexWranglerError, match="committed verified slot"):
        install_like_operation(config)
    assert os.readlink(retained / ".local/codex/active") == "slots/b"
    monkeypatch.setattr(os, "replace", real_replace)
    repaired = run_wrangler(retained, env, ["--repair", str(retained), "--repair-auto"])
    assert repaired.returncode == 0, repaired.stderr
    normal = config_from_args(parse_args(["--repair", str(retained)]))
    assert read_slot_metadata(normal.layout, "a") is not None
    assert read_slot_metadata(normal.layout, "b") is not None


def test_relocated_legacy_cli_repair_migrates_safely(fresh_project):
    """Origin recovery also survives the existing guarded default migration."""

    original, operator_or_local_home, env, args = fresh_project
    shared = "--shared-home" in args
    legacy_args = ["--local-dir", ".codex-local", "--codex-home-dir", ".codex-home"]
    if shared:
        legacy_args.append("--shared-home")
    installed = run_wrangler(original, env, legacy_args)
    assert installed.returncode == 0, installed.stderr
    home = operator_or_local_home if shared else original / ".codex-home"
    sentinel = home / "preserved-context"
    sentinel.write_bytes(b"legacy retained fixture\n")
    identity = home.stat().st_ino
    retained = original.parent / "retained legacy"
    original.rename(retained)
    repaired = run_wrangler(retained, env, ["--repair", str(retained), "--repair-auto"])
    assert repaired.returncode == 0, repaired.stderr
    final_home = home if shared else retained / ".local/codex-home"
    assert final_home.stat().st_ino == identity
    assert (
        final_home / "preserved-context"
    ).read_bytes() == b"legacy retained fixture\n"
    normal = config_from_args(parse_args(["--repair", str(retained)]))
    assert read_slot_metadata(normal.layout, "a") is not None
    assert read_slot_metadata(normal.layout, "b") is not None


def test_launch_preserves_existing_context(installed_project):
    """Startup must neither replace a context directory nor chmod its contents."""

    _project, home, _env = installed_project
    context = home / ".codex"
    context.mkdir(mode=0o750)
    sentinel = context / "preserved-context"
    sentinel.write_bytes(b"fixture context\n")
    before = (context.stat(), sentinel.stat(), sentinel.read_bytes())

    for _ in range(2):
        completed = launch(installed_project, "resume", "--last")
        assert completed.returncode == 0, completed.stderr

    for field in ("st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(context.stat(), field) == getattr(before[0], field)
        assert getattr(sentinel.stat(), field) == getattr(before[1], field)
    assert sentinel.read_bytes() == before[2]


def test_launch_does_not_recreate_missing_home(installed_project):
    """An absent protected HOME is not mistaken for a fresh .codex child."""

    _project, home, _env = installed_project
    home.rmdir()

    completed = launch(installed_project)

    assert completed.returncode != 0
    assert not home.exists()


@pytest.mark.parametrize("kind", ["file", "dangling-link", "directory-link"])
def test_launch_rejects_unsafe_codex_home(installed_project, kind):
    """Unsafe context destinations cannot redirect writes or be replaced."""

    project, home, _env = installed_project
    context = home / ".codex"
    target = project / "outside-context"
    if kind == "file":
        context.write_bytes(b"not a directory\n")
    else:
        if kind == "directory-link":
            target.mkdir()
        context.symlink_to(target, target_is_directory=True)
    before = context.lstat()

    completed = launch(installed_project)

    # Shared mode retains support for the operator's existing directory link.
    if home.name == "operator home" and kind == "directory-link":
        assert completed.returncode == 0, completed.stderr
    else:
        assert completed.returncode != 0
    for field in ("st_ino", "st_mode", "st_size", "st_mtime_ns", "st_ctime_ns"):
        assert getattr(context.lstat(), field) == getattr(before, field)
    if kind == "file":
        assert context.read_bytes() == b"not a directory\n"
    elif kind == "directory-link":
        assert list(target.iterdir()) == []
    else:
        assert not target.exists()


@pytest.mark.parametrize("concurrent_creation", [False, True])
def test_launcher_handles_mkdir_failure(installed_project, concurrent_creation):
    """A failed mkdir is accepted only if another launch made a real directory."""

    _project, home, env = installed_project
    tools = Path(env["PATH"].split(os.pathsep)[0])
    mkdir = tools / "mkdir"
    mkdir.write_text(
        f"#!{sys.executable}\n"
        "from pathlib import Path\n"
        "import sys\n"
        + ("Path(sys.argv[-1]).mkdir(mode=0o700)\n" if concurrent_creation else "")
        + "sys.exit(1)\n",
        encoding="utf-8",
    )
    mkdir.chmod(0o755)

    completed = launch(installed_project)

    if concurrent_creation:
        assert completed.returncode == 0, completed.stderr
        assert (home / ".codex").is_dir()
    else:
        assert completed.returncode != 0
        assert "Cannot create CODEX_HOME directory" in completed.stderr
        assert not (home / ".codex").exists()
    assert home.is_dir()


@pytest.mark.parametrize("shared_home", [False, True])
def test_real_codex_first_launch(tmp_path, config_factory, shared_home):
    """Opt in with CODEX_WRANGLER_SMOKE_BINARY to probe real offline startup."""

    source = os.environ.get("CODEX_WRANGLER_SMOKE_BINARY")
    if not source:
        pytest.skip("set CODEX_WRANGLER_SMOKE_BINARY to a native Codex executable")
    if shutil.which("node") is None:
        pytest.skip("generated launchers require Node.js")
    config = config_factory(tmp_path, shared_home=shared_home)
    binary = config.layout.local_node_modules_dir / ".bin" / "codex"
    binary.parent.mkdir(parents=True)
    shutil.copyfile(source, binary)
    binary.chmod(0o755)
    config.layout.launcher_path.parent.mkdir(parents=True)
    config.layout.launcher_path.write_text(
        build_launcher_content(config), encoding="utf-8"
    )
    config.layout.launcher_path.chmod(0o755)
    caller_home = tmp_path / "caller-home"
    caller_home.mkdir()
    home = caller_home if shared_home else config.layout.codex_home_dir
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": str(caller_home),
        "CODEX_LOCAL_PREFLIGHT": "off",
        "XDG_CONFIG_HOME": str(home / ".config"),
        "XDG_CACHE_HOME": str(home / ".cache"),
        "XDG_STATE_HOME": str(home / ".local/state"),
        "XDG_DATA_HOME": str(home / ".local/share"),
    }

    completed = subprocess.run(
        [str(config.layout.launcher_path), "login", "status"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=15,
    )

    assert completed.returncode == 1, completed.stderr
    assert "Not logged in" in completed.stderr
    assert "Error finding codex home" not in completed.stderr
    assert (home / ".codex").is_dir()
