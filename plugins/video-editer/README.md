# video_editer

A standalone MCP plugin with local ASR, its own Python/FFmpeg execution core
and bundled graphics, for Codex and other MCP-compatible agents. The caller
decides what to edit; the plugin exposes deterministic atomic execution tools.

## 0.6.0: mandatory ASR before commerce source selection

The six new tools are `speech_runtime_status`, `speech_model_prepare`,
`speech_transcribe_submit`, `speech_candidates_get`, `speech_range_validate`, and
`commerce_speech_candidate_submit` (110 tools total). Local faster-whisper is now
a declared dependency. Model preparation, recognition and retries use the same
single-heavy-task queue as media/render work. CUDA is preferred; bounded CPU int8
fallback follows the resource policy and records its reason. QSV video encoding
does not imply CUDA ASR support. Recognition does not upload footage or synthesize speech.

The staged workflow returns transcript progress and next-call arguments. Voice/body
selection requires actual source-bound transcript job and consecutive sentence IDs.
Word-boundary checks reject truncated, disjoint, changed or cross-source evidence;
timeline and render gates revalidate it. Display-only silent sources get an explicit
no-audio/no-speech record. ASR punctuation, confidence and context aid full-sentence
selection but never certify meaning, emotional delivery or listening quality.

Install this plugin's updated requirements in the MCP interpreter and reconnect.
Call `speech_runtime_status`, then queue `speech_model_prepare` if needed. It downloads
the pinned small model on first use; `source_model_dir` supports offline transfer.
`VIDEO_EDITER_ASR_MODEL_DIR` selects another local faster-whisper model directory.
No caller-written ASR scripts are required. Existing source-only projects retain
artifacts but need this evidence before new candidate use or renders. Read
[the exact sequence and limitations](skills/baiying-dialogue-video/references/speech-evidence.md).

## 0.5.1: explicit pending review copies

When the host cannot actually watch/listen, commerce_candidate_defer saves observed facts as true and unknown facts as null. Explicit preview_only assembly can render an unapproved review copy. Known defects, silent body/voice and rejected candidates stay blocked; pending facts cannot be waived or reach final delivery. Normal production still requires actual review.

Audio/image files use media_import; the video-only asynchronous entrypoint now rejects those formats immediately with the correct next tool. Both entrypoints retain the shared heavy resource guard. Typography fits main words, underline and support as a compact glyph block instead of anchoring support to the bottom of an oversized safe box; returned layout measurements aid review.

## 0.5.0: staged source-only commerce editing

- The main skill now routes source-only editing separately from generated/dialogue hooks. A muted talking shot is explicitly ineligible as a pure display.
- Persistent commerce workflow tools expose the next stage/action after interruption: inspect and review picture/voice/body candidates, atomically assemble selected units, add transparent typography, bind presentation events, queue preview/final, and check delivery.
- Candidate inspection, output inspection and retries share the existing single-heavy FIFO. The editor still does not call an LLM or certify visual meaning.
- delivery_inspection_submit creates output-bound PTS frames, listening material, selected-source audio checks and per-final-segment sound checks. It never increments the edit revision. delivery_review_form starts every judgement unchecked.
- Mandatory checks cannot use not_applicable; unrelated evidence cannot certify a check; missing inspection or unmeasured planned sound cannot be waived. Repair requires changed output and relevant content, not an unrelated edit/revision bump.
- commerce_typography_add provides transparent outlined lettering, per-video custom palette, main/support hierarchy and contained animation. It checks supplied exclusions and removes conflicting editable subtitle intervals; it does not detect faces or burned captions.
- A pre-existing accepted 3-second display/voice hook and a rejected framing example are bundled with hashes and explicit scope. Old typography in a sample does not supersede current layout requirements.

Start with the [source-only stages](skills/baiying-dialogue-video/references/source-only-stages.md). Existing projects are not enrolled automatically. Existing generic editing/generation tools remain available for their explicit workflows; enrolled source-only projects cannot prepare or attach generated hooks. Existing review-v1 records remain readable but require bound inspection for v2 delivery approval.

## 0.4.0: artifact-bound review and repair

- `delivery_review_record` records fourteen commerce review checks with local
  evidence hashes against an immutable succeeded render. Missing checks stay
  pending; failures need a repair action and a newly rendered timeline revision.
  Saved failures cannot be erased by submitting an empty or all-pass form.
