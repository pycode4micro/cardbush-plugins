# Delivery validation — 2026-09-15

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
