# Seedream + Seedance + Video Enhancement MCP

## 0.4.1: reference text review

The reference-video Skill now checks small print, captions, watermarks and credits before upload. Preserve or clean them according to the task, using clip-specific coordinates and checking the result. Generation APIs are unchanged.

## 0.4.0: task management and reliable downloads

Use `seedance_list_tasks` for provider history, `seedance_get_tasks` for bounded batch queries, and `seedance_download_task(task_id, dest)` to save the exact signed URL without transcribing its query. Creation always runs free local preflight. Local rejection reports no paid request; ambiguous submission/billing/refunds remain unknown. Missing provider progress/ETA stays null. See [contracts and offline validation](docs/task-delivery.md). Reinstall the Python package and reconnect MCP after updating.

<img src="assets/logo.png" width="96" height="96" alt="Seedream MCP logo">

[中文](README.md) · English user guide

Generate images with Seedream, generate videos with Seedance, and upscale videos with MediaKit. Use the Codex / Cardbush plugin format or connect through a standard MCP client.

## Bundled reference-video Skill

[`reference-video-production`](skills/reference-video-production/SKILL.md) orchestrates media analysis → masking human reference images / preprocessing human reference videos → model-aware FFmpeg cuts → COS upload and URLs → Seedance video-reference generation → delivery.

Describe your task in a Skill-capable client or invoke `$reference-video-production`, supplying source paths, product references, character/voice references and caption requirements. For example: “Replace the product and character, preserve the reference rhythm, and generate a complete video with Chinese speech. Check model limits before preprocessing, cutting and uploading.” Claude Code discovers skills in the plugin's root `skills/` directory; use the invocation shown by your client. See the [Claude plugin specification](https://code.claude.com/docs/en/plugins-reference#skills). A standalone MCP connection does not load these instructions; import the plugin/Skill in a compatible client.

Prepare the dependencies required by your task:

- Local media access and executable **FFmpeg / ffprobe**. The agent plans cuts; the upload tool does not cut video.
- A suitable identity-suppression / stylization processor, preferably a dedicated plugin. **No particular preprocessing plugin name is required.** Verify single/multiple-person and face/head/body coverage. Missing capability stops preprocessing, rather than silently uploading raw human references.
- Tencent COS Upload or an equivalent COS uploader, configured through its documented environment/user variables. This package does not embed the uploader or preprocessing models. The current `upload_video` accepts videos only; images/audio use supported asset references or local inputs, not renamed video extensions.
- `ARK_API_KEY` with access to the chosen model. The Skill reads actual per-clip, aggregate-reference, count, output-duration and resolution limits; it does not fix all cuts to 4, 15 or 30 seconds or silently downgrade a model.

Prefer temporary presigned URLs without making the bucket public. Preserve source files and do not delete/rename cloud objects by default. Preprocessing does not guarantee anonymity or provider acceptance. This is an orchestration Skill, not a new one-call MCP tool. External dependencies must be ready, and paid submissions require user authorization.

Reload the plugin and start a new session after updating. Existing MCP tools and logos are preserved; personal media, private asset IDs, runtime job records and preprocessing model weights are not bundled.

## 1. Install and connect

Requires Python 3.11 or newer. Extract the package, open a terminal in the directory containing `pyproject.toml`, and run:

```shell
python -m pip install .
```

Install/import the plugin directory in your client. For a standard MCP connection, add:

```json
{
  "mcpServers": {
    "seedream": {
      "command": "python",
      "args": ["-m", "seedream_mcp"]
    }
  }
}
```

- Codex / Cardbush use the included `.codex-plugin/plugin.json` and `.mcp.json`. Add the market with `codex plugin marketplace add pycode4micro/cardbush-plugins --ref main`, then run `codex plugin add seedream-mcp@cardbush-plugins`.
- Claude and other standard MCP clients can use the configuration above. A `.claude-plugin/plugin.json` is also included.
- `command` must use the Python interpreter used for installation. Use its absolute path if you have multiple interpreters.
- Importing the plugin does not install Python dependencies; the installation command above is still required.
- Do not enable both the plugin connection and a manual connection to the same service, or tools will appear twice.

