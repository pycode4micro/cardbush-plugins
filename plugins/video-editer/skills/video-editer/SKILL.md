---
name: video-editer
description: Use video_editer MCP tools to inspect long videos through local audiovisual evidence, store agent-authored indexes and clip candidates, and execute explicit video timelines. The tools do not call a model or infer editorial intent.
---

# video_editer

Use this plugin when an agent needs to execute a concrete video-editing plan.
It is standalone: do not start an editing platform or load another project's
environment. Tool outputs refer to its own data directory. No model is called
inside the plugin; the caller supplies clip boundaries, text and effects.

1. For a new edit, call `project_create` and import authorized local materials
   with `media_import` for short videos/images/audio, or background `media_register`
   for long videos. For an existing project, reuse its ID. Inspect using
   `media_inspect` and `timeline_get` before changing its timeline.
   Use `canvas_configure` for a nondefault aspect ratio or explicit cover crop;
   it never chooses a subject-aware crop. Defaults are vertical and contained.
2. Make one explicit change per `clip_*`, `transition_apply`, `callout_*`,
   `overlay_*`, `subtitle_*`, or `audio_*` call.
3. Call `timeline_validate` before rendering.
4. Use `render_preview` before `render_final` whenever output quality needs review.
5. Use returned `undo_snapshot` values to recover from an unwanted mutation.

The plugin is an execution layer, not a director. It must not choose clips,
write sales copy, select transitions, or create media without an explicit
instruction from the calling agent or user.

## Safe tool use

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
- For long renders use `render_submit`; it returns an immutable job/revision
  while a detached worker runs. `render_queue_list`/`render_job_status` are
  read-only. Later timeline edits do not change queued snapshots. FIFO has one
  background worker per data directory; synchronous render tools bypass it.
- Use `render_cancel` only when the task calls for stopping that exact job.
  Confirm terminal cancellation with status; completed outputs are not undone.
  Artifacts and checkpoints are retained. After a host restart,
  `render_queue_start` drains queued work but does not retry terminal/interrupted
  jobs. `render_retry` is synchronous and uses the saved revision, not new edits.