- `delivery_review_get` rechecks the output, evidence and current revision.
  Rendering success is separate from `delivery_ready`; preview approval is not
  final delivery approval. The tools permit clearly labelled review copies.
- Cue signal reports must match the exact render, input and output hashes.
  Warnings remain visible until individually resolved with evidence. Auditory
  checks require an explicit listening/user-feedback observation; no tool
  infers subjective quality from RMS, ASR or a successful job.
- Queued frame extraction verifies requested versus decoded source PTS and
  rejects mislabeled first/key frames. The skills include accepted typography
  crops, a rejected panel example, and a concrete content-repair workflow.

Read [examples and repair](skills/baiying-dialogue-video/references/quality-and-repair.md).
This adds no model/provider dependency and makes no automatic editorial choices.
GPU-first selection, the shared single-heavy-task lock and queued retries remain.

## 0.3.9: cue evidence and reviewed commerce defaults

- `audio_review_submit` queues per-cue with/without/contribution audio samples
  at the same master gain, warns about low or masked effects and optionally
  compares the delivered sound from a succeeded render's saved revision.
  Signal presence is separate from listening approval. Query/cancel/retry using
  `media_job_*`; expensive work retains the single shared heavy slot.
- `audio_track_update` changes levels/fades/labels in place, preserving IDs,
  source bindings and undo. Explicit `sfx`/`voiceover`/`music` metadata supports
  selection; it never guesses an effect from a filename or adjusts gain itself.
- `media_inspect` defaults to an immediate stored-metadata read, even while
  heavy work waits for resources. `refresh=true` explicitly uses the guarded
  probe. Neither mode certifies current media bytes.

The bundled skills now share the accepted approximately 45-second workflow:
a roughly three-second full-body hook with independent source voice, complete
body units, varied transparent typography within one theme, subtitle/effect
mutual exclusion and reviewed safe placement. This workflow requires at least
one visible decorative transition, defaults to one and permits at most two.
Source-speech eligibility, final sound coverage, per-cue signal and listening
are separate checks. Existing user preferences override these scoped defaults.
Generated hooks remain optional and default to Seedance 2.0 Fast. No dependency,
model call, universal template style or automatic editorial engine was added.
See [audio review semantics](skills/video-editer/audio-and-delivery.md) and
[validation scope](VALIDATION.md).

## 0.3.8: Seedance 2.0 Fast hook default

The bundled commerce workflow now explicitly selects
`doubao-seedance-2-0-fast-260128` with native 720p, portrait ratio and audio.
It plans four generated seconds for the existing 2–4 second retained hook,
checks Fast-specific reference limits, and omits 2.5-only request fields.
No automatic model fallback is allowed; later explicit user choices override
the default. Native generation and the 1080x1920 edit canvas are tracked
separately. Historical 2.5 receipts stay accurate. This changes the skills and
planning template, not the model-free editing engine.

## 0.3.7: specific hooks with a source payoff

Commerce hooks now start from a concrete wearing concern, visible difference
or detail worth revealing, with a recorded source interval that answers it.
The first body demonstration begins that payoff. The user's Autumn review
rejected generic texture praise as an opening, so “这质感非常好” alone is no
longer a default hook. Word emphasis remains optional performance guidance.
Both bundled skills and the planning template use this rule; the execution
tools, 2–4 second hook target, original speed and resource defaults are unchanged.
This is a planning revision, not a newly generated or approved video.

## 0.3.6: selectable emphatic openings

Alongside the default short dialogue, the bundled skills now offer a single
presenter statement or rhetorical question with natural word emphasis and an
optional two-note sting. The [hook options](skills/baiying-dialogue-video/references/hook-options.md)
cover texture, customer response and outfit-reveal angles, their evidence needs,
vocal delivery, typography timing and sound placement. Retained hooks still
target 2–4 seconds at original speed. These are editable planning options;
no prosody API or automatic paid generation was added. The new delivery style
has not yet been validated through a generated audiovisual example.

## 0.3.5: reusable commerce editing defaults

The plugin now bundles both the `video-editer` execution skill and the
[`baiying-dialogue-video` orchestration skill](skills/baiying-dialogue-video/SKILL.md).
For product demonstration cuts, the caller applies the
[shared editorial rules](skills/baiying-dialogue-video/references/editorial.md)
unless the user specifies a different direction:

