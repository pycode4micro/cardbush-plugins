# video_editer

A standalone, model-free MCP plugin with its own Python/FFmpeg execution core
and bundled graphics, for Codex and other MCP-compatible agents. The caller
decides what to edit; the plugin exposes deterministic atomic execution tools.

## Install anywhere

Requirements: Python 3.11+, `mcp`, `imageio-ffmpeg`, Pillow and NumPy. FFmpeg needs libx264
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
- `video_editer/media_jobs.py` / `media_ops.py`: background video registration,
  content identity, local audiovisual windows, actual-PTS frames and technical scans.
- `video_editer/media_store.py` / `segments.py`: SQLite evidence/index/candidate
  versions, literal Unicode search and separate observation coverage.
- `video_editer/engine.py`: project storage and FFmpeg execution.
- `video_editer/assets/`: eight bundled callout/paint-phase PNGs.
- `server.py`: portable launcher, not a connection to another platform.
- `skills/zj-video-editor/SKILL.md`: guidance for the calling agent.

The internal Codex ID remains `zj-video-editor` for installed-client
compatibility; the display name and Python package are `video_editer`.

## Atomic tool contract

The 70 MCP tools cover project creation, material import/inspection/frame
extraction, timeline get/snapshot/diff/validate/restore, track creation,
clip add/update/insert/split/remove/reorder, transition list/apply,
callout list/add/update/remove, overlay add/keyframe/remove, subtitle
add/update/remove, audio bed/track add/remove/source gain, preview/final
rendering and technical output/quality inspection, plus source/output time maps,
event bind/unbind, individually timed points, audio peaks and render status/retry.
The seven new entrypoints are `canvas_configure`, `clip_camera_set`,
`overlay_transform_set`, `render_submit`, `render_queue_list`, `render_cancel`
and `render_queue_start`.

Timeline mutations return an incrementing `revision` and `undo_snapshot`.
Restore creates a new revision and is itself undoable. Read-only tools do not
write project state. Failed parameter updates leave project.json unchanged.
The prompt field is inert metadata. There is no model client or planner.

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
It also includes sample-peak headroom warnings from `audio_inspect`. Peak near
full scale is a clipping risk, not proof of audible distortion; this is not a
true-peak meter. These checks do not prove speech completeness, factuality,
semantic synchronization or aesthetics.

## Failed render recovery

Each render saves `input.json`, `timeline.json` and `state.json` before execution.
`render_job_status` reports the stage, error or result; a released worker lock
identifies an interrupted attempt. Errors include the job ID.

`render_retry` creates a NEW job from the failed job's saved revision, not the
latest project timeline. Completed base/graphics/caption/audio stages are reused
only when their file checksums and runtime fingerprint match. Changed/missing
source media or tampered input snapshots reject retry; corrupted checkpoints
are re-rendered. Earlier attempts and outputs remain untouched.

If the agent has edited the timeline to fix an editorial error, call a new
`render_final`/`render_preview` instead of retrying an old revision. Recovery is
stage-level: a failure inside the base FFmpeg render repeats that base stage,
not just the unfinished source clip.

## Background rendering and cancellation

- `render_submit(project_id, preview=false)` validates and snapshots immediately,
  then starts a detached local worker and returns `job_id`/revision without
  waiting for the video. Later edits do not modify queued plans.
- Background jobs use FIFO order and one worker per data directory. Synchronous
  `render_final`/`render_preview` remain compatible and run outside this queue.
- `render_queue_list` and `render_job_status` report queued/running/terminal
  states and current stage. Progress is stage-level, not a precise percentage.
- `render_cancel` targets an exact job. Queued jobs become cancelled; running
  jobs check cancellation between stages/frames and during subprocess execution.
  Only owned child processes are terminated. Confirm `cancelled` with status;
  a job that already finished is not undone. All artifacts remain on disk.
- A detached worker survives an MCP client exit, but not an OS shutdown. After
  host restart use `render_queue_start` to drain queued jobs. Interrupted, failed
  and cancelled attempts are not retried automatically; `render_retry` creates
  a new synchronous attempt from the saved snapshot. Cancel queued work first.
- Source hashes and runtime fingerprints are checked for queued work. Changed
  media/code/fonts fail closed; submit a new job after reviewing those changes.

Project writes use nonblocking cross-process locks; a competing write returns
a busy error for the caller to retry. Rendering captures an immutable project
snapshot under a short lock and checks source-file hashes before delivery.

## Long-source evidence (P0/P1)

The new workflow adds 20 tools; it is opt-in and does not change existing edits.
See [Agent evidence workflow](skills/zj-video-editor/long-media.md) for complete
annotation/candidate contracts and observation requirements.

- Background video import: `media_register` supports `managed_copy` (default)
  and explicit no-copy `reference`. A streaming SHA-256 identifies source bytes.
  Images/audio retain `media_import`; old video imports can be fingerprinted.
- `media_prepare`, `media_job_status/list/cancel/retry`, `media_queue_start`
  provide persistent media work independently of the rendering queue. Completed
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
Generated-effect ingestion, a cross-project creative library and editable
structure templates remain P2/P3, not part of this release.

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
