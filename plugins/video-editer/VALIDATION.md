# Validation

## 0.6.2 single-render commerce flow — 2026-10-20

Full discovery passed 190 tests on Windows/Python 3.12 with FFmpeg 7.1;
`test_commerce_workflow.py` also passed 25/25 in a focused run. The new cases
advance a completed presentation directly to `stage=render`, submit the default
render with `preview=false`, and verify the resulting job is not marked as a
preview. The old preview-then-final gate is no longer exercised as a required
route.

The product flow now renders once at the project canvas size, then binds
delivery inspection, listening, audio cue review and delivery review to that
same artifact. `preview=true` remains available only as a legacy/review-copy
branch and cannot satisfy the normal final-delivery route by itself. Skill
steps, reference documents, the workflow plan, README, MCP descriptions and
plugin metadata now all describe this single-artifact flow at version 0.6.2.

This was a code/documentation change; no customer project was edited and no new
render was submitted. The existing r155 artifact remains a 540x960 review copy,
not a 1080x1920 delivery. Passing tests and render completion still do not
replace continuous viewing, listening, or the `commerce_delivery_get` decision.

## 0.6.1 source selection repair — 2026-10-18

82 targeted regressions passed: speech (18), speech clock (1), commerce (25),
delivery review (20), shared resources/queue (18). Both skill structure validators
passed. The commerce cases reject tiny labels without changing the project,
accept an explicit two-line keyword layout, and reject later unreadable scaling.

A read-only replay of 40 saved ASR decoder outputs from the reported local run
now validates every timestamp structure while preserving all nonblank word text.
All 14 formerly failed outputs contained recoverable touching zero-duration
tokens; their components and low confidence are retained, with listening
warnings. This parsing replay is not a fresh inference or a listening approval.
Malformed/isolated timestamps still fail, and decimals cannot be split into
independent sentence selections, including in a legacy transcript.

A fresh MCP stdio run also transcribed one of those previously failing 38-second
original sources: 10 sentence proposals, 191 timed groups, no timestamp errors.
It verified 110 tools, shared heavy concurrency 1, source selection and word-cut
rejection, job deduplication, and an unchanged timeline. Actual ASR device was
bounded CPU int8 because CUDA was unavailable; no customer edit/render or
candidate preview was submitted. Two lettering assets were visually inspected
in an isolated synthetic fixture, not approved as a finished customer video.

The reported video remains a 540x960 review copy, not an approved 1080x1920
delivery. Source-caption/voice conflicts, a weak opening quote, outfit changes
and insufficient detail emphasis are now explicit selection/repair examples in
the skills. These are caller judgements; the plugin does not certify audiovisual
meaning. No Cardbush host runtime or customer project state was modified.

## 0.6.0 local ASR prerequisite — 2026-10-18

102 targeted regressions passed: speech (14), speech clock (1), commerce (23),
delivery review (20), shared resources/queue (18), and long media (26).
Two additional isolated standalone checks passed for the declared import
boundary and FFmpeg stdin handling (104 checks total for this update).
The speech-clock case uses only short synthetic audio: delayed audio, nonzero
container origin, trimmed windows and padding stay aligned. No customer edit is
created by these tests. Both bundled skill structure validators passed.

Real MCP stdio transcription was tested on an existing authorized source in an
isolated project. It returned 19 sentence proposals and 162 timed ASR words.
Selection before transcription and a truncated first-word range were rejected;
duplicate submission reused its job, recovery returned speech_candidates_get,
and the original timeline stayed unchanged. No candidate video preview, edit,
video generation or customer render was submitted. This is speech evidence,
not a listening, sentence-meaning or creative quality approval.

The initial run found a PyAV API incompatibility during second-pass decoding.
The worker now consumes the already normalized PCM samples directly. The failed
job remains recorded and its successful retry used media_job_retry and the same
heavy queue. Actual device on this host was CPU int8, four bounded threads:
CUDA ASR was unavailable and the existing policy allowed CPU fallback. CUDA
preference and strict refusal of forbidden CPU fallback are unit-tested, not a
claim of an actual CUDA inference on this host. GPU video encoding is separate.

