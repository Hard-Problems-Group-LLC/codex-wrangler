"""Small runtime helpers for process execution and operator output."""

from __future__ import annotations

from datetime import datetime, timezone
import os
import signal
import shutil
import subprocess
import sys
import threading
import time
from typing import Dict, Mapping, Optional, Sequence, Tuple

from .models import CodexWranglerError

COMMAND_HEARTBEAT_SECONDS = 15.0


class _ManagedTerminationSignal(BaseException):
    """Carry one terminal-closing signal out of a blocking subprocess wait."""

    def __init__(self, signal_number: int) -> None:
        super().__init__(signal_number)
        self.signal_number = signal_number


def install_managed_termination_handlers() -> Dict[int, object]:
    """Install temporary POSIX HUP/TERM handlers in the main thread."""

    if os.name == "nt" or threading.current_thread() is not threading.main_thread():
        return {}
    previous_handlers: Dict[int, object] = {}

    def raise_termination(signal_number: int, _frame: object) -> None:
        """Interrupt ``communicate`` while retaining the triggering signal."""

        raise _ManagedTerminationSignal(signal_number)

    for signal_number in (signal.SIGHUP, signal.SIGTERM):
        previous = signal.getsignal(signal_number)
        if previous == signal.SIG_IGN:
            continue
        previous_handlers[signal_number] = previous
        signal.signal(signal_number, raise_termination)
    return previous_handlers


def restore_managed_termination_handlers(
    previous_handlers: Mapping[int, object],
) -> None:
    """Restore signal dispositions changed for one managed command."""

    for signal_number, previous in previous_handlers.items():
        signal.signal(signal_number, previous)


def utc_now_iso() -> str:
    """Return a UTC timestamp suitable for JSON metadata and reports."""

    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def eprint(message: str) -> None:
    """Send operator-facing status output to stderr."""

    print(message, file=sys.stderr, flush=True)


def detect_npm_binaries() -> Tuple[str, str]:
    """Return platform-correct executable names for npm and npx."""

    if os.name == "nt":
        return "npm.cmd", "npx.cmd"
    return "npm", "npx"


def ensure_command_exists(name: str) -> None:
    """Raise a clear error when a required external command is missing."""

    if shutil.which(name) is None:
        raise CodexWranglerError(
            "Required command {!r} was not found in PATH. Install Node.js/npm "
            "first and rerun this script.".format(name)
        )


