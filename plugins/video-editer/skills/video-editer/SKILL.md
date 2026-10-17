---
name: video-editer
description: Inspect and edit videos using explicit timelines, local audiovisual evidence and a GPU-first single-heavy-task queue. Also orchestrate account-specific generated opening hooks using real reference slices, an external generation provider, durable task records and reviewed insertion. The execution tools do not call a model or infer editorial intent.
---

# video_editer

Use this plugin when an agent needs to execute a concrete video-editing plan.
It is standalone: do not start an editing platform or load another project's
environment. Tool outputs refer to its own data directory. No model is called
inside the plugin; the caller supplies clip boundaries, text and effects.

For live-commerce product cuts, use the bundled
[editorial defaults](../baiying-dialogue-video/references/editorial.md) unless the
user gives a newer direction: about 45 seconds (over 30, under 60), original
speed, a 2–4 second hook when needed, real feature/detail cuts and role-based
typography. The default offscreen question/presenter reply also has an optional
[emphatic presenter opening](../baiying-dialogue-video/references/hook-options.md)
with one naturally lengthened emphasis and optional two-note sound cue.
Decorative transitions
default to one, at most two, only at major topic boundaries; none is fine when
there is no useful boundary. The reference and
[planning template](../baiying-dialogue-video/assets/workflow-plan.json) define
spacing, duration, placement and review. These are caller planning defaults,
not runtime limits or rules for unrelated video genres. Read the
[Baiying workflow](../baiying-dialogue-video/SKILL.md) only when collecting or
orchestrating that source; existing local footage does not require collection.

When adding or generating an opening hook, read [Generated hooks](generated-hooks.md).
Use the selected account's actual footage AND identity assets, preserve the
accepted body edit, and attach only a reviewed output. External generation is
asynchronous and requires the user's existing authorization; the local editor
records it but never makes hidden paid calls.
Plan and verify sound and whole-video duration separately from the hook;
silent reference preparation must not silently mute requested generated speech.
For dialogue hooks, separate offscreen and presenter voices and speaking turns.
Keep hook and body at 1.0x by default. Only retime when explicitly requested;
retime video and explicit audio together, preserve pitch, and shift body events
by the actual hook length. Do not add an unsolicited decorative AI-opening label.

1. For a new edit or authorized handoff test, call `project_create` and import
   authorized local videos with background `media_register`, including short
   videos; use `media_import` for images/audio. Save source hashes and project/job
   IDs, poll `media_job_status`, and use only `succeeded.result`. Retry failed,
   cancelled or interrupted imports with `media_job_retry` and track its new ID.
   Reuse successful imports of the same source hash within the selected project.
   For a handoff-only test, stop after import verification. For an existing
   project, reuse its ID. Inspect using
   `media_inspect` and `timeline_get` before changing its timeline.
   Use `canvas_configure` for a nondefault aspect ratio or explicit cover crop;
   it never chooses a subject-aware crop. Defaults are vertical and contained.
2. Make one explicit change per `clip_*`, `transition_apply`, `callout_*`,
   `overlay_*`, `subtitle_*`, or `audio_*` call.
3. Call `timeline_validate` before rendering.
4. Submit `render_submit(preview=true)` for review, then submit the final render.
   Poll `render_job_status`; only a succeeded job's `result` is a usable output.
5. Use returned `undo_snapshot` values to recover from an unwanted mutation.

The plugin is an execution layer, not a director. It must not choose clips,
write sales copy, select transitions, or create media without an explicit
instruction from the calling agent or user.

## Safe tool use

For audio levels, fades, frame coverage, cadence, or soundtrack-only repair, read
[Audio and delivery](audio-and-delivery.md). Retain the explicit gain/fade and
frame-alignment controls; audio checks measure LUFS/true peak and can be expensive.


For long-source exploration, index/coverage, or candidate workflows, read
[Long media evidence](long-media.md). Those tools preserve source
identity and host-authored observations separately from the timeline. They do
not make a whole video understood simply by generating thumbnails or proxies.

- `media_inspect`, `timeline_get`, `timeline_validate`, `transition_list`,
  `callout_list_templates`, and `output_inspect` are read-only.
- Importing media, editing a timeline, and final rendering mutate project
  state or artifacts. Preserve each returned revision and undo snapshot.
