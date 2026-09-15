# MiniMax music API mapping

Verified 2026-09-15. Authoritative references:

- [Music Generation](https://platform.minimax.cn/docs/api-reference/music-generation)
- [Lyrics Generation](https://platform.minimax.cn/docs/api-reference/lyrics-generation)
- [Music Cover Preprocess](https://platform.minimax.cn/docs/api-reference/music-cover-preprocess)
- [Music 3 model and self-hosted API](https://huggingface.co/MiniMaxAI/MiniMax-Music3)

## Cloud

Bearer API key. Region `cn` uses `https://api.minimax.cn`; `global` uses `https://api.minimax.io`. Select the region matching the key/account. The plugin does not send credentials to arbitrary custom cloud endpoints.

| Endpoint | Exposed controls |
|---|---|
| `/v1/music_generation` | `music-3.0`, `music-2.6`; prompt, lyrics, auto lyrics, instrumental, streaming, hex/URL transport, WAV/MP3/PCM, sample rate, bitrate, non-streaming end watermark |
| `/v1/lyrics_generation` | `write_full_song`, `edit`; prompt, existing lyrics, fixed title; returns song_title, style_tags, lyrics |
| `/v1/music_cover_preprocess` | `music-cover`; URL or Base64 reference; returns editable lyrics, feature ID and song-section timestamps |
| `/v1/music_generation` with `music-cover` | One-step reference restyling; optional changed lyrics; two-step generation from feature ID plus changed lyrics |

Original music prompt: up to 2000 characters; lyrics up to 3500. Instrumental requires a prompt; lyrics are omitted. With `lyrics_optimizer=true` and empty lyrics, MiniMax writes lyrics from the prompt. Lyrics API prompt max 2000, existing lyrics max 3500; edit requires actual source lyrics in this wrapper.

Cover prompt 10–300 characters; supplied cover lyrics 10–1000. Exactly one of `audio_path`, `audio_url`, `audio_base64`, or `cover_feature_id`. The latter is available only for generation and requires lyrics. Local files are converted to Base64 without a public upload. Reference audio: 6 seconds to 6 minutes, at most 50 MB; MiniMax validates duration/decodability. The plugin validates local file size before sending. Feature IDs last approximately 24 hours. `structure_result` supplies section timestamps in seconds, not word-level synchronized lyrics.

Sampling rates: 16000, 24000, 32000, 44100. Bitrates: 32000, 64000, 128000, 256000. Default plugin output: WAV / 44100 Hz / 256000. PCM is raw audio: retain `result.json` for interpretation; the plugin does not invent a bit depth when the provider omits it.

Streaming uses SSE with hex audio and completion status `2`. The plugin saves the assembled audio and byte progress; it does not claim realtime audio playback in the Codex UI. `aigc_watermark` works only without streaming. URL transport triggers an immediate credential-free download; download failure is recoverable without another generation.

On 2026-08-20 MiniMax stopped new-user access to the paid music/lyrics APIs and stopped free music models. Existing paid access may remain. The wrapper omits discontinued `*-free` models even though portions of the parameter documentation still list them. It reports provider access failures; it cannot grant account access. No endpoint in this wrapper is a provider-side asynchronous job query: job IDs and monitoring are local.

## Self-hosted Music 3

Connect to an existing SGLang-Omni server with `/v1/audio/speech`. `backend=local` maps lyrics to `input`, prompt to `instructions`, seed to `seed`, and `duration_seconds × 25` to `max_new_tokens` (max 300 seconds). Output is non-streaming 32 kHz stereo WAV. Instrumental uses `[Instrumental]` and explicit no-vocal instructions. Fine control remains probabilistic.

This adapter supports songs and instrumentals, not the proprietary lyrics or cover endpoints. Prepare lyrics with Codex when needed and identify their origin accurately. Cloud output parameters, optimizer and watermark are rejected on local mode. The plugin connects to the server; it does not install CUDA, download model weights or start GPU infrastructure automatically. Local service API key, if required, uses `MINIMAX_LOCAL_API_KEY`, never the cloud key.