### Plugin icon

Codex uses `interface.composerIcon`, `logo`, and `logoDark`; the same self-contained dark badge works on light and dark interfaces. MCP initialization (`serverInfo.icons`) and tool listings (`icons`) include an embedded PNG, with no network fetch. Claude Code's plugin manifest currently has no dedicated logo field. Display in Claude and other MCP clients depends on their UI implementation and is not guaranteed in every version. After updating, rerun `python -m pip install .`, reinstall/reload the plugin, and start a new session.

Protocol references: [Claude plugin manifest](https://code.claude.com/docs/en/plugins-reference#plugin-manifest-schema), [MCP icons](https://modelcontextprotocol.io/specification/2025-11-25/basic#icons). This independent plugin icon does not imply an official vendor plugin.

## 2. Configure keys (environment / Windows user variables recommended)

**Use environment variables. For Windows desktop clients, user-level variables are recommended: no administrator access or system-level configuration is required. The plugin does not automatically load `.env` files. Avoid writing keys into the plugin directory or configuration files.**

Configure only the services you need:

| Variable | Purpose | Where to obtain it |
| --- | --- | --- |
| `ARK_API_KEY` | Shared by Seedream images and Seedance videos | API Key management in the [Volcengine Ark console](https://console.volcengine.com/ark/). Use an API Key, not an AK/SK pair. |
| `MEDIAKIT_API_KEY` | Video enhancement only; no automatic Ark fallback | [MediaKit settings](https://console.volcengine.com/imp/ai-mediakit/settings). Use a valid MediaKit API Key or an IAM universal API Key with the required permissions. |

Your account also needs access to the selected service/model and sufficient quota or balance. A configured key does not prove that service access is enabled.

### Windows user variables (recommended)

1. Search Windows for “Edit environment variables for your account,” or open the Environment Variables dialog.
2. Click **New** under **User variables**.
3. Set the name to `ARK_API_KEY` or `MEDIAKIT_API_KEY`, enter the corresponding key as the value, and save.
4. Restart your client or reconnect MCP, then check configuration as described below.

The plugin reads the current Windows user's saved variables on demand, even when an older desktop process did not inherit newly added variables. It does not read other users' variables.

### Temporary testing: process environment

This PowerShell example prompts without displaying the key or embedding its value in command history. It sets the enhancement key; change the variable name to `ARK_API_KEY` for image/video generation.

```powershell
$enteredKey = Read-Host "MEDIAKIT_API_KEY" -AsSecureString
$env:MEDIAKIT_API_KEY = [System.Net.NetworkCredential]::new('', $enteredKey).Password
```

Start the client or MCP server from **the same terminal**. This does not update an already-running desktop app or persist after the terminal closes.

On macOS / Linux, use Bash and run only the parts you need:

```bash
read -r -s -p "ARK_API_KEY: " ARK_API_KEY
printf '\n'
export ARK_API_KEY
read -r -s -p "MEDIAKIT_API_KEY: " MEDIAKIT_API_KEY
printf '\n'
export MEDIAKIT_API_KEY
```

Launch the client from that terminal. Desktop-launched apps may not inherit shell variables; use your client's supported environment injection mechanism. Windows user-variable fallback is unavailable on macOS/Linux.

### Precedence and overrides

**Client process environment (including empty values) → current Windows user variables → defaults.**

- A stale process key takes priority. Remove the client override or restart the client to refresh inheritance.
- An explicit `"ARK_API_KEY": ""` or `"MEDIAKIT_API_KEY": ""` blocks user-variable fallback. Remove empty overrides instead of leaving blank placeholders.
- Windows user variables are read on demand without caching, subject to process-variable precedence.
- Set `ARK_READ_USER_ENV=0` in the client process to disable Windows user-variable fallback for the entire plugin, including MediaKit.
- Never put keys in prompts, tool arguments, chats, screenshots, source code, or shared packages.

## 3. Check configuration

Call any of these free offline tools. They do not generate media:

- `seedream_capabilities`
- `seedance_capabilities`
- `video_enhance_capabilities`

Inspect `configured` and `configuration.variables.ARK_API_KEY` / `MEDIAKIT_API_KEY`:

| Value | Meaning |
| --- | --- |
| `source=windows_user` | Read from the current Windows user's variables |
| `source=process` | Supplied by the client process |
| `configured=false` | No usable nonempty configuration was found |
| `configured=true` | A value was found; provider authentication and paid API access have not necessarily been verified |

You can also run `python scripts/diagnose_config.py` for diagnostics without displaying keys.

## 4. Tools and workflow

| Purpose | Free capabilities / preview | Paid execution | Query |
| --- | --- | --- | --- |
| Images | `seedream_capabilities` / `seedream_preview_request` | `seedream_generate` | Generation returns results directly |
| Videos | `seedance_capabilities` / `seedance_preview_request` | `seedance_create_task` | `seedance_get_task` |
| Enhancement | `video_enhance_capabilities` / `video_enhance_preview_request` | `video_enhance_create_task` | `video_enhance_get_task` |

For local enhancement inputs, `video_enhance_upload` uploads a file and returns a `mediakit://` URI without creating an enhancement task. Any applicable storage/transfer charges follow provider billing.

Check capabilities, preview parameters, then submit once after approval. An accepted asynchronous task is not a finished result. Query the returned task ID; **never repeat create to check progress**. The plugin does not automatically retry timed-out requests or switch models.

### Generate an image

Pass these arguments to `seedream_preview_request`, then to `seedream_generate` after approval:

```json
{
  "request": {
    "prompt": "Create a white-background product photograph using the reference image. Preserve its colors, collar, cuffs and knit pattern.",
    "watermark": false,
    "output_format": "png"
  },
  "local": {
    "reference_images": ["C:/images/product.png"],
    "aspect_ratio": "3:4",
    "resolution": "2K",
    "save_images": true
  }
}
```

Replace the path with an existing absolute path; omit `reference_images` for text-only generation. Do not combine local references with `request.image`. If setting `request.size` directly, omit `local.aspect_ratio` and `local.resolution`.

With saving enabled, base64 images are normally saved under `SEEDREAM_OUTPUT_DIR` and returned as absolute paths. Explicit `response_format="url"` returns links without downloading. See the [image parameter reference (Chinese)](docs/official-parameters.md) for additional options.

### Generate a video

Pass these arguments to `seedance_preview_request`, then to `seedance_create_task` after approval:

```json
{
  "request": {
    "model": "2.5",
    "content": [
      {"type": "text", "text": "Follow the camera movement and pacing of 视频1. Use the product from 图片1 and create a complete short film with natural ambient sound."},
      {"type": "video_url", "video_url": {"url": "https://example.com/reference.mp4"}, "role": "reference_video"},
      {"type": "image_url", "image_url": {"url": "asset://YOUR_PRODUCT_ASSET_ID"}, "role": "reference_image"}
    ],
    "resolution": "1080p",
    "ratio": "adaptive",
    "duration": 10,
    "generate_audio": true,
    "watermark": false
  }
}
```

Replace the URL and asset ID with real accessible inputs. Use the returned `task.id` as the `task_id` argument to `seedance_get_task`.

| Model alias | Output resolutions | Duration |
| --- | --- | --- |
| `2.0` | 480p / 720p / 1080p / 4k | 4–15 seconds or -1 |
| `2.0-fast` / `2.0-mini` | 480p / 720p | 4–15 seconds or -1 |
| `2.5` / `2.5-pro` | 480p / 720p / 1080p | 4–30 seconds or -1 |

`-1` lets the model choose duration. Set `generate_audio=true` when sound is required. Reference videos require HTTP(S) or `asset://`, not local MP4 paths or video base64. Images/audio can use supported URLs, assets or data URIs; local image/audio paths require absolute paths and `local.allow_local_files=true`.

**MediaKit's `mediakit://` upload result cannot be passed directly to Seedance.** Use a Seedance-compatible asset/upload workflow. Custom `ep-*` endpoints require the correct `local.capability_profile`. See the [video parameter reference (Chinese)](docs/seedance-parameters.md) for voice references, model limits and editing options.

### Upscale a video: standard / generative

1. For a local video, call `video_enhance_upload` with `{"file_path":"C:/videos/input.mp4"}` and retain its `video_url`.
2. Preview the following arguments with `video_enhance_preview_request`, then submit with `video_enhance_create_task` after approval.
3. Pass the returned `task.task_id` to `video_enhance_get_task`.
4. Once `status=completed`, obtain the output from `task.result.video_url`. Downloads are not automatic.

Standard:

```json
{"request":{"variant":"standard","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","scene":"aigc","enhance_style":"natural","bitrate_level":"high","client_token":"unique-request-001"}}
```

Generative:

```json
{"request":{"variant":"generative","video_url":"mediakit://YOUR_FILE_ID","resolution":"1080p","bitrate_level":"high","client_token":"unique-request-002"}}
```

- Use a new `client_token` for each new logical request. Retain the original token for an uncertain submission and inspect status first; do not casually change tokens and retry.
- Standard supports scene presets and `hd` / `natural` styles. Generative rejects `scene`, `enhance_style` and `resolution_limit`.
- Generative requires SDR input and supports 720p / 1080p / 2k output.
- Omit `fps` to preserve the input frame rate. The plugin does not proactively trim, speed up or mute videos.
- Inputs support HTTP(S), `mediakit://`, `vod://` and `tos://`. See the [enhancement parameter guide (Chinese)](docs/video-enhance.md) for full limits.

## 5. Optional environment variables

Usually only the API keys need to be configured:

| Variable | Default / purpose |
| --- | --- |
| `ARK_BASE_URL` | `https://ark.cn-beijing.volces.com/api/v3` |
| `SEEDREAM_MODEL` | `doubao-seedream-5-0-pro-260628` |
| `SEEDREAM_OUTPUT_DIR` | Current user's `Pictures/Seedream` |
| `SEEDREAM_TIMEOUT_SECONDS` | 300 seconds |
| `SEEDANCE_MODEL` | `doubao-seedance-2-5-260628` |
| `SEEDANCE_TIMEOUT_SECONDS` | 60 seconds per HTTP request; range 1–300 |
| `MEDIAKIT_BASE_URL` | `https://mediakit.cn-beijing.volces.com` |
| `MEDIAKIT_TIMEOUT_SECONDS` | 120 seconds per HTTP request; range 1–300 |
| `ARK_READ_USER_ENV` | `1`; set to `0` to disable Windows user-variable fallback |

Set the client's tool timeout to at least 360 seconds. Only administrators should configure API base URLs through environment variables; prompts and tool arguments cannot override them.

## 6. Troubleshooting and updates

| Symptom | Action |
| --- | --- |
| `No module named seedream_mcp` | Run `python -m pip install .` with the Python interpreter used by the client |
| Key set but not detected | Inspect capabilities `configuration`; check spelling, empty overrides, stale process variables and the fallback switch |
| 401 / 403 | Verify the service-specific key, expiry, enabled service and permissions; enhancement does not automatically use the Ark key |
| Parameter rejected | Check the relevant capabilities tool; different models/variants accept different fields |
| Task ID returned but no video | Query that task with get_task; do not create another |
| Result link no longer works | It may have expired. Download promptly. MediaKit can refresh links within supported retention, but recovery is not guaranteed for every service |
| New tools missing after upgrade | Reinstall the Python package and reload the plugin; start a new Codex task or reconnect MCP in other clients |

To update, extract the new package, run `python -m pip install .` again, and reload the plugin. Existing environment/user-variable settings can be reused. Keep generated outputs and keys out of packages you share.
