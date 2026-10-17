# Generated opening hooks

This is an optional host-orchestrated workflow. The editor stores plans,
provenance, task state and explicit timeline edits; generation stays in the
connected video provider. It does not infer a good hook, recognize people, or
silently make paid API calls. Use existing user authorization for the requested
generation, upload and edit; do not ask again merely because a new step starts.

For this live-commerce style, inherit the
[shared editorial defaults](../baiying-dialogue-video/references/editorial.md).
Use a suitable existing opening when available. Generation fills a missing hook;
real source footage demonstrates the product claims. Preserve the accepted body
when the request only changes the hook, typography or decorative transitions.

## Account and material selection

1. Resolve the requested presenter/account and product. Reuse a confirmed local
   identity registry when available; never copy another person's asset ID.
   A shop account can have multiple presenters. Account names alone do not prove
   who appears in a slice. If the user changes accounts, change BOTH the source
   footage and the generation identity. Preserve earlier edits/projects.
2. Collect/import the selected account's footage with the collection plugin and
   queued `media_register`. Inspect actual footage, speech and product. An API
   product title may disagree with the picture; don't turn it into a claim.
   Keep shortages/source exhaustion visible. Do not mix accounts/products to
   satisfy a count. Request missing scope only when it affects the result.
3. `hook_account_put(account_id, expected_version, profile)` stores versioned
   reusable settings outside the plugin. Required fields: `display_name`,
   `identity_mode` (`authorized_person`, `synthetic`, `product_only`),
   `identity_description`, `voice_policy`. Optional `style`, `asset_group_id`,
   `reference_assets` entries: `uri=asset://...`, `kind=image/video/audio`,
   `status`, `verification_reference`. `confirmed` means the user confirmed the
   mapping, NOT that current provider access was checked. `Active` requires a
   provider status receipt. Pending/revoked/expired assets cannot be selected.
   No names, IDs, paths, credentials or GPUs are hardcoded in the package.

## Plan before generation

Compare 2–3 opening ideas against actual evidence. Prefer an immediate visual
question, a demonstrated contrast or the most revealing detail. Make the first
0.5s legible and connect the promise to a real demonstration in the body. Avoid
invented prices, material specifications, physical transformations or benefits.

Use `hook_plan_create` to pin the account version, product description, exact
source ranges, their hashes, a complete native prompt and the planned retained
duration. Plan fields: `product_key`, `product_description`, `opening_idea`,
`seam_description`, `prompt`, `keep_seconds`,
`source_references=[{asset_id,start,end,purpose}]`.

For the dialogue-commerce profile, retain 2–4 seconds: one short question and
one direct reply, then immediately show the real demonstration. Request a
provider-supported duration with trim room; generated duration and retained
duration are separate. Shorten the copy before generation rather than speeding
up delivery or truncating a final syllable to fit. Honor explicit user choices.
Plan the whole delivered runtime separately from the hook. A short hook does
not justify cutting an accepted body down to a technical demo. Preserve the
complete explanation and action payoff; if the user asks for a longer cut,
add useful source content instead of padding, freezes or repeated footage.
Plan a compatible final pose, crop, camera direction and light for the seam.
Put sales text in editable overlays after generation; don't depend on generated
Chinese typography. Do not add an unsolicited decorative "AI-generated opening"
title; preserve existing or platform-required marks. Use original speech where
possible; generating new speech requires its explicit script and the selected
presenter's voice reference.

Record an explicit audio plan: original voiceover, generated speech, or deliberate
silence. For requested spoken generation, set `generate_audio=true`, bind this
account's voice reference and give the complete script plus natural delivery
time. A silent reference export does not imply a silent output. Do not disable
sound just to simplify insertion. If an existing body uses `source_volume=0`,
mix the generated voice back through an explicit audio event or restore source
gain as appropriate; `audio_policy="inherit"` alone will inherit the mute too.

For a dialogue hook, write explicit turns: offscreen question, presenter reply,
then the source demonstration. Name each speaker, who is visible, exact wording,
voice mapping and delivery (surprise, emphasis, rising question, playful reply).
Only the visible current speaker moves their mouth; the presenter listens during
the offscreen question. Bind the account voice to the presenter alone and use a
distinct synthetic voice for an unspecified offscreen colleague. Prefer short,
conversational lines; heightened emotion should not become robotic syllables or
shouting. State desired pitch/energy in generation, not through accidental audio
resampling. Review turn-taking and the speaker distinction, not just transcript text.

Keep both the hook and body at original speed (1.0x) by default. When the user
requests 1.25x, generate at natural speed and perform exactly one
local synchronized retime. `keep_seconds` and the output review use ORIGINAL
source seconds. Pass `playback_speed=1.25` to `hook_attach`; its ripple then uses
the actual accelerated 25fps duration (7 source seconds become 5.6 output seconds).
Explicit generated audio must use `audio_track_add(playback_speed=1.25)` too:
`source_start` is in source seconds, while `duration` and fades are in output
seconds. The executor uses pitch-preserving `atempo`; it does not raise pitch.
Keep original-body audio at 1.0x, place graphics from the accelerated time map,
and preserve speech tails. If an existing clip's speed changes later, explicitly
update/recreate separate audio events too; binding their placement alone does
not synchronize their audio tempo. Check the mixed accelerated output again.

## Real video reference, not prompt-only imitation

