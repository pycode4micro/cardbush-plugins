# Audio, frame clocks and delivery

Use this reference for quick cuts, mixed generated clips, pops, loudness changes,
repeated motion, frozen tails or soundtrack-only repair. These are technical
measurements, not proof of speech correctness or creative quality.

## Before cutting

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

The time map uses integer 25 fps frame indices. `end_frame_exclusive` and
`end_timecode` exclude the next frame. Use its actual output times for music
sync, including transition overlap, `tail_padding` and `trimmed_tail_seconds`.
Do not assume raw selected seconds sum to output duration. `cover` padding
stays below one frame per clip; it must not conceal seconds of missing footage.

## Validate and repair

1. `timeline_validate` defaults to audio checks. It renders only planned audio
   to temporary float PCM, applies clip/bed/extra-track/master settings and
   measures predicted LUFS/true peak. This costs local CPU but no paid service.
   `check_audio=false` is only a structural check and reports audio unchecked.
2. Review `source_range_overlap`, `source_time_reversal`, `audio_level_jump`
   (default RMS difference >8 dB across 100 ms windows) and
   `audio_join_discontinuity` (exact boundary sample jump >0.1 full scale).
   These warnings identify locations to listen to; they do not prove an audible
   pop. Thresholds are configurable. `valid:true` only means executable.
3. Adjust clip gain/fades or separate-track levels within the requested edit.
   Do not fix repeated action by freezing frames or stretch speech to hide a
   missing tail. Separate source cadence from accidental timeline replay.
4. Deliver `render_final`'s measured `audio` and `video_stream` fields along with
   the immutable path/revision. Hard cuts encode each clip once and concatenate
   packets; the soundtrack is assembled in float PCM and encoded to AAC once.
   A mismatched decoded frame count fails delivery instead of declaring success.
5. For sound-only repair use `render_audio_only(project_id)` then
   `audio_replace(video_path, audio_path, dest)` with a new absolute destination.
   The latter copies video packets, pads short audio with silence and trims long
   audio to video coverage. Existing files are never overwritten.

When claiming a fix, report measurements and sampled scope. These regressions
do not establish the cause of every third-party file's gain or motion defects.