- For speech-driven point cards prefer `callout_points_add`: 1-4 entries with
  explicit `text`, `start`, `end` (max 7 characters; duration >=0.12s). No beat is
  derived. Use `callout_update(points=...)` to revise them; card appearance is a
  fixed preset. Legacy `callout_add(template="point_list")` keeps its two-point
  `|` format and +0.72s delay only for compatibility.
- Callouts only use `product_left` or `product_right`; do not obscure the
  product centre.
- Use overlay/caption/audio tracks rather than adding decorative content to the
  main video track. Position keyframes must be explicitly supplied by the
  calling agent.
- Main clips can also be images. For an image, `clip_add` start/end are virtual
  still seconds (0..3600); duration is (end-start)/speed. `clip_camera_set` can
  zoom/pan images or videos: start/end zoom 1..4, start/end x/y 0..1 within crop
  room, and easing. At zoom=1 there is no pan room. Detach camera motion before
  splitting and explicitly set the resulting moves.
- `overlay_transform_set` replaces a key list: `at` plus optional x/y, scale,
  clockwise rotation, opacity and easing. Missing fields inherit the previous
  state; destination easing controls the preceding interval. x/y refer to the
  original rectangle; scale/rotation act around its centre. Bounds are checked
  on every rendered frame. Masks are fixed none/circle/rounded_rect. Use extra
  keys for overshoot; there is no automatic bounce or person tracking.
- Transition IDs refer to outgoing edges; overlap shortens assembled duration.
  Choose another effect or explicitly adjust clips if an overlap is rejected;
  the plugin will not silently choose for you.
- `transition_list.procedural` contains six distinct evolving-mask reveals,
  separate from legacy xfade mappings. Read intent/season/avoid/coverage before
  choosing. Winter frost/snow and summer liquid/bubbles use different geometry.
  Full coverage can obscure a simultaneous claim; inspect the overlap window.
- `callout_list_templates.procedural` describes four independently drawn styles
  with measured editable text. Supply the copy yourself; no model runs inside
  the plugin. Capacity is 10 characters; positions stay at the side. New raster
  typography uses an installed CJK font or `VIDEO_EDITER_FONT_FILE`; legacy ASS
  uses `VIDEO_EDITER_FONT`. Do not claim pixel-identical reference reproduction.
- Use `timeline_time_map` and `event_bind` when an event should follow a clip's
  source range through speed, trim and reorder changes. Unbound events keep
  absolute time. Existing point/key times must map into that range on binding.
  Detach with `event_unbind` before editing their timestamps. Removing a bound
  range is rejected, not silently clipped. A split rebinds right-side events;
  spanning events require an explicit detach or split decision.
- Read the time map's actual overlap and `tail_padding`: the executor uses a
  shared 25fps clock, preserving selected source ranges with less than one frame
  of end padding. Do not sum raw source durations to place later events.
- `timeline_validate` reports fatal errors and conservative overlap/headroom
  warnings with event IDs. Resolve errors before rendering; inspect warnings
  in preview rather than assuming every geometric overlap is wrong. No local
  model locates the product or selects a fix. Several template names share art.
- Use the returned render path, revision and timeline. There is no web output
  endpoint, automatic 30-second cut or semantic quality approval.
- On failure, use `render_job_status` and `render_retry` with the reported job ID.
  Retry uses the failed job's SAVED revision in a new attempt, reusing verified
  completed stages. If you changed the edit, start a new render instead.
  Changed source files reject retry. Busy writer errors are retryable after the
  other write finishes.
- Use `render_submit` for rendering; it saves an immutable job/revision and
  returns before input hashing and encoding. Later edits do not change queued
  snapshots. Media work and renders share one FIFO per data directory and one
  heavy-operation slot across data directories using the same resource directory.
  Legacy synchronous render/import helpers also acquire that resource slot.
  `task_queue_list` reports combined job order and resource-wait reasons.
  Read [Resource controls](resources.md) for GPU selection, limits and waiting.
- Use `render_cancel` only when the task calls for stopping that exact job.
  Confirm terminal cancellation with status; completed outputs are not undone.
  Artifacts and checkpoints are retained. MCP startup/reconnection resumes queued
  work automatically; `render_queue_start` also drains it. Neither retries terminal/interrupted
  jobs. `render_retry` queues a new attempt using the saved revision, not new edits;
  poll its new job ID. Never treat submission as a completed render.
