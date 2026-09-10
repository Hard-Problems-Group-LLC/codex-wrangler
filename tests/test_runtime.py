import os
import signal
import subprocess
import sys
import time

import pytest

from codex_wrangler.models import CodexWranglerError
from codex_wrangler.runtime import run_command, terminate_process_group


def test_run_command_reports_timeout(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = None

        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(cmd=["npm", "install"], timeout=timeout)

    process = FakeProcess()
    terminated = []
    monkeypatch.setattr(
        "codex_wrangler.runtime.subprocess.Popen",
        lambda command, **kwargs: process,
    )
    monkeypatch.setattr(
        "codex_wrangler.runtime.terminate_process_group",
        lambda observed: terminated.append(observed),
    )

    with pytest.raises(CodexWranglerError, match="timed out after 5s"):
        run_command(["npm", "install"], cwd=str(tmp_path), timeout_seconds=5)

    assert terminated == [process]


def test_run_command_terminates_group_on_keyboard_interrupt(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = None

        def communicate(self, timeout=None):
            raise KeyboardInterrupt

    process = FakeProcess()
    terminated = []
    monkeypatch.setattr(
        "codex_wrangler.runtime.subprocess.Popen",
        lambda command, **kwargs: process,
    )
    monkeypatch.setattr(
        "codex_wrangler.runtime.terminate_process_group",
        lambda observed: terminated.append(observed),
    )

    with pytest.raises(KeyboardInterrupt):
        run_command(["npm", "install"], cwd=str(tmp_path), timeout_seconds=5)

    assert terminated == [process]


@pytest.mark.skipif(os.name == "nt", reason="POSIX terminal signals only")
@pytest.mark.parametrize("observed_signal", [signal.SIGHUP, signal.SIGTERM])
def test_run_command_terminates_group_on_terminal_signal(
    monkeypatch,
    tmp_path,
    observed_signal,
):
    """Terminal closure cannot leave npm descendants running detached."""

    class FakeProcess:
        returncode = None

        def communicate(self, timeout=None):
            signal.raise_signal(observed_signal)
            raise AssertionError("managed signal must interrupt communicate")

    process = FakeProcess()
    terminated = []
    previous_handler = signal.getsignal(observed_signal)
    monkeypatch.setattr(
        "codex_wrangler.runtime.subprocess.Popen",
        lambda command, **kwargs: process,
    )
    monkeypatch.setattr(
        "codex_wrangler.runtime.terminate_process_group",
        lambda observed: terminated.append(observed),
    )

    with pytest.raises(CodexWranglerError, match=observed_signal.name):
        run_command(["npm", "install"], cwd=str(tmp_path), timeout_seconds=5)

    assert terminated == [process]
    assert signal.getsignal(observed_signal) == previous_handler


def test_run_command_emits_elapsed_heartbeat_without_extending_deadline(
    monkeypatch,
    tmp_path,
    capsys,
):
    class FakeProcess:
        returncode = 0

        def __init__(self):
            self.calls = 0

        def communicate(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired(cmd=["npm", "install"], timeout=timeout)
            return None, None

    process = FakeProcess()
    monkeypatch.setattr("codex_wrangler.runtime.COMMAND_HEARTBEAT_SECONDS", 1.0)
    monkeypatch.setattr(
        "codex_wrangler.runtime.subprocess.Popen",
        lambda command, **kwargs: process,
    )

    result = run_command(
        ["npm", "install"],
        cwd=str(tmp_path),
        timeout_seconds=30,
    )

    assert result.returncode == 0
    assert process.calls == 2
    stderr = capsys.readouterr().err
    assert "Still running after" in stderr
    assert "remaining" in stderr


def test_terminate_process_group_kills_descendants_after_leader_exits(monkeypatch):
    """A reaped group leader does not imply all descendants have exited."""

    class FakeProcess:
        pid = 4242

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("group SIGKILL should be sufficient")

    signals = []
    monkeypatch.setattr(
        "codex_wrangler.runtime.os.killpg",
        lambda pgid, observed_signal: signals.append((pgid, observed_signal)),
    )
    moments = iter([0.0, 3.0])
    monkeypatch.setattr("codex_wrangler.runtime.time.monotonic", lambda: next(moments))

    terminate_process_group(FakeProcess())

    assert signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]


def test_terminate_process_group_retries_signals_after_second_ctrl_c(monkeypatch):
    """A Ctrl-C arriving during cleanup cannot abandon the npm process group."""

    class FakeProcess:
        pid = 4343

        def poll(self):
            return None

        def wait(self, timeout=None):
            return 0

        def kill(self):
            raise AssertionError("group retry should complete cleanup")

    signals = []
    attempts = {signal.SIGTERM: 0, signal.SIGKILL: 0}

    def interrupt_once(pgid, observed_signal):
        if observed_signal == 0:
            return
        signals.append((pgid, observed_signal))
        attempts[observed_signal] += 1
        if attempts[observed_signal] == 1:
            raise KeyboardInterrupt

    monkeypatch.setattr("codex_wrangler.runtime.os.killpg", interrupt_once)
    moments = iter([0.0, 3.0])
    monkeypatch.setattr("codex_wrangler.runtime.time.monotonic", lambda: next(moments))

    terminate_process_group(FakeProcess())

    assert signals == [
        (4343, signal.SIGTERM),
        (4343, signal.SIGTERM),
        (4343, signal.SIGKILL),
        (4343, signal.SIGKILL),
    ]


def test_run_command_wraps_spawn_failure_without_traceback(monkeypatch, tmp_path):
    """A candidate exec-format or launch error becomes an operator-safe error."""

    monkeypatch.setattr(
        "codex_wrangler.runtime.subprocess.Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("exec failed")),
    )

    with pytest.raises(CodexWranglerError, match="Failed to start command"):
        run_command(["broken-codex", "--version"], cwd=str(tmp_path))


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics only")
def test_run_command_timeout_kills_real_grandchild(tmp_path):
    """The real timeout path kills a descendant before its delayed write."""

    sentinel = tmp_path / "detached-grandchild-survived"
    leader = tmp_path / "leader.py"
    leader.write_text(
        "\n".join(
            (
                "import subprocess",
                "import sys",
                "import time",
                "sentinel = sys.argv[1]",
                "subprocess.Popen([sys.executable, '-c', "
                '    "import pathlib,sys,time; time.sleep(1.2); "'
                '    "pathlib.Path(sys.argv[1]).write_text(\\"survived\\\\n\\")",'
                "    sentinel])",
                "time.sleep(30)",
                "",
            )
        ),
        encoding="utf-8",
    )

    with pytest.raises(CodexWranglerError, match="timed out"):
        run_command(
            [sys.executable, str(leader), str(sentinel)],
            cwd=str(tmp_path),
            timeout_seconds=0.25,
        )

    time.sleep(1.3)
    assert not sentinel.exists()