- About 45 seconds, over 30 and under 60; hook and body stay at original speed.
- Retain a 2–4 second offscreen question and presenter reply, then show the real
  evidence. Generate only a needed, authorized hook using matching references.
- Cut to actual selling-point demonstrations and detail shots; typography
  emphasizes one current point with consistent fonts and colours.
- Use one decorative transition by default, at most two, at major topic changes.
  If two are used, their centres are at least 15 seconds apart. Default length
  is 0.32 seconds (0.24–0.48 allowed). The historical zero-transition option was
  superseded by the current minimum of one; do not add a second merely to fill a quota.
- GPU-first execution, one shared heavy task and queued retries remain in place.

These are scoped skill defaults, not automatic timeline operations or runtime
caps. Copy the [planning template](skills/baiying-dialogue-video/assets/workflow-plan.json)
and map it to current tool schemas. Existing local footage needs no Baiying
collection; generated hooks require the available external provider and account
assets. Page-turn artwork in the tested case was a custom overlay, not a new
native transition. No new tool or built-in flower-text template API is claimed.

Explicit audio tracks now normalize to the mixer's 48 kHz clock before
sample-based delay, preserving timeline placement for 32/44.1 kHz source voices.
See [validation](VALIDATION.md) for the checked release scope.

## 0.3.4: shared task queue, resource controls and generated hooks

- Media preparation and rendering share a durable FIFO. Failed renders retry
  through that queue using the saved revision; submission returns a job ID,
  and only `succeeded.result` is a completed output.
- Heavy work has one shared slot, including compatible synchronous tools.
  Portable defaults bound CPU threads, lower worker priority, reserve memory
  and wait when the host is busy. GPU H.264 encoding is preferred after a real
  encoder probe, with an explicit bounded CPU fallback or a strict GPU mode.
- Generated opening hooks use account profiles, reference slices, durable
  external task records and explicit review before insertion. The calling
  agent uses its authorized generation provider; this plugin makes no generation
  calls. Local ASR is separate. Preserve the accepted body edit and verify identity, clothing, speech
  and the join. Original speed is supported; an explicitly requested 1.25x
  hook retimes picture and separate audio together while preserving pitch.
- The audio, frame-coverage and cadence controls below remain available.
  Source-bound events retain precision when clips are trimmed, sped up or
  shifted by a prepended hook.

See [resource controls](skills/video-editer/resources.md) and
[generated hooks](skills/video-editer/generated-hooks.md) for the workflow.

## 0.2.0: audio and frame-accurate delivery

- Main clips now expose gain, mute and independent 25 ms default fades (set to
  0 to disable). `clip_update` also edits `shot_type`; custom role/shot labels
  are validated and preserved. Master gain defaults to 0 dB, normalization and
  limiting default off. `audio_configure` enables explicit processing.
- Audio is assembled separately in float PCM and encoded once at delivery.
  Hard cuts concatenate encoded video packets instead of repeatedly encoding
  the growing film. Each rendering pass explicitly encodes at 25 fps, and final
  delivery checks decoded frame count and video coverage against the timeline.
- `timeline_validate` measures planned audio by default: LUFS, true peak,
  adjacent RMS differences and boundary sample jumps. It also warns about
  repeated source ranges. `check_audio=false` skips the audio render/analysis;
  `valid:true` means executable, not that warnings or creative review passed.
- Every cut exposes integer frames/timecodes. `frame_alignment=cover` pads a
  fractional frame; `nearest`/`floor` explicitly permit trimming a fractional
  tail. `render_audio_only` and `audio_replace` repair audio with copied video.
- `media_cadence_inspect` distinguishes decoded coverage from sampled motion
  updates. `media_import` supports relative paths with explicit `base_dir`,
  content deduplication and same-name warnings.

See [parameters and workflow](skills/video-editer/audio-and-delivery.md) and
[reproducible validation](VALIDATION.md). The marketplace plugin is named
`video-editer` and its MCP server is `video_editer`. Update this marketplace entry and reinstall the
Python package in the interpreter used by MCP, then reconnect/open a new task.

## Install anywhere

Requirements: Python 3.11+, `mcp`, `imageio-ffmpeg`, Pillow, NumPy and psutil. FFmpeg needs libx264
and libass; imageio-ffmpeg supplies a binary fallback. Chinese text requires an
installed CJK font (Microsoft YaHei on Windows, or Noto Sans CJK SC on Linux).
Proprietary system fonts are not redistributed.

