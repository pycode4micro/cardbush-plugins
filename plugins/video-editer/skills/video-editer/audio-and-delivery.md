# Audio, frame clocks and delivery

Use this reference for quick cuts, mixed generated clips, pops, loudness changes,
repeated motion, frozen tails or soundtrack-only repair. These are technical
measurements, not proof of speech correctness or creative quality.

## Before cutting

- For product-demonstration edits, apply the bundled
  [source audio eligibility rules](../baiying-dialogue-video/references/source-audio.md)
  before admitting each selected source range. Missing or silent body audio is
  excluded; only a full-body hook display with verified independent sound is
  exempt. Metadata stream presence, whole-file loudness and ASR alone cannot
  establish that each selected range has usable sound. Mark uncertain or failed
  checks pending, recalculate usable material coverage, and preserve the source.
- Inspect source audio with `audio_inspect`: integrated LUFS, true peak, sample
  peak and RMS are different metrics. Silent measurements are `null`.
- For generated animation, use `media_cadence_inspect`. It reports decoded
  coverage and consecutive near-duplicate luma differences over an explicit
  sample. A 24 fps container can contain only about 8 distinct updates/second.
  Static scenes can have the same statistic; inspect motion before blaming
  generation. Do not silently interpolate, regenerate or charge for replacements.
- `media_import` accepts an absolute local path, or a relative path plus an
  explicit absolute `base_dir`. Identical bytes reuse an asset by default;
  `deduplicate=false` intentionally creates another asset. Same names with
  different bytes produce a warning. Use the returned ID, not the filename.
- Compare same-source selections before adding them. Unless replay is intended,
  keep source ranges increasing and non-overlapping. Trim handles before
  assembly: A01 [5, 7.5) followed by [7, 9) repeats 0.5 seconds.

## Explicit sound and time

`clip_add`, `clip_insert_at`, `clip_update` support:

| Field | Meaning |
| --- | --- |
| `audio_gain_db` | 0 dB default; -96..24 dB |
| `audio_mute` | false default |
| `audio_fade_in_ms`, `audio_fade_out_ms` | 25 ms default, individually switch off with 0; each limited to half a short clip |
| `frame_alignment` | `cover` default; `nearest` or `floor` explicitly permit fractional tail trimming |
| `role`, `shot_type` | Nonblank custom labels preserved as metadata, at most 80 characters; update supports both |

Fades do not shift the picture or overlap two voices. For a continuous source
take where a fade is undesirable, set both values to 0. `clip_split` preserves
the outer fades and disables fades at its new internal boundary.

The master defaults to gain 0, normalization off and limiter off.
`audio_configure` sets master gain, optional `target_lufs`, and an optional
oversampled limiter with no makeup gain. Setting target LUFS uses measured
static gain; it does not compress varying shots. AAC encoding can change true
peaks, so inspect the delivered file even with a limiter. Do not set a target
just because a render is requested; preserve the user's intended sound level.

Source sound, replacement bed and extra tracks have separate controls. An
additive mix can exceed headroom; changing clip gain cannot fix a loud separate
music track. Keep all settings and track choices in the project snapshot.
Use `audio_track_add(role="sfx", label="reveal of collar")` for explicit cue
metadata; `voiceover`, `music` and `unspecified` are other roles, not mixer buses.
Older entries remain unclassified until explicitly tagged. Change gain, fades
or metadata with `audio_track_update(audio_id=...)`, preserving the entry ID,
time binding and undo snapshot. Detach a binding before changing its start or
duration. Replacing a track by removing and re-adding it loses those references.
An explicit extra track accepts `playback_speed=1.0` or `1.25`; its `duration`
is output time and `source_start` is source time. For a sped-up hook, supply the
same speed for picture and separate sound when they are a lip-synced take.
An independently sourced voiceover over silent display footage may use a
different speed when the user requests it; record both rates and keep the
picture at its requested speed. Audio tempo changes preserve pitch.
Do not accelerate the accepted body edit unless requested.

Separate tracks are normalized to 48 kHz before sample-based delay. Keep
`start` and `source_start` in seconds; do not compensate manually for a 32/44.1
kHz source by shifting the event. Recheck the actual mixed voice onset and tail
after adding a generated hook, even when the source transcript is complete.

The time map uses integer 25 fps frame indices. `end_frame_exclusive` and
`end_timecode` exclude the next frame. Use its actual output times for music
sync, including transition overlap, `tail_padding` and `trimmed_tail_seconds`.
Do not assume raw selected seconds sum to output duration. `cover` padding
stays below one frame per clip; it must not conceal seconds of missing footage.

## Validate and repair

Review editorial continuity separately from technical audio joins. Keep a
complete spoken unit across relevant detail shots, inspect cuts already baked
into source composites, and continuously review the last hook second through
the first 8–10 body seconds. Check topic, pronoun/context, action completion and
caption alignment. Short fades repair neither broken meaning nor rushed action;
frame samples and low boundary jumps do not prove a smooth viewing experience.

1. `timeline_validate` defaults to audio checks. It renders only planned audio
   to temporary float PCM, applies clip/bed/extra-track/master settings and
   measures predicted LUFS/true peak under the shared heavy-work slot. This
   costs local CPU but no paid service.
   `check_audio=false` is only a structural check and reports audio unchecked.
