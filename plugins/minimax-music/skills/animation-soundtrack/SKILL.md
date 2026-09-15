---
name: animation-soundtrack
description: Create animation opening and ending themes and scene BGM with MiniMax. Use for an animation soundtrack, character themes, scene cue planning, or a coordinated OP/ED and background music package.
---

# Animation soundtrack

Read the sibling [Music Studio](../music-studio/SKILL.md) for API execution, configuration, job recovery and delivery. Use the available MiniMax tools or that skill's bundled CLI fallback.

## Build from the animation

Derive the musical brief from the user's story, genre, audience, emotional arc and existing cuts. If the request is underspecified, ask only what changes the output (for example story/style and lyric language). Proceed with clearly labeled assumptions for ordinary choices. Respect an already agreed brief.

For a soundtrack package, maintain a cue sheet in the user's outputs directory: cue name, scene, purpose, desired duration, vocal/instrumental, style, instruments, entry/exit, dialogue density, job ID and delivered asset path. This sheet tracks intended timing; it is not measured synchronization. Generate only the user-authorized set/count of cues.

## Theme songs

Create or use structured lyrics for OP/ED, keeping story specificity and a singable hook. Include vocal delivery and arrangement development in the prompt. Suggest a compact opening edit or a full song according to the user's format. Cloud duration is a preference, not an API setting. If the user requests multiple candidates, give each an explicit variation and unique stable request ID; record the selected version.

## Scene BGM

Use `generate_instrumental`. Describe the scene function and emotional progression, not only a mood word. For dialogue scenes, request sparse orchestration, restrained melody and room for speech. For action, identify the energy arc and a clear edit point. For loops, request a restrained ending and test the join later; do not label an untested clip seamless.

Starting points to adapt, not mandatory extra generations:

- Daily life: warm piano or plucked strings, light pulse, modest dynamics.
- Mystery: restrained low texture, sparse motifs, gradual tension.
- Action: clear rhythmic drive, staged build, a defined resolution.
- Loss/reconciliation: exposed melodic instrument, slow harmonic movement, space.

## Continuity and delivery

Maintain a shared instrumentation/palette across cues. Reusing a text prompt does not guarantee the same melody. When the user requests a recurring character melody, use an authorized reference and evaluate the available cover workflow, or explain the need for arrangement/editing. Do not represent a style-related generation as a verified melodic variation.

Keep requested music, generated music and final edited music distinct in the cue sheet. Deliver actual saved audio, lyrics, generation parameters and the sheet. Listen against the animation when inspection is available; assess lyric intelligibility, hook, dialogue masking and musical transitions. When precise timing, loop editing or mixing is requested, use available editing tools and verify the finished file rather than claiming the generator achieved exact timing. No mandatory publishing or additional paid generation steps.