def run_command(
    command: Sequence[str],
    cwd: str,
    capture_output: bool = False,
    env: Optional[Mapping[str, str]] = None,
    timeout_seconds: Optional[int] = None,
) -> subprocess.CompletedProcess:
    """Run a subprocess with bounded whole-group interruption handling."""

    if env is not None and "NPM_CONFIG_CACHE" in env:
        eprint(
            "[codex-wrangler] Environment: NPM_CONFIG_CACHE={}".format(
                env["NPM_CONFIG_CACHE"]
            )
        )
    eprint("[codex-wrangler] Running: {}".format(" ".join(command)))
    if timeout_seconds is not None:
        eprint("[codex-wrangler] Timeout: {}s".format(timeout_seconds))
    popen_kwargs = {
        "cwd": cwd,
        "env": dict(env) if env is not None else None,
        "text": True,
    }
    if capture_output:
        popen_kwargs.update({"stdout": subprocess.PIPE, "stderr": subprocess.PIPE})
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        popen_kwargs["start_new_session"] = True
    previous_handlers = install_managed_termination_handlers()
    process: Optional[subprocess.Popen] = None
    try:
        try:
            process = subprocess.Popen(list(command), **popen_kwargs)
        except OSError as exc:
            raise CodexWranglerError(
                "Failed to start command {}: {}".format(" ".join(command), exc)
            ) from exc
        started_at = time.monotonic()
        deadline = started_at + timeout_seconds if timeout_seconds is not None else None
        next_heartbeat = started_at + COMMAND_HEARTBEAT_SECONDS
        while True:
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                terminate_process_group(process)
                raise CodexWranglerError(
                    "Command timed out after {}s: {}".format(
                        timeout_seconds,
                        " ".join(command),
                    )
                )
            deadline_is_next = deadline is not None and deadline <= next_heartbeat
            next_event = deadline if deadline_is_next else next_heartbeat
            wait_seconds = max(0.001, next_event - now)
            try:
                stdout, stderr = process.communicate(timeout=wait_seconds)
                break
            except subprocess.TimeoutExpired as exc:
                if deadline_is_next:
                    terminate_process_group(process)
                    raise CodexWranglerError(
                        "Command timed out after {}s: {}".format(
                            timeout_seconds,
                            " ".join(command),
                        )
                    ) from exc
                elapsed = max(0, int(time.monotonic() - started_at))
                remaining = (
                    "{}s remaining".format(max(0, int(deadline - time.monotonic())))
                    if deadline is not None
                    else "no overall timeout"
                )
                eprint(
                    "[codex-wrangler] Still running after {}s ({}): {}".format(
                        elapsed,
                        remaining,
                        " ".join(command),
                    )
                )
                next_heartbeat += COMMAND_HEARTBEAT_SECONDS
            except KeyboardInterrupt:
                terminate_process_group(process)
                raise
    except _ManagedTerminationSignal as exc:
        # Restore the process's normal signal disposition before group cleanup
        # so cleanup syscalls cannot recursively raise the managed exception.
        restore_managed_termination_handlers(previous_handlers)
        previous_handlers = {}
        if process is not None:
            terminate_process_group(process)
        try:
            signal_name = signal.Signals(exc.signal_number).name
        except ValueError:
            signal_name = str(exc.signal_number)
        raise CodexWranglerError(
            "Command interrupted by {}; its managed process group was "
            "terminated: {}".format(signal_name, " ".join(command))
        ) from exc
    finally:
        restore_managed_termination_handlers(previous_handlers)
    completed = subprocess.CompletedProcess(
        list(command),
        process.returncode,
        stdout,
        stderr,
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip() if completed.stderr else ""
        detail = "\n{}".format(stderr) if stderr else ""
        raise CodexWranglerError(
            "Command failed with exit code {}: {}{}".format(
                completed.returncode,
                " ".join(command),
                detail,
            )
        )
    return completed


def terminate_process_group(process: subprocess.Popen) -> None:
    """Stop one managed command and descendants as completely as possible."""

    if os.name == "nt":
        try:
            if process.poll() is None:
                process.send_signal(signal.CTRL_BREAK_EVENT)
                process.wait(timeout=2)
                return
        except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
            pass
        return

    process_group_id = process.pid
    try:
        os.killpg(process_group_id, signal.SIGTERM)
    except KeyboardInterrupt:
        # The originating Ctrl-C may be followed by another while cleanup is
        # already running. Retry once because the signal syscall may not have
        # reached every descendant before Python raised.
        try:
            os.killpg(process_group_id, signal.SIGTERM)
        except (OSError, KeyboardInterrupt):
            pass
    except ProcessLookupError:
        return
    except OSError:
        pass

    grace_deadline = time.monotonic() + 2.0
    interrupted_again = False
    while time.monotonic() < grace_deadline and not interrupted_again:
        try:
            process.poll()
            os.killpg(process_group_id, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            pass
        except OSError:
            break
        try:
            time.sleep(0.05)
        except KeyboardInterrupt:
            interrupted_again = True

    try:
        os.killpg(process_group_id, signal.SIGKILL)
    except KeyboardInterrupt:
        try:
            os.killpg(process_group_id, signal.SIGKILL)
        except (OSError, KeyboardInterrupt):
            pass
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except (OSError, subprocess.TimeoutExpired, KeyboardInterrupt):
        try:
            process.kill()
        except (OSError, KeyboardInterrupt):
            pass
