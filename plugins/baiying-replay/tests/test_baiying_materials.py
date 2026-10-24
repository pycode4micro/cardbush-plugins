from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest
import requests

from baiying import client, material_service as jobs, media_validation as media, replay_service as replay


SCOPE = {"mobile": "13800000000", "account_id": "111", "product_id": "900"}


def clip(vid, fragment, *, qualified=True, duration=34):
    return {"video_id": vid, "fragment_id": fragment, "qualified": qualified,
            "duration_seconds": duration, "download_url": f"https://v3.zjcdn.com/{vid}.mp4", "cover_url": "cover.jpg"}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIYING_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("BAIYING_REPLAY_STATE_PATH", str(tmp_path / "state.sqlite3"))
    monkeypatch.setenv("BAIYING_COOKIE_CACHE_PATH", str(tmp_path / "cookies.sqlite3"))
    monkeypatch.setattr(client, "CACHE_PATH", tmp_path / "cookies.sqlite3")
    monkeypatch.setattr(media, "ffmpeg_binary", lambda: "test-ffmpeg")
    product = {"product_id": "900", "product_title": "开衫", "product_cover": "cover.jpg",
               "videos": {"1": clip("1", "101"), "2": clip("2", "102", qualified=False)},
               "fragments": {}, "linked_fragments": {"101", "102"}}
    scan = {"scan_complete": True, "pages_read": 1, "replay_records_read": 1, "replay_total": 1}
    downloads = []

    def fetch(payload):
        assert payload["account_id"] == "111" and payload["product_id"] == "900"
        return {"account_id": "111", "account_name": "店铺A"}, copy.deepcopy(product), dict(scan)

    def download(url, target, timeout, **kwargs):
        downloads.append(url)
        target.write_bytes(Path(url).stem.encode())
        return {"status": "downloaded", "path": str(target), "bytes": target.stat().st_size}

    def validate(path, **kwargs):
        content = Path(path).read_bytes()
        if content == b"broken":
            raise ValueError("broken video")
        duration = 80 if content.startswith(b"original") else 34
        return {"sha256": hashlib.sha256(content).hexdigest(), "duration_seconds": duration,
                "width": 720, "height": 1280, "fps": 25, "has_audio": True, "video_codec": "h264",
                "bytes": len(content), "full_decode_passed": True, "visual_signatures": []}

    monkeypatch.setattr(replay, "_product", fetch)
    monkeypatch.setattr(replay, "_download", download)
    monkeypatch.setattr(media, "validate_video", validate)
    monkeypatch.setattr(jobs, "_launch", jobs.run_worker)
    monkeypatch.setattr(client, "trigger_auto_clip", lambda _: pytest.fail("Unrequested provider mutation"))
    return {"product": product, "scan": scan, "downloads": downloads,
            # Existing cases exercise the explicitly supported legacy route.
            "payload": {**SCOPE, "source_policy": "smart_clip_first", "output_dir": str(tmp_path / "out")}, "root": tmp_path}


def test_new_materials_default_to_originals_even_when_finished_clips_are_plentiful(setup):
    setup["product"]["fragments"]["101"] = {"fragment_id": "101", "download_url": "https://v3.zjcdn.com/original101.mp4"}
    setup["product"]["fragments"]["102"] = {"fragment_id": "102", "download_url": "https://v3.zjcdn.com/original102.mp4"}
    payload = {k: v for k, v in setup["payload"].items() if k != "source_policy"}
    result = jobs.prepare_materials(payload)
    assert result["status"] == "ready"
    assert result["targets_and_policy"]["target_count"] == 1
    assert setup["downloads"] == ["https://v3.zjcdn.com/original101.mp4"]
    assert result["summary"]["original_video_count"] == 1
    assert result["summary"]["smart_clip_video_count"] == 0
    handoff = json.loads(Path(result["editor_handoff_path"]).read_text("utf-8"))
    assert handoff["source_policy"] == "original_only"
    assert handoff["assets"][0]["source"] == "original_fragment"
    assert handoff["assets"][0]["has_clip_record"] is True
    assert handoff["assets"][0]["caption_status"] == "unchecked"
    assert handoff["source_caption_review_required"] is True


def test_original_only_reports_shortage_without_downloading_or_generating_finished_clips(setup):
    setup["product"]["fragments"]["103"] = {"fragment_id": "103"}
    result = jobs.prepare_materials({**setup["payload"], "source_policy": "original_only", "target_count": 1})
    assert result["status"] == "source_exhausted"
    assert not setup["downloads"] and not result["clip_plans"]
    assert result["summary"]["shortage"]["video_count"] == 1
    assert not any("智能切片" in option for option in result["alternatives"])


