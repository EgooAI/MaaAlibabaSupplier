"""Remote execution lifecycle tests with a replaced process boundary."""

from __future__ import annotations

import io
import json
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from backend.app import diagnostics
from backend.app.api import main
from backend.app.shared.backend import execution_service as module
from backend.tests.auth_client import authenticate


class FakeProcess:
    def __init__(self, stdout: bytes = b"", stderr: bytes = b"", code: int = 0, hang: bool = False):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.code = code
        self.hang = hang
        self.terminated = False

    def wait(self, timeout=None):
        if self.terminated:
            return -15
        return None if self.hang else self.code

    def kill(self):
        self.terminated = True


class FakeClock:
    def __init__(self, step: float = 0.0):
        self.value = 1000.0
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


def install_spawn(monkeypatch, *processes):
    pending = list(processes)
    seen = []

    def spawn(script_path, cwd, env):
        process = pending.pop(0) if pending else FakeProcess()
        seen.append(SimpleNamespace(script=script_path, cwd=cwd, env=env, process=process))
        return module._Spawned(process)

    monkeypatch.setattr(module, "_spawn", spawn)
    return seen


def enable(service):
    generation = service.state()["generation"]
    return service.set_enabled(True, generation=generation)


def submit(service, script="print('ok')\n", *, run_id="a" * 32, timeout_s=30):
    state = service.state()
    return service.submit(run_id=run_id, start_token=state["start_token"],
                          generation=state["generation"], script=script, timeout_s=timeout_s) 