The new gate covers missing/corrupt/cross-source transcripts, nonconsecutive IDs,
first/last-word cuts, adjacent unselected speech, no-speech display-only records,
unknown meaning, low-confidence words, compact polling, legacy missing evidence,
interrupted/failed retry routing and partial transcript coverage. It does not
detect a model lying about listening or prove ASR word estimates acoustically
exact. Default model files are prepared locally and not shipped in the ZIP.

## 0.5.1 real preview run and pending-review distinction — 2026-10-18

43 targeted regressions pass: commerce workflow (23) and delivery review (20). New cases cover pending-only preview admission, blocked final delivery, known-defect/silent/rejected candidate exclusion, nonwaivable pending facts, pending status never hiding repair/stale evidence, wrong audio/image import rejected before queue submission, and compact main/support glyph spacing. Both bundled skills pass the skill validator. The 101-test 0.5.0 baseline remains separately recorded below; this patch did not rerun every historical suite.

An authorized 42.56-second source-only review was rendered through actual MCP tools from an isolated project using existing customer footage. It uses a 3-second full-body display, independent original voice, five body units, three theme colors, seven designed effects and one decorative transition. Successful encoder events report h264_qsv; heavy concurrency stayed 1. The first output failed typography review and was revised through tools instead of relabelling the old failure. No video generation, speech synthesis or paid provider was invoked.

This host explicitly lacks audio-input listening capability. Candidate unknowns stay pending; the output is an unapproved review copy, not an accepted creative sample. Decoded sound presence, cue comparisons, actual PTS images and software tests do not certify naturalness, hook emotion, continuity or final listening. Private source IDs, footage and run artifacts are retained locally, not included in the bundle.


## 0.5.0 staged source-only workflow — 2026-10-18

Validation uses isolated fixtures and small synthetic audio, not customer footage. No new customer video or paid generation was submitted.

101 targeted tests pass: commerce workflow (18), delivery review (18), generated hooks (9), resource queue (18), audio review (10), binding precision (2), long media (26). The resource suite deliberately logs a mocked dispatcher startup failure; its recovery assertion passes.

Targeted suites cover commerce stage persistence/recovery, talking/cropped hook facts, silent-source rejection, independent source voice, duplicate body windows, atomic timeline construction, original speed and explicit voice timing, shared queue admission, raw-render stage gates, final-after-preview gate, transparent centered lettering, reserved areas, caption exclusivity, per-video palette consistency, source/evidence tampering, inspection without timeline mutation, mandatory-check applicability, unrelated JSON evidence, missing bound inspection, output/subject-aware repair, never-prefilled approval, GPU fallback/resource isolation, audio comparisons and existing generated-hook behavior.

The staged inspector creates continuous previews and machine evidence. It does not implement a vision or speech-understanding model. A caller must actually watch/listen; model observations are explicitly distinguished from measurements. An incapable reviewer must leave checks pending. These tests do not certify editorial quality or benchmark a particular smaller model.

Bundled reference media is copied from pre-existing examples; the accepted hook demonstrates display plus independent source voice, not blanket compliance with later typography changes. Existing user projects are not enrolled or changed by the upgrade. Fresh installations preserve host MCP configuration.

## 0.4.0 evidence-bound review — 2026-10-18

61 targeted regressions pass: `test_delivery_review.py` (7),
`test_long_media.py` (26), `test_resources_queue.py` (18), and
`test_audio_review.py` (10). The dispatcher-error traceback in the resource
suite is an intentional failure fixture, not a production startup failure.

New cases cover missing checks, evidence-required judgements, failed-check
retention until a new rendered revision, output/evidence/revision invalidation,
unreviewed new renders, preview-only approval, individual warning resolution,
exact audio/render identity, missing cue signals, and explicit listening
versus measurements. PTS cases reject mislabeled first/key frames while allowing
the following decoded frame and low-frame-rate sources.