2. Review `source_range_overlap`, `source_time_reversal`, `audio_level_jump`
   (default RMS difference >8 dB across 100 ms windows) and
   `audio_join_discontinuity` (exact boundary sample jump >0.1 full scale).
   These warnings identify locations to listen to; they do not prove an audible
   pop. Thresholds are configurable. `valid:true` only means executable.
3. Adjust clip gain/fades or separate-track levels within the requested edit.
   Do not fix repeated action by freezing frames or stretch speech to hide a
   missing tail. Separate source cadence from accidental timeline replay.
4. Submit `render_submit`, poll `render_job_status`, and deliver only a
   `succeeded.result`, including its measured `audio` and `video_stream` fields
   and immutable path/revision. Hard cuts encode each clip once and concatenate
   packets; the soundtrack is assembled in float PCM and encoded to AAC once.
   A mismatched decoded frame count fails delivery instead of declaring success.
5. For sound-only repair use `render_audio_only(project_id)` then
   `audio_replace(video_path, audio_path, dest)` with a new absolute destination.
   The latter copies video packets, pads short audio with silence and trims long
   audio to video coverage. Existing files are never overwritten.
   These compatible synchronous repair tools acquire the same heavy-work slot;
   they do not return an asynchronous job ID.

When claiming a fix, report measurements and sampled scope. These regressions
do not establish the cause of every third-party file's gain or motion defects.

For designed sound effects, separately verify each cue in the delivered mix.
Track presence, unchanged prior audio and whole-video loudness do not establish
that an effect is perceptible. Compare isolated cues and the contextual mix at
the same playback level; do not normalize A/B examples independently. A fixed
offset below a very short quiet speech window can make cues effectively vanish.
Use nearby complete speech and the rendered cue envelope to adjust individual
gain, timbre or timing. Record measured signal presence and listening status
separately; user feedback that a cue is inaudible reopens that review.

### Queued cue review (0.3.9+)

1. Tag intentional effects `role="sfx"` or pass explicit `audio_ids`. Submit
   `audio_review_submit(project_id, audio_ids, render_job_id=...)` after a
   successful preview/final render. The render job selects its immutable saved
   timeline, not today's edit. Without `render_job_id`, only a saved snapshot
   of the planned mix is checked; delivery remains explicitly unchecked.
2. Poll `media_job_status`. Review generation, source verification and retries
   use the shared heavy slot. `media_job_retry` creates a queued attempt from the
   same saved request; after changing the edit, submit a new review instead.
   Resource waiting is not failure and must not trigger duplicate submissions.
3. Read each cue's report and equal-level `with.wav`, `without.wav`,
   `contribution.wav`, and optional `delivered.wav`. All comparisons use the
   full mix's actual master gain; normalization is never recomputed separately
   for the version without the cue. Contribution is the marginal difference
   after the master, including limiter interactions, not an isolated source stem.
4. Warnings flag muted/negligible cues, low cue RMS (initially below -40 dBFS)
   or possible masking (more than 14 dB below nearby active audio). Context is
   the 75th percentile of non-silent 20 ms windows, not automatic speech detection.
   These are review prompts, not target levels, creative approval or instructions
   to raise every effect. Compare with complete nearby phrases and listen.
5. Delivered signal comparison reports `confirmed`, `not_detected`,
   `inconclusive` or `not_testable`. Lossy encoding or changed audio runtime can
   make a comparison inconclusive. Every report keeps `human_listened=false`
   and `perceptual_review=needs_listening`; record actual listening/user review
   separately. Even confirmed signal presence does not certify audibility.

One request handles 1–16 explicit cues, each at most 15 effective seconds, in
a timeline of at most 600 seconds, with 0.25–5 seconds of context per side
(default 1.5). It renders one reference mix and one version without each cue
sequentially, so it can take time. It creates review artifacts only: no timeline,
gain, video or production resource policy is changed. No model/service is used.
This check does not replace the source-audio gate, full expected-speech interval
checks or human review of the mix. Preserve the final job/revision and report IDs.

Source eligibility and final-mix audibility are separate checks. Inspect each
intended sound-bearing output interval for unexpected silence, especially hook
handoff, extra-track boundaries and the ending. Check global and clip mute,
source volume, separate-track gain and coverage. Whole-video LUFS or background
music elsewhere cannot clear a silent interval; natural pauses are reviewed in
context rather than automatically removed. User-reported continuity failures
reopen that section for repair even if an earlier technical review succeeded.

## 0.5.0 bound delivery inspection

For the staged source-only commerce workflow, queue delivery_inspection_submit after each successful preview/final. Poll media_job_status and use delivery_review_form with the succeeded inspection ID. It includes PTS images, listening WAV, per-selected-source signal qualification and per-final-segment sound. The inspector does not import its output into the edit or change the timeline revision.

Pass inspection_job_id to delivery_review_record along with actual observations and matching sound-review job IDs. Mandatory not_applicable, unrelated JSON evidence, missing inspection, unmeasured planned sound, and a revision-only/content-unrelated repair cannot produce delivery_ready. A new output requires a new bound inspection; a repeated request for the same immutable output reuses its queued/running/succeeded inspection. No measurement certifies listening or naturalness.