Extract the ZIP anywhere, including a path with spaces, and install into the
Python environment used by the host agent:

```console
python -m pip install .
python -m video_editer
```

The second command starts the stdio MCP server and waits for a client; it is
not a web interface. `video-editer` is an equivalent console entrypoint.
Codex loads `${PLUGIN_ROOT}/server.py` from the bundled `.mcp.json`.
Other MCP-compatible hosts can use this configuration after pip installation:

```json
{"mcpServers":{"video_editer":{"command":"python","args":["-m","video_editer"]}}}
```

For a dedicated virtual environment, use its absolute Python executable as
`command`. No editing platform, web server, database server, model SDK, API key
or `.env` is required. The plugin does not load another project's configuration.

Optional host environment settings:

- `VIDEO_EDITER_DATA_DIR`: defaults to `<user-home>/.video_editer`. Projects,
  imports and render outputs live here, outside the installed plugin/cache.
- `VIDEO_EDITER_RESOURCE_DIR`: defaults to `<user-home>/.video_editer_runtime`.
  Policy, encoder probes and the heavy-work lock are shared across data
  directories that use this resource directory.
- `FFMPEG_BIN` / `FFPROBE_BIN`: executable overrides. Without ffprobe,
  inspection falls back to FFmpeg.
- `VIDEO_EDITER_FONT`: installed font family, e.g. `Noto Sans CJK SC`.
- `VIDEO_EDITER_FONT_FILE`: font file for the new procedural graphic templates.
  Defaults to installed Microsoft YaHei Bold, Noto Sans CJK Bold or PingFang.
  Set both font options when you want consistent legacy ASS and new graphic text.

Editing is local and does not need a proxy or network access. Installing Python
dependencies may need package-index access. Existing platform projects are not
automatically migrated, modified or removed.

## Independent package

- `video_editer/mcp_server.py`: atomic tools and timeline operations.
- `video_editer/contracts.py`: validation, diff and keyframe math.
- `video_editer/timing.py`: explicit source-time bindings and per-point timing.
- `video_editer/preflight.py`: structured cross-track diagnostics.
- `video_editer/jobs.py` / `locking.py`: durable render attempts and OS-level locks.
- `video_editer/canvas.py` / `animation.py`: explicit canvas and transform contracts.
- `video_editer/visuals.py`: procedural callout typography and evolving reveal masks.
- `video_editer/render_queue.py` / `processes.py`: detached FIFO worker and cancellation.
- `video_editer/task_queue.py`: shared media/render dispatch and queue inspection.
- `video_editer/resources.py` / `encoders.py`: resource admission, low-priority
  execution, hardware encoder probes and bounded fallback.
- `video_editer/hooks.py`: account profiles, reference plans, durable external
  generation records, review and hook insertion.
- `video_editer/audio.py` / `quality.py`: float PCM sound assembly, loudness,
  true peak, decoded frame coverage and sampled motion cadence.
- `video_editer/media_jobs.py` / `media_ops.py`: background video registration,
  content identity, local audiovisual windows, actual-PTS frames and technical scans.
- `video_editer/media_store.py` / `segments.py`: SQLite evidence/index/candidate
  versions, literal Unicode search and separate observation coverage.
- `video_editer/engine.py`: project storage and FFmpeg execution.
- `video_editer/assets/`: eight bundled callout/paint-phase PNGs.
- `server.py`: portable launcher, not a connection to another platform.
- `skills/video-editer/SKILL.md`: guidance for the calling agent.

The plugin ID and Skill are `video-editer`. The MCP server name, display
name and Python package are `video_editer`.

For installations using the previous plugin ID, refresh the marketplace and
install `video-editer`; disable the previous installation to avoid duplicate tools.
Existing projects remain in the same `VIDEO_EDITER_DATA_DIR`.

## Atomic tool contract

The MCP tools cover project creation, material import/inspection/frame
extraction, timeline get/snapshot/diff/validate/restore, track creation,
clip add/update/insert/split/remove/reorder, transition list/apply,
callout list/add/update/remove, overlay add/keyframe/remove, subtitle
add/update/remove, audio bed/track add/remove/source gain, preview/final
rendering and technical output/quality inspection, plus source/output time maps,
event bind/unbind, individually timed points, audio peaks and render status/retry.
Additional entrypoints cover canvas/camera/overlay motion, queued execution,
resource policy, generated hooks and explicit audio/delivery inspection.

