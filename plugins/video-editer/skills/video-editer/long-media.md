# Long-source evidence and candidate selection

The host Agent supplies understanding. These tools have no LLM, transcription,
embedding, image-generation or automatic editorial selection service.

## Access and jobs

- `media_register(project_id, source_path, mode)` is a background **video** import.
  `managed_copy` (default) keeps a separate copy; explicitly choose `reference`
  to avoid copying a large original. References must stay accessible and unchanged.
  Use legacy `media_import` for images/audio; upgrade legacy videos with
  `media_prepare(operation="fingerprint")` before the evidence workflow.
- Poll `media_job_status`. Only `succeeded.result` is usable. Stage counts/bytes
  are technical progress, not how much the Agent has understood. Jobs run in a
  separate persistent FIFO from rendering; media and render workers can overlap.
- `media_job_cancel` retains artifacts. `media_job_retry` creates a new attempt
  of the saved request; completed verified evidence can be reused, but partial
  copy/hash/encode operations restart. It is not byte-level resume.
  `media_queue_start` drains queued jobs after host restart. Interrupted jobs
  require explicit retry; do not repeatedly retry a changed/missing source.
- If an unchanged file merely has a new mtime, `media_prepare(operation="verify")`
  recomputes the strong hash and refreshes the stat hint. Changed contents require
  a new asset; verification cannot attach old annotations to different bytes.
- Every range is in seconds relative to the original container start. Metadata
  includes its absolute start and video time base. Do not pass absolute PTS as
  a relative cut time. Proxy output starts at zero; its source map is nominal.
  VFR, low-fps and non-keyframe boundaries need actual decoded source timestamps
  from `media_frames_at`, not a frame-number/fps guess.

## Browse progressively, within the task's budget

1. `media_window_list` gives a paginated directory of overlapping ranges. It
   decodes nothing. `media_storyboard_page` optionally submits three thumbnails
   for each window on a small page; results are separate image paths and times.
2. Request selected windows with `media_segment_preview`. It preserves available
   source audio and makes no timeline edit. One window is at most 600 seconds;
   choose shorter windows and enough context for the task, not the maximum by
   default. No full-source proxy is automatically generated.
3. Actually view/listen using the host's available multimodal capabilities.
   A file path returned by this plugin is not evidence that you have watched it.
   If the host cannot inspect audiovisual content, keep speech/completeness
   unknown; report that missing capability instead of inventing a transcript.
4. Use `media_frames_at` (1–48 timestamps, up to 1920px longest edge) for targeted
   visual details. Frame evidence cannot substantiate spoken quotes or continuous
   action. `media_prepare(operation="scan", options={"start":...,"end":...})`
   reports black/silence signals only. These are not sentence boundaries or
   final clip decisions; fixed-camera narration may have no shot changes.

Cache reuse is scoped to the project and locked to content hash, parameters and
implementation/runtime. It verifies source/output bytes before use. Two explicitly
registered identical files in the same project may share derived files with
distinct evidence records. This is not a cross-project media or effects library.

## Store what was actually observed

`segment_index_upsert` takes a full annotation, with optional `segment_id` and
`expected_version` for append-only updates. Required fields:

```json
{
  "source_start": 1200,
  "source_end": 1210,
  "summary": "Host-authored description of this observed interval",
  "observation": "audiovisual",
  "evidence_refs": [{"id": "evidence_RETURNED_ID", "version": 1}],
  "tags": ["host-chosen content tag"],
  "quotes": [],
  "uncertainty": "What remains uncertain, or an explicit statement of none"
}
```

Observation is `audiovisual`, `audio`, `silent_video`, or `visual_frames`.
Continuous observations must be covered by preview evidence; audio observations
require a real audio stream. `visual_frames` requires `sampling_note` and frame
evidence, and cannot contain spoken quotes. Quotes, when actually heard, contain
`text`, `source_start`, `source_end` within the observed range. Optional `claims`
and `risks` are lists of host-authored strings. Do not convert attractive wording
into a product fact without source support. Optional `parent_ref: {id,version}`
links a refined child to an enclosing observation; retain uncertainty at each level.

`segment_index_search` matches literal Unicode terms (including Chinese) and
exact tags; it does not understand synonyms or rank editorial quality. Try
alternate terms when relevant; zero matches does not mean no useful footage.
`segment_index_get` can read a pinned historical version.

`media_coverage_get` keeps technical scan, generated preview, host audiovisual,
audio and silent-video review ranges separate. Sampled frames are points, not
continuous coverage. Consult unreviewed gaps when deciding where to explore next;
the plugin cannot verify the truth of an Agent's claim to have reviewed a range.

## Candidate pool, not an automatic cut list

`candidate_add` requires `source_start`, `source_end`, pinned `segment_refs`,
`reason`, `visual_evidence`, `tags`, `quotes`, `risks`, and `boundary_review`:

```json
{
  "opening": "complete",
  "ending": "complete",
  "speech": "complete",
  "action": "unknown",
  "note": "Host explanation of the boundary assessment"
}
```

Assessments can be `complete`, `incomplete`, `unknown`, or `not_applicable`.
Complete speech requires exact indexed quotes and audio-reviewed coverage;
the plugin checks those links, not whether the sentence really makes sense.
Use `context` for supporting commentary and `preferred_speed` (1.0/1.25) only
when you have chosen a speed. No fixed 2–5s duration rule or minimum-output
truncation is imposed. Longer complete expressions may be retained or replaced
with a better short expression according to the user's brief.

Use `candidate_list`/`candidate_get` to compare versions, and `candidate_preview`
to watch the proposed boundary with explicit pre/post context. Revise through
`candidate_update(expected_version=...)`, keeping the old record. For narration,
judge actual sentence/action closure, not merely the clip's duration.

Candidates never enter the timeline automatically. When satisfied, explicitly
pass the selected **original asset ID/range** to `clip_add`, and explicitly
choose speed, transitions and text. Never use the low-resolution review proxy
as the final source. Existing timeline validation, source-event time mapping,
preview review and render recovery remain the execution path.

Generated-effect ingestion and a reusable creative asset library are later
stages, not capabilities supplied by these long-media tools.