These are synthetic software tests, not creative approval of a new customer
video. Existing Kexin source frames were inspected; the user then chose to run
the new edit in Cardbush. No new Kexin render or generation was submitted here.
Accepted examples are user-approved typography crops only, explicitly separate
from whole-video approval, motion review and listening.

## 0.3.9 cue review and commerce rules — 2026-10-18

49 targeted regressions pass: all tests in `test_audio_review.py` (10),
`test_resources_queue.py` (18), `test_audio_track_clock.py` (1),
`test_binding_precision.py` (2), `test_execution.py` (14), plus four delivery
audio cases covering gain/mute/fades, audio joins, sped-up voice tails and MCP
schemas. Synthetic fixtures use an isolated resource directory and bounded CPU;
the production resource policy and existing customer video projects are unchanged.

New checks demonstrate immediate cached reads without a probe/heavy slot,
explicit guarded refresh, in-place audio updates retaining IDs/anchors/undo,
quiet effects despite audible background sound, independently overlapping cues,
muted cues, common-gain A/B with normalization/limiting, saved-revision review
after live edits, missing delivered effects, AAC comparison, deferred source
hashing, changed-source rejection and queued retry from the saved request.
The existing suite also checks MCP stdio, single-heavy-task resource locking,
portable GPU selection/fallback, and unchanged editing/render behavior.

Both bundled skill entrypoints and the synchronized local orchestration skill
pass the skill validator. All 12 orchestration files match the local copy;
103 relative references across those three skill trees resolve. Planning JSON
parses and plugin/package versions agree at 0.3.9. The bundle builder now
includes source-audio, typography-layout and typography-sound references and
rejects skill links to unbundled local files.

These are software and documentation checks using synthetic media. No paid
generation or new Autumn video was produced. Reports deliberately leave
`human_listened=false`; signal measurements do not establish creative quality,
final audibility or completion of the pending real-video sound repair. The
current commerce layout, pacing and minimum-one-transition rules remain caller
planning/review responsibilities, not automatic restrictions on every video.

## 0.3.8 Fast generation default — 2026-10-17

The model-free editing engine is unchanged. The commerce skills and plan now
explicitly select Seedance 2.0 Fast, 720p, portrait audio generation and a
four-second starting duration, while omitting the two 2.5-only request fields.
The connected generation service reports the same Fast default. An offline
preview adapted from the Autumn reference request preserves all three ordered
reference roles and native audio. The historical request remains unchanged.

The generation plugin's video, reference-skill, configuration and real stdio
checks pass: 111 tests. These include default-model routing with image/video/
audio references, Fast parameter rejection and preservation of the explicit
2.5 alias. No paid generation, remote-media access check or new video render was
performed; the offline preview does not establish provider acceptance or quality.

## 0.3.7 specific hooks and source payoff — 2026-10-17

Both bundled skills and the synchronized local orchestration copy pass
`skill-creator/scripts/quick_validate.py` with Python UTF-8 mode. All nine
orchestration files match locally; 18 relative links within that skill resolve.
The planning JSON parses, package versions agree, and paid generation remains
disabled in the template. `git diff --check` reports no whitespace errors.

This revision records the user's rejection of the Autumn texture-praise hook
and adds a concrete attention reason plus a source-payoff record. It changes
instructions, the plan and release metadata only. No execution code changed,
no engine suite was rerun, and no replacement video was generated or reviewed.
Schema and link checks do not establish the creative quality of a new hook.

## 0.3.6 emphatic hook options — 2026-10-17

Both bundled skills and the local orchestration copy pass the skill validator.
All nine orchestration files match locally; 28 relative skill links resolve.
The plan parses with two hook modes and seven optional angles, mode-specific
copy/speaker fields, separate emphasis and SFX records, and no paid task enabled.
Version/UI metadata and the existing speed/resource/transition defaults check
consistently. The standalone ZIP passes CRC and 72 content checksum checks and
includes the new hook-options reference.

This release changes skills, the planning template and package metadata only;
the execution engine is unchanged. No video was generated or rendered, no new
voice/SFX performance was auditioned, and the engine suite was not rerun.
The naturalness of stretched emphasis and the proposed two-note cue must still
be reviewed on actual output; the examples are planning guidance, not a passed
audiovisual production test or substantiation of customer/commerce claims.

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

