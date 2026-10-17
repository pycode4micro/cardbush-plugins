"""Portable policy, process isolation, mixed FIFO and GPU fallback contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from video_editer import (
    engine,
    resources,
    encoders,
    processes,
    jobs,
    render_queue,
    media_jobs,
    task_queue,
)
from video_editer import mcp_server as s


class ResourceFixture(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="video-resources-test-"))
        self.env = patch.dict(
            os.environ, {"VIDEO_EDITER_RESOURCE_DIR": str(self.root / "resources")}
        )
        self.env.start()
        resources.configure(
            {
                "encoder": "cpu",
                "cpu_threads": 2,
                "reserve_memory_percent": 5,
                "min_free_memory_mb": 128,
                "max_system_cpu_percent": 100,
            }
        )

    def tearDown(self):
        self.env.stop()


class ResourceTests(ResourceFixture):
    def test_reconnect_only_resumes_queued_jobs_without_resubmitting(self):
        queue_root = self.root / "queue"
        queue_root.mkdir()
        retained = [{"job_id": "old_queued", "status": "queued"},
                    {"job_id": "old_failed", "status": "failed"},
                    {"job_id": "old_running", "status": "running"}]
        with (
            patch.object(task_queue, "root", return_value=queue_root),
            patch.object(task_queue, "records", return_value=retained),
            patch.object(task_queue, "start_worker", return_value={"worker_pid": 123}) as start,
            patch.object(media_jobs, "submit", side_effect=AssertionError("Must reuse job IDs")),
        ):
            self.assertEqual(task_queue.resume_pending(), {"queued_jobs": 1, "worker_pid": 123})
            start.assert_called_once()
            start.reset_mock()
            retained.pop(0)
            self.assertEqual(task_queue.resume_pending(), {"queued_jobs": 0})
            start.assert_not_called()
            self.assertEqual([row["status"] for row in retained], ["failed", "running"])

    def test_mcp_startup_recovers_queue_and_retains_tools_after_recovery_error(self):
        for failure in (None, OSError("test dispatcher failure")):
            with (
                patch.object(task_queue, "resume_pending", side_effect=failure) as recover,
                patch.object(s.mcp, "run") as serve,
            ):
                s.main()
                recover.assert_called_once()
                serve.assert_called_once_with(transport="stdio")

    def test_encoder_diagnostic_retains_initial_cause_in_long_logs(self):
        log = (
            "banner\n[h264_qsv] Current frame rate is unsupported\n"
            + "frame progress\n" * 300
        )
        self.assertIn("frame rate is unsupported", encoders.diagnostic(log))

    def test_state_reader_retries_transient_windows_replace_conflict(self):
        with (
            patch.object(
                Path,
                "read_text",
                side_effect=[PermissionError(), PermissionError(), '{"ok":true}'],
            ),
            patch.object(engine.time, "sleep") as pause,
        ):
            self.assertEqual(engine.read_json(self.root / "state.json"), {"ok": True})
            self.assertEqual(pause.call_count, 2)
        with (
            patch.object(Path, "read_text", side_effect=PermissionError()),
            patch.object(engine.time, "sleep") as pause,
        ):
            with self.assertRaises(PermissionError):
                engine.read_json(self.root / "state.json")
            self.assertEqual(pause.call_count, 39)

    def test_runtime_memory_excess_stops_only_the_owned_child(self):
        resources.configure({"max_worker_memory_mb": 128})
        spawned = []
        real_popen = processes.popen

        def popen(*args, **kwargs):
            child = real_popen(*args, **kwargs)
            spawned.append(child)
            return child

        with patch.object(processes, "popen", side_effect=popen):
            with self.assertRaises(resources.ResourceLimitError):
                processes.run(
                    [
                        sys.executable,
                        "-c",
                        "import time; data=bytearray(160*1024*1024); time.sleep(8)",
                    ],
                    capture_output=True,
                    check=True,
                    timeout=5,
                )
        self.assertEqual(len(spawned), 1)
        self.assertIsNotNone(spawned[0].poll())
        with resources.heavy_slot():
            pass

    def test_worker_applies_new_cpu_budget_at_the_next_slot(self):
        with (
            patch.object(resources, "_worker_pid", os.getpid()),
            patch.object(resources, "_worker_affinity", [0, 1, 2, 3]),
            patch.object(resources.os, "cpu_count", return_value=4),
            patch.object(resources, "low_priority") as priority,
        ):
            resources.configure({"cpu_threads": 1})
            with resources.heavy_slot():
                resources.configure({"cpu_threads": 2})
                self.assertEqual(resources.current_policy()["cpu_threads"], 1)
            with resources.heavy_slot():
                self.assertEqual(resources.current_policy()["cpu_threads"], 2)
            self.assertEqual([c.args[1] for c in priority.call_args_list], [1, 2])
            self.assertEqual(priority.call_args.args[2], [0, 1, 2, 3])

    def test_policy_rejects_bad_changes_atomically(self):
        before = (resources.root() / "policy.json").read_bytes()
        for setting in (
            {"heavy_concurrency": 2},
            {"cpu_threads": True},
            {"reserve_memory_percent": 0},
            {"encoder": "fake_gpu"},
            {"unknown": 1},
            {"max_worker_memory_mb": 1},
        ):
            with self.subTest(setting=setting), self.assertRaises(ValueError):
                resources.configure(setting)
            self.assertEqual(before, (resources.root() / "policy.json").read_bytes())

    def test_auto_threads_reserve_cpu_on_different_hosts(self):
        for cores, expected in ((1, 1), (2, 1), (8, 2), (16, 4), (128, 4)):
            with patch.object(resources.os, "cpu_count", return_value=cores):
                self.assertEqual(
                    resources.effective({**resources.DEFAULTS})["cpu_threads"], expected
                )

    def test_memory_admission_wait_can_be_cancelled(self):
        cancel = self.root / "cancel.json"
        waits = []

        def waiting(value):
            waits.append(value)
            cancel.write_text("{}")

        with (
            patch.object(resources, "admission", return_value={"reason": "low_memory"}),
            patch.object(resources.time, "sleep"),
            processes.cancellation(cancel),
        ):
            with self.assertRaises(processes.RenderCancelled):
                with resources.heavy_slot(waiting):
                    self.fail("Must not admit low-memory work")
        self.assertEqual(waits, [{"reason": "low_memory"}])
        cancel.unlink()
        with resources.heavy_slot():
            pass  # Cancellation released the global lock.

    def test_threads_bound_inputs_outputs_and_metadata_command(self):
        command = resources.limit_ffmpeg(
            ["ffmpeg", "-i", "one.mp4", "-i", "two.mp4", "-c:v", "libx264", "out.mp4"]
        )
        for index, token in enumerate(command):
            if token == "-i":
                self.assertEqual(command[index - 2 : index], ["-threads", "2"])
        self.assertEqual(command[-3:], ["-threads", "2", "out.mp4"])
        metadata = resources.limit_ffmpeg(
            ["ffmpeg", "-hide_banner", "-i", "source.mp4"]
        )
        self.assertEqual(metadata[-2:], ["-i", "source.mp4"])

    def test_resource_lock_serializes_different_data_directories_and_processes(self):
        code = """import json,sys,time