Timeline mutations return an incrementing `revision` and `undo_snapshot`.
Restore creates a new revision and is itself undoable. Read-only tools do not
write project state. Failed parameter updates leave project.json unchanged.
The prompt field is inert metadata. There is no LLM planner; local ASR only supplies speech evidence.

Media imports preserve source files and keep copies. Restoring a timeline does
not delete imported media. Render/frame tools create artifacts, not revisions.

## Tracks and timing

- Main clips are sequential video selections or still images. For images,
  `clip_add` / `clip_insert_at` start/end are virtual source seconds in 0..3600;
  playback duration is (end-start)/speed. No automatic image movement is added.
  Canvas defaults to 1080x1920. `canvas_configure` accepts even dimensions
  256..3840, up to 8,294,400 pixels, `contain`/`cover`, and a #RRGGBB background.
  The shared clock remains 25fps. Speed is explicitly 1.0 or 1.25. Silent sources get
  silence for reliable assembly. There is no automatic crop based on role.
- `clip_insert_at` uses a zero-based clip index; `clip_split.at_seconds` uses
  playback seconds relative to that clip, converted using its speed. A split
  preserves duration/speed and puts the outgoing transition on the right half.
- Overlay tracks accept image/video, normalized top-left x/y, exact width AND
  height (stretch), and opacity. Full rectangles must fit at all keyframes.
  Position keys use absolute timeline seconds and piecewise linear interpolation;
  base position anchors start and the last key holds. Visibility is [start,end).
  `overlay_transform_set` adds scale, clockwise rotation, opacity, linear/ease-in/
  ease-out/ease-in-out/hold curves, and fixed circle/rounded-rectangle masks.
  Full details and bounds are below; no automatic subject tracking is performed.
- Caption tracks contain explicit text/timing.
- Audio tracks support timeline start, source trim, duration, gain and fades.
  Duration zero uses the available source remainder capped to output end.
  `audio_duck_source` is constant source gain, not speech-aware ducking.
  Track gains are additive (amix normalize=0); callers must avoid clipping.
  A short explicit dialogue bed is padded with silence and never cuts the video.

Unbound events retain absolute timestamps. Explicitly bound events follow their
clip's trim, speed and order, including preceding transition overlap.
The caller must review synchronization. Timeline diff includes property
changes, order, custom tracks and settings, not just changed IDs.

## Explicit camera and graphic motion

`clip_camera_set` accepts `start_zoom`, `end_zoom` (1..4), `start_x`, `start_y`,
`end_x`, `end_y` (0..1 within available crop), and `easing`. It zooms/pans the
fitted image/video across its selected playback range. At zoom=1 there is no
crop room, so pan has no visible effect. Trimming/speed changes retime this
whole camera move. Pass `camera=null` to detach before splitting the clip;
set the two resulting camera moves explicitly. No image/video content is inferred.

`overlay_transform_set` replaces the key list. Each key has `at` and optional
`x`, `y`, `scale`, `rotation`, `opacity`, `easing`. Missing fields inherit the
previous state. `x/y` remain the top-left of the untransformed rectangle; scale
and rotation act around its centre. Easing on the destination key controls
the interval leading into it. Scale is 0.05..4, rotation -360..360 degrees,
opacity 0..1. Up to 256 keys and one hour per overlay. Empty keys reset motion.

Validation checks the transformed rectangle at all rendered frames and keys,
not just endpoints. Out-of-frame motion is rejected, never silently repositioned.
Masks are fixed `none`, `circle` (ellipse for nonsquare rectangles), or
`rounded_rect`, not arbitrary animated masks. Legacy x/y keys still work.
Bindings also retime transform keys. Detach before editing a bound key list.
Animated graphics are rasterized locally and can be slower than simple overlays.

## Explicit source-time binding

1. Read `timeline_time_map` to obtain each clip's source range, speed, output
   start/end and outgoing overlap. Mapping and rendering share a 25fps clock.
   Each selected range is rounded UP to a whole frame; `tail_padding` reports
   the less-than-40ms last-frame/silence pad. No extra source content is read and
   no selected speech is trimmed to fit. Preset overlaps also use whole frames.
   This prevents per-clip AAC/frame rounding from accumulating timing drift.