Read the connected reference-production/stylizer skills and provider capabilities
first. Current Seedance 2.x raw real-face reference images/videos use authorized
provider assets. Two routes are supported:

- Authorized raw reference slice: ingest the exact slice in this person's
  provider asset group and obtain `Active` plus `asset://...`.
- Prepared visual reference: a suitable local plugin removes the original head
  identity while preserving garment, hands, motion and background. Review every
  shot, turns and reported gaps; coverage numbers alone aren't verification.
  Use that processed video plus the selected presenter's authorized image asset.
  This isn't a guarantee of platform acceptance; respect provider rejections.

Export each required slice through queued
`media_prepare(operation="reference", options={start,end,max_edge,include_audio})`.
This is a higher-quality 25fps export; it does NOT anonymize or upload. Defaults:
`include_audio=false`, even `max_edge=640`. Choose dimensions that satisfy the
current generation provider's area/fps limits (640 can be too small). Use 1280
or 1920 when appropriate. Reference retries use `media_job_retry`, never a new
synchronous FFmpeg process.

For external preprocessing, run one exact job, retain its receipt, wait for its
terminal result and import its output via the editor queue. The external
plugin must share the editor heavy-slot guard, or execute only while the editor
is idle with no other heavy task admitted. Never imply an unrelated plugin is
automatically covered by this editor's queue. GPU in the stylizer can accelerate
mesh rendering while its inference/encoding still uses CPU. Enforce the existing
single-heavy-task policy throughout; don't reserve local GPU while cloud
generation is merely queued/running.

Upload only the reviewed slice(s). Keep private signed URLs outside distributed
skills and public logs. The exact native request must contain a `video_url`
with `role=reference_video`; images or the phrase “use a reference” alone don't
qualify. Verify local metadata and perform the provider's free preview.

`hook_request_prepare` binds every request media index to provenance:

- `source="account"`: matches a confirmed/Active profile asset and media kind.
- `source="slice"`: `source_index`, `evidence_id`, `uploaded_sha256` matching the
  queued export. Raw real Seedance video additionally requires
  `provider_asset={uri,status:"Active",account_id,asset_group_id,verification_reference}`.
- For a processed slice, also set
  `preprocessing={source_evidence_id,output_asset_id,coverage:"head",job_receipt,
  all_required_heads_covered:true,garment_preserved:true,review_evidence_refs:[...]}`.
  `uploaded_sha256` must match the imported processed output. Its duration must
  match the source window. A matching account image asset must be in the request.
  The review booleans are HOST assertions supported by saved observations,
  never automatically filled from a success status.

## Durable asynchronous generation

1. Prepare the exact provider-native request and keep its private copy. The
   plugin records a fingerprint, redacted preview and provenance only.
2. `hook_generation_begin` records the existing user authorization and durably
   claims a submission. Only `may_submit=true` permits ONE external create call.
   Repeated calls return false even after reconnect or an unknown outcome.
3. Persist the returned original task ID with `hook_generation_record`, then
   query that same ID. A failed poll is not a new generation. On a create timeout,
   record `unknown` and reconcile history; do not retry the paid create call.
   A terminal paid failure needs explicit authorization for another paid attempt
   and a new plan. Local media/render retry remains in the shared queue.
4. A submitted/succeeded task is not yet a reviewed local video. Download the
   provider's exact successful URL through its download tool, or a normal
   downloader if unavailable. Record a further succeeded receipt with
   `output_sha256` from the download; review only accepts that file's hash.
   Keep its task ID/hash. Queue `media_register` and
   use only its succeeded result. Download retries do not call generation again.

## Review and attach

Review actual output against the selected source: person, hairstyle, garment
color/structure/pattern, hands/motion, truthful claims, audio and seam. Save
specific frames or AV evidence. Record `hook_output_review` for the retained
range with `checks={person,garment,motion,seam,claims,audio}` values
`pass/fail/not_applicable`, `note` and `evidence_refs`. Face/garment acceptance
is a host judgement, not a tool capability. Failed checks block attachment.
For a spoken hook, inspect audible levels and speech content, using listening
or a transcript with its uncertainty stated. An audio stream alone cannot
prove the line is audible or complete. Check the hook again in the mixed final
video: verify opening silence, voice handoff, tail words and whole runtime.
Do not clip off speech merely to meet an earlier estimated hook length.

Read the latest timeline revision, then `hook_attach(..., expected_revision,
audio_policy="silent"|"inherit")`. It atomically prepends the accepted hook,
hard-cuts to the original body and shifts ALL absolute text/graphics/animation
keys/point cards/audio by the actual 25fps duration. Source-bound events follow
their clips exactly once. Existing body clip choices and audio source offsets
stay intact. It returns an undo snapshot and refuses stale revisions, locked
tracks, changed media or duplicate insertion. Convert legacy continuous beds
to timed audio first. `silent` requires an audio-free hook or globally muted
source; `inherit` explicitly uses the existing source gain.

Add hook-specific editable typography/SFX after attachment. Validate, queue a
preview, inspect opening/seam/body sync, then queue final render. Use GPU-first
portable policy with heavy concurrency 1, never assume NVIDIA. Report the
actual encoder, output path, generation task, source account/product, tested
limits and whether identity/garment review passed. Do not claim mocked state
tests or free previews are a completed real generation.