from pathlib import Path
from video_editer import resources
with resources.heavy_slot():
 start=time.time();time.sleep(.3);end=time.time()
 Path(sys.argv[1]).write_text(json.dumps([start,end]))
"""
        children = []
        for index in range(2):
            env = {
                **os.environ,
                "VIDEO_EDITER_DATA_DIR": str(self.root / f"data-{index}"),
            }
            children.append(
                subprocess.Popen(
                    [sys.executable, "-c", code, str(self.root / f"{index}.json")],
                    env=env,
                )
            )
        for child in children:
            self.assertEqual(child.wait(timeout=30), 0)
        spans = sorted(
            json.loads((self.root / f"{i}.json").read_text()) for i in range(2)
        )
        self.assertLessEqual(spans[0][1], spans[1][0])

    def test_encoder_runtime_probe_tries_different_vendors(self):
        resources.configure({"encoder": "auto"})
        binary = self.root / "ffmpeg"
        binary.write_bytes(b"binary")
        calls = []

        def run(command, **kwargs):
            calls.append(command)
            if "-encoders" in command:
                return subprocess.CompletedProcess(
                    command, 0, b" V..... h264_nvenc\n V..... h264_qsv\n", b""
                )
            selected = command[command.index("-c:v") + 1]
            return subprocess.CompletedProcess(
                command, int(selected != "h264_qsv"), b"", b"no compatible device"
            )

        with (
            resources.heavy_slot(),
            patch.object(encoders.subprocess, "run", side_effect=run),
        ):
            result = encoders.select(binary)
        self.assertEqual(result["encoder"], "h264_qsv")
        self.assertTrue(result["gpu"])
        self.assertEqual(len(calls), 3)

    def test_all_vendors_pass_only_after_synthetic_probe(self):
        binary = self.root / "ffmpeg"
        binary.write_bytes(b"binary")
        for encoder in encoders.GPU_ENCODERS:
            resources.configure({"encoder": encoder})

            def run(command, **kwargs):
                return subprocess.CompletedProcess(
                    command,
                    0,
                    (" V..... " + encoder + "\n").encode()
                    if "-encoders" in command
                    else b"",
                    b"",
                )

            with (
                resources.heavy_slot(),
                patch.object(encoders.subprocess, "run", side_effect=run) as invoked,
            ):
                result = encoders.select(binary, refresh=True)
            self.assertEqual(result["encoder"], encoder)
            self.assertEqual(invoked.call_count, 2)

    def test_strict_gpu_never_silently_falls_back(self):
        resources.configure({"encoder": "gpu_required"})
        binary = self.root / "ffmpeg"
        binary.write_bytes(b"binary")
        with (
            resources.heavy_slot(),
            patch.object(
                encoders.subprocess,
                "run",
                return_value=subprocess.CompletedProcess([], 0, b"", b""),
            ),
        ):
            with self.assertRaisesRegex(ValueError, "fallback is disabled"):
                encoders.select(binary)
        self.assertIsNone(encoders.cached()["encoder"])

    def test_gpu_failure_preserves_partial_then_retries_cpu_once(self):
        resources.configure({"encoder": "auto"})
        target = self.root / "out.mp4"
        gpu = {"encoder": "h264_nvenc", "gpu": True}
        cpu = {"encoder": "libx264", "gpu": False}
        command = ["ffmpeg", "-n", "-i", "test.mp4", "-c:v", "libx264", str(target)]

        def encode(args, **kwargs):
            if not target.exists():
                target.write_bytes(b"gpu partial")
                return subprocess.CompletedProcess(
                    args, 1, b"", b"Error while opening encoder"
                )
            raise AssertionError("Partial output was not moved")

        def success(args, **kwargs):
            target.write_bytes(b"cpu complete")
            return subprocess.CompletedProcess(args, 0, b"", b"")

        sequence = iter([encode, success])
        with (
            patch.object(
                encoders, "prepare", side_effect=[(command, gpu), (command, cpu)]
            ),
            patch.object(
                processes,
                "_run_once",
                side_effect=lambda *a, **kw: next(sequence)(*a, **kw),
            ) as called,
        ):
            processes.run(command, capture_output=True, check=True)
        self.assertEqual(called.call_count, 2)
        self.assertEqual(target.read_bytes(), b"cpu complete")
        self.assertEqual(
            next(self.root.glob("out.mp4.gpu-failed-*")).read_bytes(), b"gpu partial"
        )

    def test_non_gpu_failure_and_existing_output_are_not_retried(self):
        target = self.root / "kept.mp4"
        target.write_bytes(b"user data")
        command = ["ffmpeg", "-i", "missing.mp4", "-c:v", "libx264", str(target)]
        gpu = {"encoder": "h264_nvenc", "gpu": True}
        with (
            patch.object(encoders, "prepare", return_value=(command, gpu)),
            patch.object(
                processes,
                "_run_once",
                return_value=subprocess.CompletedProcess(
                    command, 1, b"", b"Error while opening encoder"
                ),
            ) as called,
        ):
            with self.assertRaises(subprocess.CalledProcessError):
                processes.run(command, check=True)
        self.assertEqual(called.call_count, 1)
        self.assertEqual(target.read_bytes(), b"user data")


class MixedQueueTests(ResourceFixture):
    def setUp(self):
        super().setUp()
        self.data = patch.object(engine, "PROJECTS", self.root / "data/projects")
        self.data.start()
        self.source = self.root / "source.mp4"
        processes.run(
            [
                engine.ffmpeg_bin(),
                "-nostdin",
                "-y",
                "-f",
                "lavfi",
                "-i",
                "color=blue:s=320x240:r=25:d=0.5",
                "-c:v",
                "libx264",
                str(self.source),
            ],
            capture_output=True,
            check=True,
            timeout=30,
        )
        self.pid = s.project_create("Synthetic queue verification")["project_id"]
        s.canvas_configure(self.pid, 320, 256)
        self.asset = s.media_import(self.pid, str(self.source))["asset"]["id"]
        s.clip_add(self.pid, self.asset, 0, 0.4)

    def tearDown(self):
        self.data.stop()
        super().tearDown()

    def test_mixed_fifo_and_retry_are_background_and_snapshot_bound(self):
        with (
            patch.object(render_queue, "start_worker", return_value={}),
            patch.object(media_jobs, "start_worker", return_value={}),
        ):
            with patch.object(
                jobs, "media_manifest", wraps=jobs.media_manifest
            ) as manifest:
                first = s.render_submit(self.pid)
                self.assertTrue(manifest.call_args.kwargs["defer"])
            middle = s.media_register(self.pid, str(self.source))
            last = s.render_submit(self.pid)
            s.render_cancel(self.pid, last["job_id"])
            retry = s.render_retry(self.pid, last["job_id"])
        self.assertEqual(retry["status"], "queued")
        self.assertEqual(retry["retry_of"], last["job_id"])
        combined = s.task_queue_list()
        self.assertEqual(combined["heavy_concurrency"], 1)
        self.assertEqual(
            [r["kind"] for r in combined["jobs"]],
            ["render", "media", "render", "render"],
        )
        self.assertIsNone(combined["next_offset"])
        s.clip_update(
            self.pid,
            s.timeline_get(self.pid)["timeline"]["tracks"]["main"][0]["id"],
            end=0.32,
        )
        with patch.object(resources, "worker_setup"):
            task_queue.worker()
        states = [
            s.render_job_status(self.pid, first["job_id"]),
            s.media_job_status(self.pid, middle["job_id"]),
            s.render_job_status(self.pid, retry["job_id"]),
        ]
        self.assertTrue(all(v["status"] == "succeeded" for v in states), states)

        def epoch(value):
            from datetime import datetime

            return (
                datetime.fromisoformat(value).timestamp()
                if isinstance(value, str)
                else value
            )

        self.assertLessEqual(
            epoch(states[0]["finished_at"]), epoch(states[1]["started_at"])
        )
        self.assertLessEqual(
            epoch(states[1]["finished_at"]), epoch(states[2]["started_at"])
        )
        self.assertEqual(states[2]["result"]["revision"], retry["revision"])
        self.assertLess(
            retry["revision"], s.timeline_get(self.pid)["timeline"]["revision"]
        )
        self.assertTrue(states[0]["resource_events"])

    def test_submit_does_not_hash_large_inputs_and_worker_checks_change(self):
        imported = Path(s.media_inspect(self.pid, self.asset)["materials"][0]["path"])
        real = jobs.digest

        def digest(path):
            if Path(path) == imported:
                raise AssertionError("Input hash must run in worker")
            return real(path)

        with (
            patch.object(jobs, "digest", side_effect=digest),
            patch.object(render_queue, "start_worker", return_value={}),
        ):
            submitted = s.render_submit(self.pid)
        with imported.open("ab") as stream:
            stream.write(b"changed")
        with patch.object(resources, "worker_setup"):
            task_queue.worker()
        state = s.render_job_status(self.pid, submitted["job_id"])
        self.assertEqual(state["status"], "failed")
        self.assertIn("Media changed", state["error"]["message"])


if __name__ == "__main__":
    unittest.main()
