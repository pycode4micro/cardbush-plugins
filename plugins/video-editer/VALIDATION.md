# Validation

## 0.3.5 editorial defaults and audio clock — 2026-10-17

The execution and Baiying orchestration skills were validated with
`skill-creator/scripts/quick_validate.py`, including the synchronized local
orchestration copy. The shared rules and planning JSON cover the retained
2–4 second dialogue hook, original speed, roughly 45-second source-based body,
feature/detail coverage, typography roles and sparse decorative transitions.
These are scoped caller defaults and do not add automatic editing or tool APIs.

All eight orchestration files match the local copy byte for byte; 20 relative
skill links resolve. Version and planning invariants pass. The allowlisted
standalone ZIP includes both skills and all their references; its CRC and
71 content checksums pass. This is a packaging check, not a repeated clean-room
installation or full offline renderer test.

Two targeted real-audio regressions passed on Windows/Python 3.12:

- `test_audio_track_clock.AudioTrackClockTests.test_mixed_rates_keep_speech_windows_and_tail`:
  32/44.1 kHz sources retain 0.5/1.8 second placement on the 48 kHz mix clock;
  tone starts and ends are within 2 ms, and no unwanted late tail remains.
- `test_delivery_audio.DeliveryAudioTests.test_explicit_sped_up_audio_keeps_tail_pitch_and_master_settings`:
  explicit 1.25x sound retains the late tone, 431 Hz pitch and master gain.

This release does not rerun the entire 0.3.4 suite or repeat paid generation.
The prior real commerce delivery, its frame/audio checks and review limitations
are recorded in the [tested case](skills/baiying-dialogue-video/references/tested-case.md).

## 0.3.4 integration — 2026-10-17

The merged release was checked on Windows with Python 3.12 and FFmpeg 7.1.
Full discovery ran 115 tests: 112 passed initially, and the remaining three
passed after focused repairs and reruns. The repairs retained corrupt-video
diagnostics, allowed the newly declared psutil dependency in the standalone
import check, and made a byte-only hook fixture request structural validation
instead of attempting to decode its placeholder audio.

Coverage includes:

- Shared media/render FIFO and asynchronous retry using saved revisions;
  changed-source rejection, cancellation, reconnection and checkpoint recovery.
- Cross-process single-heavy-task locking across data directories; CPU budgets,
  memory admission/cancellation, worker memory limits and bounded GPU fallback.
  Vendor-selection cases use synthetic probes/mocks; they do not certify every
  vendor's hardware on this machine.
- Hook account boundaries, reference provenance, one external submission claim,
  unknown-task recovery, review-before-insertion and shifting body events once.
  Generation state fixtures make no paid model calls.
- The existing 57-cut loudness/frame-coverage regression, PCM sound assembly,
  source gain/fades, sound-only repair and cadence detection remain covered.
- A new real-audio regression checks a 1.25x independent hook track: its late
  speech-tone window remains audible, its 431 Hz pitch is preserved, and source
  ducking retains the previously configured master gain.

An actual MCP stdio handshake exposed 87 tools. All 74 previous tool names and
their input parameter names remain present; `render_retry` intentionally now
returns a queued attempt. The plugin and workflow skill frontmatter validate.
No user projects or real generation runs were used as fixtures.

The allowlisted standalone ZIP was relocated to a path containing spaces,
installed in a clean virtual environment, and exercised through real MCP stdio
with external networking blocked in the server. All 45 invoked tool types
completed their expected checks. H.264 QSV passed the real probe and full
synthetic renders with CPU fallback forbidden. Heavy concurrency remained 1.
An injected failed render retried through the queue in 0.093 seconds of submit
time, waited behind a media import, and succeeded afterward. Reference previews,
indexed frame evidence and candidate previews also completed. The verified
bundle's executable sources match this release; subsequent edits only updated
this validation record. Other GPU vendors still require runtime verification
on their respective hosts.

## Delivery validation — 2026-09-15

Run from the plugin directory with its dependencies installed:

```console
python -m unittest discover -s tests -v
```

85 tests passed on Windows with Python 3.14 and the bundled FFmpeg 7.1.
Fixtures are synthetic and retained in temporary review directories; no user
media, generated assets, credentials or paid API calls enter the tests.

The added delivery tests cover:

- 57 cuts from a 24 fps moving source into a 25 fps timeline, including 56
  fractional half-second selections and a three-second moving final shot.
  The output contains exactly 803 decoded frames (32.12 seconds); the last
  two seconds still contain changing frames. Source/output integrated
  loudness differs by less than 0.5 LUFS with gain 0 and fades disabled.
- Per-clip -6 dB produces the measured -6 dB RMS change; mute produces silent
  null LUFS; a +24 dB master with the explicit limiter retains output headroom.
- Opposite DC source samples trigger a join discontinuity warning. 25 ms fades
  suppress it; a 20 dB adjacent level difference is reported separately.
- Byte-level video-packet hashes remain identical after `audio_replace`.
- An 8 fps moving source repeated inside 24 fps has a near-duplicate fraction
  above 55% and about 8 updates/second. A one-second video with three seconds
  of audio is reported as insufficient video coverage.
- Frame policies, custom role/shot updates, source overlap warnings, content
  deduplication and the actual MCP input schema are checked.

The complete suite also covers real transitions, image/overlay/caption
rendering, source-time anchors, cancellation and checkpoint recovery. The
strict new frame check exposed an existing FFmpeg 7.1 failure: encoding a
25 fps filter result without an explicit output `-r 25` could leave a final
MP4 packet without usable duration, losing a decoded frame on subsequent
passes. Output frame rate is now explicit at every rendering stage.

Limits: these tests do not prove the exact cause of the reported +13 dB gain
in the user's original film; that source project was not used as a fixture.
The new audio path avoids repeated lossy encoding and reports every control
and final measured LUFS/true peak. PCM prediction and final AAC peaks can
differ. Cadence analysis is a luma sample, not a guarantee about animation or
speech quality. No automatic motion interpolation is performed.