def wait_terminal(service, run_id, timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        record = service.get(run_id)
        if record["status"] in {"succeeded", "failed", "cancelled", "timeout", "interrupted"}:
            return record
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state")


@pytest.fixture
def service():
    instance = module.ExecutionService()
    try:
        yield instance
    finally:
        instance.shutdown()


def test_service_starts_disabled_and_each_instance_starts_disabled(service, monkeypatch):
    assert service.state()["enabled"] is False
    enable(service)
    assert service.state()["enabled"] is True
    # A restarted backend constructs a new service; persistence never re-enables it.
    replacement = module.ExecutionService()
    try:
        assert replacement.state()["enabled"] is False
    finally:
        replacement.shutdown()


def test_stale_switch_generation_and_start_token_are_rejected(service, monkeypatch):
    install_spawn(monkeypatch)
    stale = service.state()
    enable(service)
    disabled = service.state()
    service.set_enabled(False, generation=disabled["generation"])
    enabled = enable(service)
    assert enabled["generation"] != stale["generation"]

    with pytest.raises(module.ExecutionRejected) as stale_generation:
        service.submit(run_id="b" * 32, start_token=enabled["start_token"],
                       generation=stale["generation"], script="print(1)", timeout_s=10)
    assert stale_generation.value.reason == "execution_generation_changed"

    with pytest.raises(module.ExecutionRejected) as stale_token:
        service.submit(run_id="c" * 32, start_token="0" * 32,
                       generation=enabled["generation"], script="print(1)", timeout_s=10)
    assert stale_token.value.reason == "execution_start_token_changed"

    with pytest.raises(module.ExecutionRejected) as disabled_reason:
        disabled_service = module.ExecutionService()
        try:
            disabled_service.submit(run_id="d" * 32,
                                    start_token=disabled_service.state()["start_token"],
                                    generation=disabled_service.state()["generation"],
                                    script="print(1)", timeout_s=10)
        finally:
            disabled_service.shutdown()
    assert disabled_reason.value.reason == "execution_disabled"


def test_submission_requires_bounded_script_and_timeout(service, monkeypatch):
    install_spawn(monkeypatch)
    enable(service)
    state = service.state()
    with pytest.raises(module.ExecutionRejected) as oversized:
        service.submit(run_id="e" * 32, start_token=state["start_token"], generation=state["generation"],
                       script="x" * (module.MAX_SCRIPT_BYTES + 1), timeout_s=10)
    assert oversized.value.reason == "execution_script_invalid"
    with pytest.raises(module.ExecutionRejected) as timeout:
        service.submit(run_id="f" * 32, start_token=state["start_token"], generation=state["generation"],
                       script="print(1)", timeout_s=module.MAX_TIMEOUT_S + 1)
    assert timeout.value.reason == "execution_timeout_invalid"


def test_duplicate_id_is_idempotent_and_conflicting_payload_is_rejected(service, monkeypatch):
    seen = install_spawn(monkeypatch, FakeProcess(stdout=b"first\n"), FakeProcess(stdout=b"second\n"))
    enable(service)
    first = submit(service, "print('first')")
    wait_terminal(service, first["id"])
    again = submit(service, "print('first')")
    assert again["id"] == first["id"]
    assert len(seen) == 1
    with pytest.raises(module.ExecutionRejected) as conflict:
        submit(service, "print('different')")
    assert conflict.value.reason == "execution_duplicate_conflict"


def test_only_one_active_run_and_cancel_terminates_it(service, monkeypatch):
    process = FakeProcess(hang=True)
    install_spawn(monkeypatch, process)
    enable(service)
    first = submit(service, "while True: pass", run_id="1" * 32)
    with pytest.raises(module.ExecutionRejected) as busy:
        submit(service, "print(2)", run_id="2" * 32)
    assert busy.value.reason == "execution_busy"
    record = service.cancel(first["id"])
    assert record["cancel_requested"] is True
    terminal = wait_terminal(service, first["id"])
    assert terminal["status"] == "cancelled" and process.terminated
    # A terminal cancellation is idempotent rather than an error.
    assert service.cancel(first["id"])["status"] == "cancelled"


def test_output_offsets_and_exit_status_are_recorded(service, monkeypatch):
    install_spawn(monkeypatch, FakeProcess(stdout="第一行\n第二行\n".encode(), stderr=b"warning\n", code=3))
    enable(service)
    run = submit(service, "print('both')")
    terminal = wait_terminal(service, run["id"])
    assert terminal["status"] == "failed" and terminal["exit_code"] == 3
    assert terminal["error"] == "nonzero_exit"
    detail = service.get(run["id"])
    assert detail["stdout"] == "第一行\n第二行\n"
    assert detail["stderr"] == "warning\n"
    tail = service.get(run["id"], stdout_offset=4)
    assert tail["stdout"] == "第二行\n"
    assert tail["stdout_offset"] == 4 and tail["stdout_total"] == 8


def test_output_is_capped_while_the_process_still_exits(service, monkeypatch):
    monkeypatch.setattr(module, "MAX_OUTPUT_CHARS", 5)
    install_spawn(monkeypatch, FakeProcess(stdout=b"abcdefghij", stderr=b"1234567"))
    enable(service)
    terminal = wait_terminal(service, submit(service, "print('x')")["id"])
    assert terminal["status"] == "succeeded"
    assert (terminal["stdout_chars"], terminal["stderr_chars"]) == (5, 5)
    assert terminal["stdout_truncated"] is True and terminal["stderr_truncated"] is True
    assert service.get(terminal["id"])["stdout"] == "abcde"


def test_unconfirmed_termination_is_reported_and_blocks_reuse(service, monkeypatch):
    class StuckProcess(FakeProcess):
        def wait(self, timeout=None):
            return None

    install_spawn(monkeypatch, StuckProcess(hang=True), FakeProcess())
    enable(service)
    first = submit(service, "stuck", run_id="5" * 32)
    service.cancel(first["id"])
    terminal = wait_terminal(service, first["id"])
    assert terminal["status"] == "failed" and terminal["error"] == "termination_unconfirmed"
    with pytest.raises(module.ExecutionRejected) as busy:
        submit(service, "next", run_id="6" * 32)
    assert busy.value.reason == "execution_busy"


def test_timeout_terminates_the_process_tree(monkeypatch):
    process = FakeProcess(hang=True)
    install_spawn(monkeypatch, process)
    service = module.ExecutionService(clock=FakeClock(step=2.0))
    try:
        enable(service)
        terminal = wait_terminal(service, submit(service, "while True: pass", timeout_s=1)["id"])
        assert terminal["status"] == "timeout" and terminal["error"] == "timeout"
        assert process.terminated
    finally:
        service.shutdown()


def test_restart_marks_unfinished_runs_interrupted():
    directory = module.paths.execution_dir()
    directory.mkdir(parents=True, exist_ok=True)
    record = {
        "id": "9" * 32, "status": "running", "created_at": time.time() - 5, "started_at": time.time() - 5,
        "finished_at": None, "timeout_s": 30, "exit_code": None, "cancel_requested": False,
        "script_sha256": "0" * 64, "script_bytes": 10, "error": None,
        "stdout_chars": 0, "stderr_chars": 0, "stdout_truncated": False, "stderr_truncated": False,
    }
    (directory / "runs.json").write_text(json.dumps([record]), encoding="utf-8")
    service = module.ExecutionService()
    try:
        restored = service.get("9" * 32)
        assert restored["status"] == "interrupted"
        assert restored["error"] == "restart_interrupted"
        assert service.state()["enabled"] is False
    finally:
        service.shutdown()


def test_history_is_bounded_by_count_and_age(service):
    service.initialize()
    now = time.time()
    for index in range(module.MAX_RUNS + 5):
        run_id = f"{index:032x}"
        service._runs[run_id] = {
            "id": run_id, "status": "succeeded", "created_at": now - index, "started_at": now - index,
            "finished_at": now - index, "timeout_s": 30, "exit_code": 0, "cancel_requested": False,
            "script_sha256": "0" * 64, "script_bytes": 1, "error": None,
            "stdout_chars": 0, "stderr_chars": 0, "stdout_truncated": False, "stderr_truncated": False,
        }
    stale = f"{999:032x}"
    service._runs[stale] = {**service._runs[f"{0:032x}"], "id": stale,
                            "created_at": now - module.MAX_AGE_S - 1}
    service._prune_locked(now)
    assert len(service._runs) == module.MAX_RUNS
    assert stale not in service._runs


def test_diagnostics_summary_never_creates_storage(monkeypatch, tmp_path):
    monkeypatch.setattr(module, "_INSTANCE", None)
    assert module.observe_execution() is None
    monkeypatch.setattr(module, "_INSTANCE", module.ExecutionService())
    summary = module.observe_execution()
    assert summary == {"enabled": False, "active": False, "runs": 0,
                       "succeeded": 0, "failed": 0, "interrupted": 0}
    assert not module.paths.execution_dir().exists()


def test_api_requires_authentication_and_reports_switch_state(monkeypatch):
    monkeypatch.setattr(module, "_INSTANCE", None)
    install_spawn(monkeypatch, FakeProcess(stdout="hello\n".encode()))
    with TestClient(main.create_app()) as client:
        assert client.get("/api/app/diagnostics/execution").status_code == 401
        authenticate(client)
        # Instance-level diagnostics must work without a seller-scoped epoch.
        client.headers.pop("X-Account-Epoch", None)
        state = client.get("/api/app/diagnostics/execution").json()["data"]
        assert state["enabled"] is False and len(state["start_token"]) == 32
        rejected = client.post("/api/app/diagnostics/execution/runs", json={
            "id": "7" * 32, "start_token": state["start_token"],
            "generation": state["generation"], "script": "print(1)", "timeout_s": 10,
        })
        assert rejected.status_code == 409 and rejected.json()["msg"] == "远程执行尚未开启。"
        enabled = client.put("/api/app/diagnostics/execution", json={
            "enabled": True, "generation": state["generation"],
        }).json()["data"]
        assert enabled["enabled"] is True
        submitted = client.post("/api/app/diagnostics/execution/runs", json={
            "id": "8" * 32, "start_token": enabled["start_token"],
            "generation": enabled["generation"], "script": "print('hello')", "timeout_s": 10,
        })
        assert submitted.status_code == 200, submitted.text
        deadline = time.monotonic() + 5
        detail = submitted.json()["data"]
        while time.monotonic() < deadline:
            detail = client.get("/api/app/diagnostics/execution/runs/" + "8" * 32).json()["data"]
            if detail["status"] == "succeeded":
                break
            time.sleep(0.02)
        assert detail["status"] == "succeeded" and detail["stdout"] == "hello\n"
        listed = client.get("/api/app/diagnostics/execution/runs").json()["data"]["runs"]
        assert listed[0]["id"] == "8" * 32
        disabled = client.put("/api/app/diagnostics/execution", json={
            "enabled": False, "generation": enabled["generation"],
        }).json()["data"]
        assert disabled["enabled"] is False
        assert client.get("/api/app/diagnostics/execution/runs/" + "9" * 32).status_code == 404
