"""One durable FIFO for render and media jobs, with a host-wide heavy-work gate."""

from contextlib import ExitStack
from datetime import datetime
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from . import engine, processes, resources
from .locking import file_lock


def root():
    path = engine.PROJECTS.parent / "task_queue"
    path.mkdir(parents=True, exist_ok=True)
    return path


def records():
    from . import render_queue, media_jobs

    rows = [{**r, "kind": "render"} for r in render_queue.records()]
    rows += [{**r, "kind": "media"} for r in media_jobs.records()]

    def order(row):
        stamp = row["created_at"]
        return (
            datetime.fromisoformat(stamp).timestamp() if isinstance(stamp, str) else stamp,
            row["job_id"],
        )

    return sorted(rows, key=order)


def start_worker():
    env = dict(os.environ)
    env["VIDEO_EDITER_DATA_DIR"] = str(engine.PROJECTS.parent)
    env["VIDEO_EDITER_RESOURCE_DIR"] = str(resources.root())
    options = resources.child_options({"env": env})
    if os.name != "nt":
        options["start_new_session"] = True
    command = [sys.executable, "-c", "from video_editer.task_queue import worker; worker()"]
    with (root() / "worker.log").open("ab") as log:
        child = subprocess.Popen(
            command,
            cwd=str(Path(__file__).resolve().parent.parent),
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=log,
            **options,
        )
    threading.Thread(target=child.wait, daemon=True, name="video-task-reaper").start()
    return {
        "worker_pid": child.pid,
        "queue_policy": "unified media/render FIFO; host heavy concurrency = 1",
    }


def resume_pending():
    """Resume durable queued work when the MCP host reconnects; never retry failures.

    Some hosts terminate their entire process job/group on disconnect, including
    a nominally detached child. Queue durability must not depend on that child
    surviving. Running/interrupted attempts still require an explicit retry.
    """
    with file_lock(root() / "dispatch.lock"):
        pending = [row for row in records() if row["status"] == "queued"]
        if not pending:
            return {"queued_jobs": 0}
        return {"queued_jobs": len(pending), **start_worker()}


def execute(row):
    from . import jobs, media_jobs
    from .mcp_server import _execute_attempt

    pid, jid = row["project_id"], row["job_id"]
    folder = jobs.folder(pid, jid) if row["kind"] == "render" else media_jobs.folder(pid, jid)
    previous_wait = object()

    def waiting(reason):
        nonlocal previous_wait
        key = reason.get("reason") if reason else None
        if key == previous_wait:
            return
        previous_wait = key
        with file_lock(folder / "running.lock"):
            state = engine.read_json(folder / "state.json")
            if state["status"] != "queued":
                return
            state["resource_wait"] = reason
            engine.write_json(folder / "state.json", state)

    def event(value):
        state = engine.read_json(folder / "state.json")
        events = state.setdefault("resource_events", [])
        if len(events) < 200:
            events.append(value)
        engine.write_json(folder / "state.json", state)

    with (
        processes.cancellation(folder / "cancel.json"),
        resources.heavy_slot(waiting, event) as slot,
    ):
        if row["kind"] == "media":
            media_jobs.execute(pid, jid)
        else:
            with file_lock(folder / "running.lock"):
                latest = engine.read_json(folder / "state.json")
                if latest["status"] != "queued":
                    return
                attempt = jobs.Attempt.load(pid, jid)
                attempt.state["execution_resources"] = slot["policy"]
                _execute_attempt(attempt)


def worker():
    directory = root()
    resources.worker_setup()
    with ExitStack() as stack:
        try:
            stack.enter_context(file_lock(directory / "worker.lock"))
        except ValueError:
            return
        while True:
            pending = [r for r in records() if r["status"] == "queued"]
            if not pending:
                try:
                    with file_lock(directory / "dispatch.lock"):
                        if not any(r["status"] == "queued" for r in records()):
                            stack.close()
                            return
                except ValueError:
                    time.sleep(0.1)
                continue
            row = pending[0]
            try:
                execute(row)
            except Exception as exc:
                from . import jobs, media_jobs

                folder = (jobs if row["kind"] == "render" else media_jobs).folder(
                    row["project_id"], row["job_id"]
                )
                with file_lock(folder / "running.lock"):
                    state = engine.read_json(folder / "state.json")
                    if state["status"] == "queued":
                        state.update(
                            status="cancelled"
                            if isinstance(exc, processes.RenderCancelled)
                            else "failed",
                            error={"type": type(exc).__name__, "message": str(exc)[-3000:]},
                            finished_at=time.time(),
                        )
                        engine.write_json(folder / "state.json", state)