def test_original_first_fills_from_original_before_explicitly_allowed_finished_clip(setup):
    setup["product"]["fragments"]["101"] = {"fragment_id": "101", "download_url": "https://v3.zjcdn.com/original101.mp4"}
    result = jobs.prepare_materials({**setup["payload"], "source_policy": "original_first", "target_count": 2})
    assert result["status"] == "ready"
    assert setup["downloads"] == ["https://v3.zjcdn.com/original101.mp4", "https://v3.zjcdn.com/1.mp4"]
    assert result["summary"]["conservative_duration_seconds"] == 80


def test_resume_tightening_to_original_only_excludes_already_verified_finished_clips(setup):
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    setup["product"]["fragments"]["101"] = {"fragment_id": "101", "download_url": "https://v3.zjcdn.com/original101.mp4"}
    second = jobs.resume_materials({**SCOPE, "job_id": first["job_id"], "source_policy": "original_only"})
    assert second["status"] == "ready"
    assert second["summary"]["original_video_count"] == 1 and second["summary"]["smart_clip_video_count"] == 0
    handoff = json.loads(Path(second["editor_handoff_path"]).read_text("utf-8"))
    assert [a["source"] for a in handoff["assets"]] == ["original_fragment"]
    assert len(second["assets"]) == 2  # Preserve the previous file, without counting it.


def test_saved_legacy_job_does_not_change_source_policy_on_read_or_resume(setup):
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    with jobs._db() as db:
        data = json.loads(db.execute("SELECT data_json FROM material_jobs WHERE job_id=?", (result["job_id"],)).fetchone()[0])
        data["options"].pop("source_policy")
        db.execute("UPDATE material_jobs SET data_json=? WHERE job_id=?", (json.dumps(data), result["job_id"]))
    loaded = jobs.material_status({**SCOPE, "job_id": result["job_id"]})
    assert loaded["targets_and_policy"]["source_policy"] == "smart_clip_first"
    resumed = jobs.resume_materials({**SCOPE, "job_id": result["job_id"]})
    assert resumed["status"] == "ready" and len(setup["downloads"]) == 1


@pytest.mark.parametrize("extra", [{"allow_intelligent_clipping": True}, {"allow_originals": False}])
def test_original_only_rejects_conflicting_policy_before_starting_work(setup, extra):
    with pytest.raises(ValueError):
        jobs.prepare_materials({**setup["payload"], "source_policy": "original_only", **extra})
    assert not setup["downloads"]


def test_prepare_completes_real_target_with_other_existing_clips_and_handoff(setup):
    result = jobs.prepare_materials({**setup["payload"], "target_count": 2, "target_seconds": 60})
    assert result["status"] == "ready" and result["summary"]["targets_met"]
    assert result["summary"]["verified_video_count"] == 2
    assert result["summary"]["conservative_duration_seconds"] == 68
    manifest = json.loads(Path(result["manifest_path"]).read_text("utf-8"))
    handoff = json.loads(Path(result["editor_handoff_path"]).read_text("utf-8"))
    assert manifest["schema_version"] == "baiying-materials/v1"
    assert all(Path(a["source_path"]).is_file() for a in handoff["assets"])
    assert len(handoff["assets"]) == 2 and handoff["editorial_review_required"]
    assert all(a["media"]["full_decode_passed"] for a in handoff["assets"])
    # Short clips must enter the same asynchronous import queue as long videos.
    assert all(a["suggested_import_tool"] == "media_register" for a in handoff["assets"])
    assert all(a["import_arguments"] == {"source_path": a["source_path"], "mode": "managed_copy"}
               for a in handoff["assets"])
    assert "13800000000" not in Path(result["manifest_path"]).read_text("utf-8")


def test_qualified_only_stops_with_choices_and_resume_uses_existing_files(setup):
    result = jobs.prepare_materials({**setup["payload"], "target_count": 2, "quality": "qualified_only", "allow_originals": False})
    assert result["status"] == "needs_input" and result["summary"]["shortage"]["video_count"] == 1
    resumed = jobs.resume_materials({**SCOPE, "job_id": result["job_id"], "quality": "prefer_qualified"})
    assert resumed["status"] == "ready"
    assert len(setup["downloads"]) == 2  # First valid file was not downloaded again.


