"""Verify cached quality-gate discovery never traverses protected local state."""

import importlib.util
from pathlib import Path
import socket
import sys

import pytest


def load_wrapper():
    """Load the real repository overlay without invoking its CLI."""

    path = Path(__file__).resolve().parents[1] / "scripts/run_quality_gate_cached.py"
    spec = importlib.util.spec_from_file_location("local_quality_gate_overlay", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "local_root", [".local", ".codex-local", ".codex-home", ".codex", ".agents"]
)
def test_cached_gate_prunes_local_state_before_walk(tmp_path, monkeypatch, local_root):
    """Pruning remains effective even when Git-ignore discovery is unavailable."""

    wrapper = load_wrapper()
    main = wrapper.load_helper_main()
    helper = sys.modules[main.__module__]
    source = tmp_path / "source.py"
    source.write_text("print('candidate')\n")
    protected = tmp_path / local_root
    protected.mkdir()
    (protected / "private-state").write_bytes(b"local fixture state\n")
    walked = []
    real_walk = helper.os.walk

    def observing_walk(*args, **kwargs):
        """Observe actual traversal without substituting discovery logic."""

        for result in real_walk(*args, **kwargs):
            walked.append(Path(result[0]))
            yield result

    monkeypatch.setattr(helper.os, "walk", observing_walk)
    files = helper.iter_scope_files(tmp_path, ["."], None, [], [])

    assert files == [source]
    assert protected not in walked
    assert helper.fingerprint_files(tmp_path, files, "entropy_check")[1] == 1


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix socket fixture")
def test_cached_gate_does_not_fingerprint_runtime_socket(tmp_path):
    """An ignored live control socket cannot break candidate verification."""

    main = load_wrapper().load_helper_main()
    helper = sys.modules[main.__module__]
    runtime = tmp_path / ".local/codex-home/.codex"
    runtime.mkdir(parents=True)
    source = tmp_path / "candidate.py"
    source.write_text("print('candidate')\n")
    with socket.socket(socket.AF_UNIX) as control:
        control.bind(str(runtime / "control.sock"))
        files = helper.iter_scope_files(tmp_path, ["."], None, [], [])
        assert files == [source]
        assert helper.fingerprint_files(tmp_path, files, "entropy_check")[1] == 1


def test_cached_gate_excludes_local_policy_input(tmp_path):
    """The restricted-name source is never read as publishable repository data."""

    main = load_wrapper().load_helper_main()
    helper = sys.modules[main.__module__]
    policy = tmp_path / ".theknowledge-restricted-names.local"
    policy.write_bytes(b"local fixture policy\n")
    assert helper.iter_scope_files(tmp_path, ["."], None, [], []) == []