## 0.7.0 — batch, incremental rendering and review evidence (2026-10-20)

- Preserved installed 0.6.2 target-quality export changes; decoupled preview quality from pending candidate review. The same output is inspected and delivered.
- Added batch ASR, batch candidates, bounded compact waiting, cross-project source analysis reuse, dependency-based render-stage reuse and queued transcode/interpolation. Existing resource/encoder policy and one-heavy-task gate remain in force.
- New synthetic regressions verify actual audio-only stage reuse, subtitle-only reuse, corrupt-cache misses, positive/silent ASR cache rebinding and model invalidation, source-internal flash cuts, derived-media sound retention, receipt coverage and missing-evidence pending status. No customer media, paid generation or real recognition inference was run.
- Full suite: 201 cases; two outdated/single-source resume expectations exposed the route change. Corrected initial batch arguments and preserved partial-transcript resume from its uncovered boundary. Post-fix focused suite: 77 cases passed, including the added coverage. Other full-suite cases passed.
- Synthetic 3-second output measurements are local smoke timings, not an end-to-end 45-second workflow benchmark. Cache assertions check reused stage hashes and changed audio, not a fragile time threshold.
- Both Skill entrypoints pass quick_validate in UTF-8 mode; MCP discovery exposes 114 tools and preview defaults false. Actual semantic video/listening quality still requires received review evidence or explicit user feedback; portable receipts are not authenticated host attestations.

## 2026-10-24 原片 ASR 时间线

新增 speech_timeline_get 的 5 项回归与现有 speech 的 18 项回归通过（共23项）：限定实际转写窗口、重叠字词合并、句内停顿、分页、证据损坏拒绝和非法参数。仅输出选材提示，不把空档认证为无声或自然全身展示。同仓库百应0.3.0独立测试95项通过，覆盖一段原片默认、关联切片不排除原片、来源策略恢复/收紧、空链接后续补齐与诊断。安装包已检查Skill相对引用及原始PNG完整性。

## 2026-10-24 显式构图选择与单原片试剪

- 新增项目级 `commerce_hook_framing_set`，仅在用户实际指示下将完整全身改为服装展示。默认仍要求完整全身；仅替换构图事实，同人同衣、无持续讲话、字幕相容、独立有效原声及实际审阅要求全部保留。已有钩子审阅/组片后不可改标签放行，旧版无构图标记的签名保持兼容。
- 57 项相关回归通过：构图 6 项、交付审阅 21 项、商业阶段流程 30 项。覆盖空指示拒绝、已拒绝候选锁定、默认全身保持、选择/输出绑定、缺真实审阅仍待验，以及构图例外不能豁免持续讲话检查。
- 实际用一段 600.03 秒商品原回放完成 ASR（275 个完整句候选）、原声及正文选区、45.48 秒纯剪辑审阅片。开头 3.2 秒服装展示是本次用户明确许可，未扩展为默认规则；原速，四个正文单元，一处侧推，三色透明花字、六处音效。
- 实测基于已安装的 0.7.2 高画质运行分支加本次原片/构图补丁：1080×1920、25fps、1137 帧；最终编码 h264_qsv，导出约 75.3 秒，重任务并发 1。源 ASR 在无 CUDA 环境使用 CPU，主窗口约 254.5 秒；这些是本次机器的实测值，不是跨设备性能承诺。
- 源正文和成片各段均有音频信号，实际输出 -17.24 LUFS、true peak -0.76 dBFS，六处音效均 `signal_presence=confirmed`。检查未发现源内短闪切或花字元数据/字号错误。已查看实际 PTS 抽帧，未连续观看/实际试听，语意、自然度及听感仍待验，`delivery_ready=false`；未伪造感知收据。
- Skill 补充“画外主持说话时，画中模特仍可能无话展示”，无 ASR 空档不能直接否定展示；移除通用编辑入口残留的强制低清预览再正式导出步骤。