def test_original_fallback_and_same_fragment_duration_are_not_double_counted(setup):
    setup["product"]["videos"].pop("2")
    setup["product"]["linked_fragments"] = {"101"}
    setup["product"]["fragments"]["101"] = {"fragment_id": "101", "download_url": "https://v3.zjcdn.com/original101.mp4"}
    result = jobs.prepare_materials({**setup["payload"], "target_count": 0, "target_seconds": 75})
    assert result["status"] == "ready"
    assert result["summary"]["total_duration_seconds"] == 114
    assert result["summary"]["conservative_duration_seconds"] == 80
    assert result["summary"]["known_source_count"] == 1


def test_exact_duplicates_cannot_make_target_look_satisfied(setup):
    setup["product"]["videos"]["2"]["download_url"] = setup["product"]["videos"]["1"]["download_url"]
    result = jobs.prepare_materials({**setup["payload"], "target_count": 2})
    assert result["status"] == "source_exhausted"
    assert result["summary"]["verified_video_count"] == 1 and result["summary"]["duplicate_count"] == 1
    assert len(json.loads(Path(result["editor_handoff_path"]).read_text("utf-8"))["assets"]) == 1


def test_visually_similar_candidates_are_retained_but_need_review_before_counting(setup, monkeypatch):
    monkeypatch.setattr(media, "similar_visuals", lambda *_: True)
    first = jobs.prepare_materials({**setup["payload"], "target_count": 2})
    assert first["status"] == "needs_input"
    assert first["summary"]["verified_video_count"] == 2
    assert first["summary"]["counted_video_count"] == 1
    assert all(Path(a["path"]).is_file() for a in first["assets"])
    reviewed = jobs.resume_materials({**SCOPE, "job_id": first["job_id"], "allow_similar_visuals": True})
    assert reviewed["status"] == "ready" and reviewed["summary"]["counted_video_count"] == 2


def test_independent_source_goal_and_download_budget_are_enforced(setup):
    setup["product"]["videos"]["2"]["fragment_id"] = "101"
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1, "min_source_count": 2, "max_downloads": 1})
    assert first["status"] == "partial"
    assert first["summary"]["shortage"]["source_count"] == 1
    assert len(setup["downloads"]) == 1
    second = jobs.resume_materials({**SCOPE, "job_id": first["job_id"], "max_downloads": 3})
    assert second["summary"]["known_source_count"] == 1  # Two cuts of one source are not two sources.
    assert not second["summary"]["targets_met"]


@pytest.mark.parametrize("incomplete,pending", [(True, False), (False, True), (False, False)])
def test_zero_videos_and_incomplete_or_unresolved_sources_are_distinguished(setup, incomplete, pending):
    setup["product"]["videos"].clear()
    setup["product"]["linked_fragments"] = {"101"} if pending else set()
    setup["scan"]["scan_complete"] = not incomplete
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    assert result["status"] == ("partial" if incomplete or pending else "source_exhausted")
    assert result["summary"]["shortage"]["video_count"] == 1
    assert Path(result["editor_handoff_path"]).is_file()


def test_failed_download_refreshes_url_and_never_counts_failure(setup, monkeypatch):
    calls = []

    def download(url, target, timeout, **kwargs):
        calls.append(url)
        if url.endswith("/1.mp4"):
            setup["product"]["videos"]["1"]["download_url"] = "https://v3.zjcdn.com/fresh1.mp4"
            raise requests.HTTPError("expired")
        target.write_bytes(b"valid")

    monkeypatch.setattr(replay, "_download", download)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    assert result["status"] == "ready" and len(calls) == 2
    assert calls[-1].endswith("fresh1.mp4")
    assert "error_detail" not in result["assets"][0] and "error" not in result["assets"][0]


def test_exhausted_download_retries_return_partial_and_can_be_retried(setup, monkeypatch):
    original = replay._download
    attempts = []

    def fail(url, *_args, **_kwargs):
        attempts.append(url)
        raise requests.Timeout()

    monkeypatch.setattr(replay, "_download", fail)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 2})
    assert result["status"] == "partial" and len(attempts) == 4
    assert result["summary"]["verified_video_count"] == 0
    assert all(a["error_detail"]["category"] == "timeout" for a in result["assets"])
    assert all(Path(a["error_detail"]["diagnostic_file"]).is_file() for a in result["assets"])
    monkeypatch.setattr(replay, "_download", original)
    result = jobs.resume_materials({**SCOPE, "job_id": result["job_id"], "retry_failed": True})
    assert result["status"] == "ready"
    assert all("error_detail" not in a for a in result["assets"])