2. Create the timed callout/subtitle/overlay/audio entry.
3. Call `event_bind(project_id, event_id, clip_id, source_start, source_end)`.
   Anchors use seconds in the original media and a particular clip instance,
   not an asset ID alone. The resulting event range follows that selection.
4. `timeline_get` and the renderer resolve the same source/output mapping.
   For point groups and keyed overlays, existing sub-event times must already
   map into the selected source range when binding; they are stored as source
   anchors and retimed on subsequent edits. Audio duration also follows the
   binding; audio media itself is trimmed, not automatically time-stretched.

Trimming away a bound range or removing its clip is rejected. On clip split,
events entirely in the right half rebind to the new clip; a spanning event
must be explicitly detached or split by the caller. No text is silently cut.
`event_unbind` freezes currently resolved times/keys/points. Detach before editing
absolute timestamps, keys or point lists, then bind again if needed. Plain-text
subtitle changes do not require detaching. Unbound overlays/audio are not rippled.
The plugin does not discover speech boundaries or guarantee that the agent's
chosen source range is a complete sentence.

## Callouts and transitions

Text and event times come from the calling agent. No auto-retiming, text
generation or eight-event limit. Capacity violations are rejected, not truncated:

- Compact comic templates: 10 characters; ordinary templates: 16.
- `point_list`: exactly two nonempty points, up to 7 characters each, joined
  by | through the legacy `callout_add`; minimum 1.1 seconds. That compatibility
  preset retains its +0.72 second reveal.
- `statement_block`: two nonempty lines, max 14/16 characters.

For arbitrary spoken beats use `callout_points_add` with 1-4 objects containing
`text`, `start` and `end`. Every reveal and exit is explicitly caller-supplied;
times must be in reveal order, text 1-7 characters and each duration >=0.12s.
Each card has a fixed cream/red style and 60ms entrance/exit fade. Rows have
independent visibility and non-overlapping spacing. Update using the `points`
argument of `callout_update`; do not edit the container's text/start/end instead.
Whole groups can be source-bound when all points belong to the same clip.
Use separate groups or overlays/captions for points spanning different clips.

Requested
graphics/typography failures are errors, not successful renders with missing
effects. Several compact template IDs share two graphic plates; they are not
eleven distinct artworks and are not guaranteed pixel-identical to references.

Legacy transitions are explicitly selected. The 20 older seasonal IDs map to FFmpeg xfade
presets, not newly generated seasonal illustrations. `paint_flash` combines
a wipe with four evolving shape phases. `zoom_punch`, `whip_pan` and
`detail_smash_cut` share an immediate crop-in preset; `none`/`punch_cut`
are hard cuts. Overlap reduces assembled duration. Clips too short for their
incoming/outgoing overlap are rejected, not extended or given a different effect.

Four additional callout styles (`burst_pop`, `minimal_label`, `ribbon_tag`,
`frost_glass`) have independently drawn geometry and measured text layout,
not shared bitmap plates. Text is still supplied by the agent (max 10 characters).
They sit in a compact side region at 60% canvas height, preserve proportions,
and animate their text and decoration together. They are not 3D/photorealistic
artwork or pixel-identical copies of references. `callout_list_templates`
describes use cases, situations to avoid, text capacity and suggested duration.

Six additional procedural transitions reveal the actual next shot using a
different mask on every frame, plus a moving coloured edge:

| ID | Actual geometry | Intended use |
| --- | --- | --- |
| `summer_liquid_flow` | Wavy liquid front overflows from left to right | Lightweight summer materials |
| `summer_bubble_reveal` | Multiple growing circles merge | Fresh colours, airy details |
| `spring_petal_sweep` | Petal-shaped corner expansion | Spring/floral/new arrivals |
| `winter_frost_grow` | Jagged crystal edges grow inward | Knitwear, wool, warmth |
| `winter_snow_curtain` | Descending snow lobes and falling particles | Winter outfit changes |
| `graphic_ink_sweep` | Staggered diagonal brush bands | Strong section/shot-size changes |

`transition_list` distinguishes these from legacy presets and returns season,
intent, avoid-scenarios, coverage and overlap. They are mathematical animations,
not physical fluid simulation. No local random/template selection occurs.

