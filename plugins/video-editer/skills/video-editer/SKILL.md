---
name: video-editer
description: Transcribe, inspect and edit videos using local word-timed ASR, source-bound sentence candidates, explicit timelines and a GPU-first single-heavy-task queue. Pure commerce edits require transcription before selection; optional generated hooks use an external provider only on request. Tools do not infer editorial intent or certify listening quality.
---

# video_editer

Use this plugin when an agent needs to execute a concrete video-editing plan.

For source-only commerce edits, first use the staged workflow in
[Source-only stages](../baiying-dialogue-video/references/source-only-stages.md).
Start/resume with commerce_workflow_start / commerce_workflow_status and follow
its next_tool and next_arguments. Use 0.7.0+ and read
[Efficient execution and review receipts](../baiying-dialogue-video/references/efficient-execution.md).
Prefer commerce_materials_prepare, commerce_candidates_submit and media_jobs_wait
for batches on the existing single-heavy-task queue. First complete local ASR with
speech_transcribe_submit, speech_candidates_get and speech_range_validate; read
[Speech evidence](../baiying-dialogue-video/references/speech-evidence.md).
Use commerce_speech_candidate_submit with consecutive sentence IDs for voice/body,
not guessed seconds. Review candidate display, independent source voice and body units
before commerce_timeline_apply; use commerce_typography_add for transparent
lettering and commerce_presentation_record for the real event IDs. Render via
commerce_render_submit. Queue delivery_inspection_submit for the actual output,
fill delivery_review_form only after reviewing its evidence, then record and
check commerce_delivery_get. Missing or uncertain facts do not pass.
Before designing commerce lettering, open all eight original PNGs listed in
[User typography images](../baiying-dialogue-video/references/typography-examples.md)
with an image-viewing tool. Filenames, Markdown source and OCR do not substitute
for viewing the pixels. Use their hierarchy and styling with the current video's
copy, palette and safe areas; do not paste the reference screenshots into output.
ASR uses a local recognition model; it never generates speech or independently
certifies visual meaning, sentence meaning or emotional delivery.
Touching zero-duration tokens are preserved in indivisible estimated groups,
flagged for listening; isolated/invalid timings still fail. Decimal numbers must
remain whole, including in older transcripts. Read candidate context before
selection. Commerce lettering rejects unreadably small main/support text and
supports two explicit lines; shorten copy or review another safe box instead of
shrinking into a label. Known source-caption/voice conflicts cannot be deferred.
In 0.5.1+, missing visual/listening capability may be recorded honestly using
commerce_candidate_defer (unknown=null). Explicit preview_only timeline assembly
permits an unapproved review copy at target quality (preview=false); rejected/silent candidates and final delivery
remain blocked. Never change unknown into a passing observation to obtain output.

Render target quality once, inspect that exact file and deliver it only when
commerce_delivery_get passes; a reviewed target-quality file needs no second
render. Actual tool/user review receipts bind media hashes, modalities and time
coverage; method labels alone leave candidates/checks pending. Do not fabricate
receipts from ASR, signal measurements or frame availability. Missing custom
commerce_text/layout is explicitly unchecked. Review internal source-cut flags.
After an audio-only repair, a new render reuses picture stages automatically;
check reused_stages rather than assuming reuse or inheriting old approval.
Use media_convert_submit for necessary transcode/cadence repair; interpolation
is opt-in. It and failed retries share the existing queue.

For commerce edits, read [examples and repair](../baiying-dialogue-video/references/quality-and-repair.md)
and [demonstration editing](../baiying-dialogue-video/references/demonstration-editing.md)
before choosing footage. Use actual PTS from queued `media_frames_at`, not labelled
first-frame contact sheets. Record editorial evidence through `delivery_review_record`
and recheck `delivery_review_get`. Missing/listening-pending checks cannot become
delivery_ready. Failed editorial choices need a changed timeline and a new render;
`render_retry` executes the old saved edit and cannot improve its content.
It is standalone: do not start an editing platform or load another project's
environment. Tool outputs refer to its own data directory. ASR is a bundled
local recognition model; there is no LLM planner or speech synthesis. The caller
chooses source-bound sentence IDs, display ranges, text and effects.