def test_changed_existing_file_is_preserved_and_replaced_with_verified_copy(setup):
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    bad = Path(result["assets"][0]["path"])
    bad.write_bytes(b"broken")
    resumed = jobs.resume_materials({**SCOPE, "job_id": result["job_id"]})
    assert resumed["status"] == "ready"
    assert bad.read_bytes() == b"broken"
    assert resumed["assets"][0]["path"] != str(bad)


def test_scope_ownership_and_request_idempotency(setup):
    payload = {**setup["payload"], "target_count": 1, "request_id": "test001"}
    first = jobs.prepare_materials(payload)
    assert jobs.prepare_materials(payload)["job_id"] == first["job_id"]
    assert len(setup["downloads"]) == 1
    with pytest.raises(ValueError, match="request_id"):
        jobs.prepare_materials({**payload, "target_count": 2})
    with pytest.raises(ValueError, match="不属于"):
        jobs.material_status({"mobile": "13900000000", "job_id": first["job_id"]})
    assert jobs.list_material_jobs(SCOPE)["jobs"][0]["job_id"] == first["job_id"]


def test_running_job_is_not_started_twice_and_can_cancel(setup, monkeypatch):
    launched = []
    monkeypatch.setattr(jobs, "_launch", lambda *args: launched.append(args))
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    jobs.resume_materials({**SCOPE, "job_id": first["job_id"]})
    assert len(launched) == 1
    jobs.cancel_materials({**SCOPE, "job_id": first["job_id"]})
    jobs.run_worker(*launched[0])
    result = jobs.material_status({**SCOPE, "job_id": first["job_id"]})
    assert result["status"] == "cancelled" and not setup["downloads"]


def test_cancel_during_stream_stops_without_counting_unverified_file(setup, monkeypatch):
    def download(url, target, timeout, **kwargs):
        with jobs._db() as connection:
            connection.execute("UPDATE material_jobs SET cancel_requested=1")
        kwargs["checkpoint"](1024, 4096)
        pytest.fail("download should have been cancelled")

    monkeypatch.setattr(replay, "_download", download)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    assert result["status"] == "cancelled" and result["summary"]["verified_video_count"] == 0


def test_dead_worker_is_reported_interrupted_then_resumes(setup, monkeypatch):
    monkeypatch.setattr(jobs, "_launch", lambda *_: None)
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    with jobs._db() as connection:
        connection.execute("UPDATE material_jobs SET started_at=0 WHERE job_id=?", (first["job_id"],))
    assert jobs.material_status({**SCOPE, "job_id": first["job_id"]})["status"] == "interrupted"
    monkeypatch.setattr(jobs, "_launch", jobs.run_worker)
    assert jobs.resume_materials({**SCOPE, "job_id": first["job_id"]})["status"] == "ready"


def test_smart_clipping_generates_downloads_and_hands_off_with_one_submission(setup, monkeypatch):
    setup["product"]["videos"].clear()
    setup["product"]["linked_fragments"].clear()
    setup["product"]["fragments"]["103"] = {"fragment_id": "103"}
    submissions = []

    def submit(payload):
        submissions.append(payload)
        setup["product"]["videos"]["3"] = clip("3", "103")
        setup["product"]["linked_fragments"].add("103")
        return {"results": [{"accepted": True, "response": {"code": 0}}]}

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1, "allow_intelligent_clipping": True})
    assert result["status"] == "ready" and len(submissions) == 1
    assert result["clip_plans"][0]["state"] == "videos_available"
    assert jobs.resume_materials({**SCOPE, "job_id": result["job_id"]})["status"] == "ready"
    assert len(submissions) == 1


@pytest.mark.parametrize("failure", ["unknown", "rejected"])
def test_generation_failure_is_bounded_and_unknown_never_resubmits(setup, monkeypatch, failure):
    setup["product"]["videos"].clear()
    setup["product"]["linked_fragments"].clear()
    setup["product"]["fragments"]["103"] = {"fragment_id": "103"}
    count = []
    clock = [1000]
    monkeypatch.setattr(jobs.time, "time", lambda: clock[0])
    monkeypatch.setattr(jobs.time, "sleep", lambda _: clock.__setitem__(0, clock[0] + 30))

    def submit(payload):
        count.append(payload)
        if failure == "unknown":
            raise requests.Timeout()
        return {"results": [{"accepted": False, "response": {"code": 1001}}]}

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1, "allow_intelligent_clipping": True, "clip_wait_seconds": 30})
    assert result["status"] == "partial" and len(count) == 1
    jobs.resume_materials({**SCOPE, "job_id": result["job_id"]})
    assert len(count) == 1