There is no 30-second minimum/cutoff, role-based acceleration, source-content
filter, automatic transition selection or closing-sentence selection.

## Immutable outputs and verification

Each preview/final uses a new directory:
`<data>/projects/<project>/mcp_renders/<preview|final>-r<revision>-<unique-id>/`.
Old jobs are not overwritten. Use the returned absolute path, not an HTTP URL.
Each successful job includes timeline.json, result.json and inspected metadata.
Preview preserves the canvas aspect with longest edge <=960px; final uses the
configured canvas. The default still produces a 540x960 proxy and 1080x1920 final.

`timeline_validate` returns structured issues with severity, code and affected
event IDs. Events outside output duration, absent selected media, invalid audio
source ranges and missing audio streams are errors that block rendering. Visual
region collisions and additive audio headroom are warnings, not automatic edits.
Moving-overlay envelopes and callout/caption bounds are conservative; a warning
is not proof of product occlusion and intentionally layered artwork may overlap.

`quality_inspect` checks black/freeze frames, decoding and audio-stream presence.
It includes audio measurements from `audio_inspect`, including integrated LUFS,
sample peak and measured true peak. Peak near full scale is a clipping risk,
not proof of audible distortion. These checks do not prove speech completeness, factuality,
semantic synchronization or aesthetics.

## Failed render recovery

Each render saves `input.json`, `timeline.json` and `state.json` before execution.
`render_job_status` reports the stage, error or result; a released worker lock
identifies an interrupted attempt. Errors include the job ID.

`render_retry` queues a NEW job from the failed job's saved revision, not the
latest project timeline. Completed base/graphics/caption/audio stages are reused
only when their file checksums and runtime fingerprint match. Changed/missing
source media or tampered input snapshots reject retry; corrupted checkpoints
are re-rendered. Earlier attempts and outputs remain untouched.

If the agent has edited the timeline to fix an editorial error, call a new
`render_submit` instead of retrying an old revision. Recovery is
stage-level: a failure inside the base FFmpeg render repeats that base stage,
not just the unfinished source clip.

## Background rendering and cancellation

- `render_submit(project_id, preview=false)` saves an immutable snapshot and
  returns `job_id`/revision before input hashing and encoding. The worker checks
  source bytes and validates the plan. Later edits do not modify queued plans.
- Media and render jobs share FIFO order per data directory and one heavy-work
  slot across processes using the same resource directory. Compatible
  synchronous render/import/analysis tools also acquire that resource slot.
- `render_queue_list` and `render_job_status` report queued/running/terminal
  states and current stage. Progress is stage-level, not a precise percentage.
- `task_queue_list` shows combined media/render order and resource wait reasons.
  A resource wait remains queued and does not require resubmission.
- `render_cancel` targets an exact job. Queued jobs become cancelled; running
  jobs check cancellation between stages/frames and during subprocess execution.
  Only owned child processes are terminated. Confirm `cancelled` with status;
  a job that already finished is not undone. All artifacts remain on disk.
- MCP startup/reconnection automatically resumes queued work; either queue-start
  tool can also drain it. Some hosts terminate child workers on disconnect, so
  offline execution is not guaranteed. No independent OS service is installed.
  Interrupted, failed and cancelled jobs require an explicit retry.
  `render_retry` returns a new queued attempt from the saved snapshot; poll its
  new job ID instead of treating submission as completion.
- Source hashes and runtime fingerprints are checked for queued work. Changed
  media/code/fonts fail closed; submit a new job after reviewing those changes.

Project writes use nonblocking cross-process locks; a competing write returns
a busy error for the caller to retry. Rendering captures an immutable project
snapshot under a short lock and checks source-file hashes before delivery.

## GPU and resource policy

Use `resource_status` for a read-only policy/status check, `resource_probe` for
a tiny real encoder test, and `resource_configure` for subsequent work. Defaults
keep heavy concurrency at 1, use at most four CPU threads, and apply memory and
system-CPU admission checks. Workers run at low priority and are stopped if
their sampled memory exceeds the configured budget.

Automatic H.264 selection probes NVENC, QSV, AMF and VideoToolbox when present.
It records a reason if bounded CPU encoding is needed. `gpu_required` or
`allow_cpu_fallback=false` rejects that fallback. Filters, decoding and graphics
may still use CPU; this is not an entirely GPU pipeline or a GPU-utilization
quota. Shared resource directories coordinate one host, not multiple machines.
See [policy defaults and limitations](skills/video-editer/resources.md).