For live-commerce product cuts, use the bundled
[editorial defaults](../baiying-dialogue-video/references/editorial.md) unless the
user gives a newer direction: about 45 seconds (over 30, under 60), original
speed, a 2–4 second hook when needed, real feature/detail cuts and role-based
typography. The current default is full-body natural display plus a separately
selected complete emotional source voiceover. Reject visibly sustained talking
in the display; muting that footage does not make it a pure display.
Offscreen question/presenter reply and emphatic presenter openings are optional
only when selected by the user; read their separate
[generated/dialogue route](../baiying-dialogue-video/references/generated-route.md).
For this product workflow, require at least one visible decorative transition,
default to one and allow at most two, at a major topic boundary between complete
explanatory units. Do not silently deliver zero unless the user explicitly
changes the requirement. Hard cuts, typography entrances and audio-only cues
do not count. Verify the actual rendered count and timestamps before delivery.
The reference and
[planning template](../baiying-dialogue-video/assets/workflow-plan.json) define
spacing, duration, placement and review. These are caller planning defaults,
not runtime limits or rules for unrelated video genres. For this workflow,
plan typography and short sound cues together for both real-source and generated
openings; read [Typography and sound](../baiying-dialogue-video/references/typography-sound.md).
For this product workflow, read
[Typography layout](../baiying-dialogue-video/references/typography-layout.md):
use transparent lettering without filled panels, bubbles or pills, and one
whole-video palette of at most three theme colors with a consistent neutral
outline. Design each video's theme for its garment, setting and content; do not
turn a case palette into a global default or a closed preset menu. Within that
theme, vary keyword scale, supporting type, arrangement and brief motion by role.
Consistency does not mean identical single-line lettering everywhere.
Keep effect text outside platform UI exclusions and burned-in source captions;
choose available top, middle-left or lower-left space only after checking the
face and product action. Inspect the full animated extent, including overshoot
and rotation. A preset's canvas bounds do not establish safe placement.
Prioritize complete meaning and actions over cut density. After the hook,
establish one complete body unit before changing topics; inspect existing
composites for internal cuts as well. Read
[Body entry continuity](../baiying-dialogue-video/references/editorial.md#钩子后的正文承接)
for adjustable timing guidance and continuous review of the first 8–10 body seconds.
For this product workflow, apply the
[Source audio gate](../baiying-dialogue-video/references/source-audio.md)
to every selected range before use: silent body material is ineligible. Only
a full-body hook display with verified independent sound may use silent picture
material. Stream presence and a successful render are not evidence of audible
source content or continuous final sound. These are caller eligibility rules,
not new automatic restrictions in the general-purpose editor.
For this product workflow, mix typography by role. Prefer centered lower
subtitle-style effects in the opening hook. For body selling points, prefer
independent non-subtitle typography in reviewed top, middle-left or lower-left
space, interspersed with quiet subtitles for ordinary speech. Share the video's
theme, but do not make the entire video subtitle-style or force a fixed alternation
quota. Effect text hides/replaces our editable ordinary subtitle while active;
one information group may contain multiple coordinated graphics layers.
Only ordinary subtitles and subtitle-style effects must center each line on the
full canvas width (x=0.5), in the lower allowed region. Narrow/wrap these lines
to avoid right-side UI instead of shifting left. Independent body effects follow
available safe space and are not forced to the canvas center or bottom.
Distinguish editable captions from burned-in source text, and review entry/exit
frames for duplication and residual flashes. Prioritize speech and avoid
duplicating sounds already present in the source. Read the
[Baiying workflow](../baiying-dialogue-video/SKILL.md) only when collecting or
orchestrating that source; existing local footage does not require collection.

Only when the user requests a generated opening hook, read [Generated hooks](generated-hooks.md).
Pure source hooks use commerce_timeline_apply, never hook_attach.
Use the selected account's actual footage AND identity assets, preserve the
accepted body edit, and attach only a reviewed output. External generation is
asynchronous and requires the user's existing authorization; the local editor
records it but never makes hidden paid calls.
Plan and verify sound and whole-video duration separately from the hook;
silent reference preparation must not silently mute requested generated speech.
For dialogue hooks, separate offscreen and presenter voices and speaking turns.
Keep hook and body at 1.0x by default. Only retime when explicitly requested.
For lip-synced dialogue, retime video and explicit audio together and preserve
pitch. For a silent full-body display with an independent voiceover, an explicit
audio-only speed request applies only to that voiceover: keep the picture and
accepted body at their requested speeds, record both rates, and shift body
events by the actual hook length. Do not add an unsolicited decorative AI-opening label.

1. For a new edit or authorized handoff test, call `project_create` and import
   authorized local videos with background `media_register`, including short
   videos; use `media_import` for images/audio. Save source hashes and project/job
   IDs, poll `media_job_status`, and use only `succeeded.result`. Retry failed,
   cancelled or interrupted imports with `media_job_retry` and track its new ID.
   Reuse successful imports of the same source hash within the selected project.
   For a handoff-only test, stop after import verification. For an existing
   project, reuse its ID. Inspect using
   `media_inspect` and `timeline_get` before changing its timeline.
   `media_inspect` now reads stored metadata without waiting for the heavy slot;
   it does not verify current file contents. Request `refresh=true` only for an
   explicit guarded probe, or queue verification/preparation when appropriate.
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
For designed effects, use `audio_track_update` to retain IDs/bindings while
adjusting cues, then queue `audio_review_submit` against the successful render's
saved revision. Check every planned cue and its equal-level comparison samples;
do not equate a successful review job or signal presence with audible quality.


For long-source exploration, index/coverage, or candidate workflows, read
[Long media evidence](long-media.md). Those tools preserve source
identity and host-authored observations separately from the timeline. They do
not make a whole video understood simply by generating thumbnails or proxies.

- `media_inspect`, `timeline_get`, `timeline_validate`, `transition_list`,
  `callout_list_templates`, and `output_inspect` are read-only.
- Importing media, editing a timeline, and final rendering mutate project
  state or artifacts. Preserve each returned revision and undo snapshot.
- When a requested style permits filled point cards, use `callout_points_add`: 1-4 entries with
  explicit `text`, `start`, `end` (max 7 characters; duration >=0.12s). No beat is
  derived. Use `callout_update(points=...)` to revise them; card appearance is a
  fixed preset. Legacy `callout_add(template="point_list")` keeps its two-point
  `|` format and +0.72s delay only for compatibility.
  The current product workflow disallows filled backgrounds: inspect the actual
  preset and use editable transparent overlays when its panel or fixed placement
  conflicts. Do not invent a background-removal parameter for a fixed template.
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

## 原片时间线辅助选材

商品纯剪辑默认取原始回放，具体按同包 baiying-dialogue-video/references/original-source-selection.md。转写成功后用 `speech_timeline_get` 查看实际覆盖、完整句和 ASR 未覆盖空档；它不重新推理，也不代表画面/声音合格。配音和正文仍用句子 ID 及时间戳校验，展示仍需连续画面复查，保留原片停顿。