def test_recovery_after_lost_worker_uses_saved_submission_plan(setup, monkeypatch):
    setup["product"]["videos"].clear()
    setup["product"]["linked_fragments"].clear()
    setup["product"]["fragments"]["103"] = {"fragment_id": "103"}
    sent = []

    def submit(payload):
        sent.append(payload)
        setup["product"]["videos"]["3"] = clip("3", "103")
        setup["product"]["linked_fragments"].add("103")
        return {"results": [{"accepted": True, "response": {"code": 0}}]}

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    original_start = replay.start_clips

    def lost_after_submit(payload):
        original_start(payload)
        raise OSError("worker stopped after POST")

    monkeypatch.setattr(replay, "start_clips", lost_after_submit)
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1, "allow_intelligent_clipping": True})
    assert first["status"] == "failed" and len(sent) == 1
    monkeypatch.setattr(replay, "start_clips", original_start)
    second = jobs.resume_materials({**SCOPE, "job_id": first["job_id"]})
    assert second["status"] == "ready" and len(sent) == 1


def test_expired_never_submitted_plan_can_resume_safely(setup, monkeypatch):
    setup["product"]["videos"].clear()
    setup["product"]["linked_fragments"].clear()
    setup["product"]["fragments"]["103"] = {"fragment_id": "103"}
    original_start = replay.start_clips
    monkeypatch.setattr(replay, "start_clips", lambda _: (_ for _ in ()).throw(OSError("stopped before POST")))
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1, "allow_intelligent_clipping": True})
    with replay._state() as connection:
        connection.execute("UPDATE clip_plans SET created_at=0")

    def submit(payload):
        setup["product"]["videos"]["3"] = clip("3", "103")
        setup["product"]["linked_fragments"].add("103")
        return {"results": [{"accepted": True, "response": {"code": 0}}]}

    monkeypatch.setattr(client, "trigger_auto_clip", submit)
    monkeypatch.setattr(replay, "start_clips", original_start)
    second = jobs.resume_materials({**SCOPE, "job_id": first["job_id"]})
    assert second["status"] == "ready"
    assert second["clip_plans"][0]["replaced_unsubmitted_plan_id"] == first["clip_plans"][0]["plan_id"]


def test_tightened_policy_does_not_count_old_ineligible_downloads(setup):
    first = jobs.prepare_materials({**setup["payload"], "target_count": 2})
    result = jobs.resume_materials({**SCOPE, "job_id": first["job_id"], "quality": "qualified_only"})
    assert result["summary"]["verified_video_count"] == 1
    assert result["status"] == "needs_input"


def test_ready_local_materials_can_resume_without_remote_login_or_network(setup, monkeypatch):
    first = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    monkeypatch.setattr(replay, "_product", lambda _: pytest.fail("No remote calls needed for verified ready files"))
    result = jobs.resume_materials({**SCOPE, "job_id": first["job_id"]})
    assert result["status"] == "ready" and len(setup["downloads"]) == 1


def test_provider_failure_is_reported_as_failed_not_no_materials(setup, monkeypatch):
    def fail(_):
        raise client.ReplayServiceError(500, "下游服务内部异常")
    monkeypatch.setattr(replay, "_product", fail)
    result = jobs.prepare_materials({**setup["payload"], "target_count": 1})
    assert result["status"] == "failed"
    detail = result["last_error"]
    assert detail["stage"] == "scanning" and detail["type"] == "ReplayServiceError"
    assert detail["provider_code"] == 500 and detail["category"] == "provider_unavailable"
    assert detail["transient"] and not detail["retryable"]
    assert Path(detail["diagnostic_file"]).is_file()
    assert "已保存进度" in result["reason"]


def test_full_media_decode_and_metadata_with_real_ffmpeg(tmp_path):
    try:
        binary = media.ffmpeg_binary()
    except ValueError:
        pytest.skip("FFmpeg not installed")
    path = tmp_path / "sample.mp4"
    subprocess.run([binary, "-nostdin", "-v", "error", "-f", "lavfi", "-i", "testsrc2=size=96x160:rate=15",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2.4", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-movflags", "+faststart", str(path)], check=True, capture_output=True,
                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    result = media.validate_video(path)
    assert result["full_decode_passed"] and result["has_audio"]
    assert result["width"] == 96 and result["height"] == 160
    assert 2.3 < result["duration_seconds"] < 2.6 and len(result["visual_signatures"]) >= 8
    corrupt = tmp_path / "corrupt.mp4"
    contents = path.read_bytes()
    corrupt.write_bytes(contents[:len(contents) // 2])
    with pytest.raises(ValueError):
        media.validate_video(corrupt)