## Long-source evidence (P0/P1)

The new workflow adds 20 tools; it is opt-in and does not change existing edits.
See [Agent evidence workflow](skills/video-editer/long-media.md) for complete
annotation/candidate contracts and observation requirements.

- Background video import: `media_register` supports `managed_copy` (default)
  and explicit no-copy `reference`. A streaming SHA-256 identifies source bytes.
  Images/audio retain `media_import`; old video imports can be fingerprinted.
- `media_prepare`, `media_job_status/list/cancel/retry`, `media_queue_start`
  provide persistent media work through the shared media/render FIFO. Completed
  evidence is cacheable; partial operations restart, not byte-resume.
- `media_window_list`, `media_storyboard_page`, `media_segment_preview`,
  `media_frames_at` provide overlapping window directories, paginated images,
  source-audio previews and actual decoded frame timestamps. Previews/scans are
  limited to 600 seconds per request; frames to 48 per request. No full-source
  transcode or global fixed sampling is imposed.
- `segment_index_upsert/search/get` and `media_coverage_get` preserve host-written
  understanding, evidence references and precise review gaps. Sampled frames,
  generated proxies and reported audiovisual review remain separate states.
- `candidate_add/update/list/get/preview` store and compare versioned source
  intervals plus boundary assessments. They never add clips to the timeline.

Storage is local, project-scoped SQLite (schema 1) plus immutable artifacts.
Identity uses SHA-256; records use `(kind, id, version)` and pin their source hash
and evidence versions. Updates are compare-and-swap appends. Old files/versions
are retained; insufficient storage fails instead of deleting anything. Stat
metadata is only a fast change hint. Processing and final rendering check bytes.

Search is literal Unicode substring plus exact tags, not semantic retrieval or
embedding search. The plugin verifies structural evidence consistency, not
whether an Agent really listened or whether its supplied claims are true.
Generated opening-hook orchestration is available through the dedicated hook
tools. A cross-project creative library and editable structure templates remain
outside this release.

## Tests and distribution

```console
python -m pip install ".[test]"
python -m unittest discover -s tests -v
python scripts/build_bundle.py
python scripts/verify_bundle.py <zip-path>
```

Offline tests use synthetic fixtures, legal/illegal edits, real pixel motion,
audio amplitudes, exact callout timing, preserved prior exports, and real MCP
stdio calls. Independence tests check package-owned assets, absent platform/model
imports, rendering with networking blocked, actual paint/winter transitions and
audio beds that cannot shorten video.

The bundle builder uses an explicit allowlist: no caches, user media/projects,
credentials or obsolete artwork. Bundle verification relocates the ZIP, creates
a clean virtual environment, installs only declared runtime dependencies, then
performs real editing/rendering through MCP. Installation requires pip access.
Tests print review artifact locations and do not perform cleanup.

Long-media regressions cover real local previews/audio, VFR and non-zero-start
source PTS, corrupt caches/media, version conflicts, frame-only quote rejection,
reference drift, cancellation/retry, nested annotations, concurrent project
writes and candidate-to-original render. `python -m scripts.benchmark_long_media`
optionally generates a decodable 2-hour, >200MB synthetic fixture and tests deep
seeking, cache reuse and original-source export. It retains the fixture/report;
it is not a real-commerce semantic recall benchmark.

Additional tests cover source-anchor transformations, split/detach behavior,
invalid points, pixel-level point visibility, cross-track warnings, audio peaks,
cross-process write contention, injected failures, interrupted workers, corrupt
checkpoints and changed-media rejection.

New tests exercise landscape/still-camera rendering, all six masks against real
red/blue video frames, actual transform/opacity pixels, four distinct callout
drawings, malformed geometry, source-bound transform timing, background FIFO,
immutable queued revisions and queued/running cancellation. `python -m
scripts.showcase` produces a synthetic audible capability reel and frame sheets.

Remaining limits: 25fps and two speed choices; sequential main track rather than
arbitrary multitrack video editing; no arbitrary animated masks, tracking,
chroma key, advanced audio mastering, per-clip render cache, distributed queue
or automatic semantic quality review. The agent remains responsible for intent.
