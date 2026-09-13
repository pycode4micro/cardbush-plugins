# Tencent COS Upload

[中文](README.md)

Four independent MCP tools, retaining the original upload interface:

| Capability | Tool | Behavior |
|---|---|---|
| Upload | `upload_video` | Local video unchanged → COS URL and object key |
| Download | `download_object` | One exact object → a new local file, no overwrite |
| Delete | `delete_object` | Preview then explicitly confirmed single-object deletion |
| Rename/move | `rename_object` | Same-bucket copy, verify, delete; non-atomic, guarded |

Call separately for each pre-cut clip or upload the entire video. No cutting, transcoding, white-model processing, generation, recursive/batch deletion or bucket administration. Download/delete/rename operate on object keys, not just video extensions.

Independent bucket/upload-arrow branding, not an official Tencent plugin. The Codex manifest includes light/dark display logos and a small icon. MCP server/tool `icons` metadata embeds a PNG for compatible Claude/Cardbush and other clients; no remote icon fetch is needed. Display depends on client support.

## Install

Requires Python 3.11+. Extract the plugin, then run inside its directory:

```shell
python -m pip install .
```

Load the directory/package through a compatible Codex/Cardbush plugin manager (`.codex-plugin/plugin.json`). Archive-import support depends on the client. For generic MCP clients, including Claude Desktop, use `examples/generic-mcp.json`:

```json
{"mcpServers":{"tencent-cos-upload":{"command":"python","args":["-m","cos_upload_mcp"]}}}
```

Use the same Python interpreter that installed the package. Set its absolute executable path if your GUI cannot find it. This JSON is generic MCP configuration, not a Claude plugin marketplace manifest.

## Configure using system/user environment variables

Create a COS bucket first. Set these variables in Windows **Environment Variables → User variables**, or in the environment that starts your MCP client. Never put actual credentials in chat, plugin files or source control.

| Variable | Value |
|---|---|
| `TENCENT_COS_SECRET_ID` | Tencent Cloud access key ID, NOT a Volcengine ARK key |
| `TENCENT_COS_SECRET_KEY` | Matching Tencent Cloud secret key |
| `TENCENT_COS_BUCKET` | Full `bucketname-appid` |
| `TENCENT_COS_REGION` | Bucket region, e.g. `ap-guangzhou` |

Prefer scoped subaccount/temporary credentials; grant only needed operations:

- Upload/link reads: `cos:PutObject`, `cos:GetObject` on the target prefix.
- Download: `cos:HeadObject`, `cos:GetObject`.
- Delete: `cos:HeadObject`, `cos:DeleteObject`.
- Rename: source/target read, write, HEAD and delete, plus `cos:GetObjectACL` and bucket `cos:GetBucketVersioning` for safety checks.

No bucket-wide listing, configuration changes or historical-version deletion permissions are required. Existing upload-only credentials need additional grants before using new operations.

| Optional variable | Default / purpose |
|---|---|
| `TENCENT_COS_TOKEN` | Matching security token for temporary credentials |
| `TENCENT_COS_PREFIX` | `reference-videos` |
| `TENCENT_COS_USE_PRESIGNED_URL` | `true`; recommended for private buckets |
| `TENCENT_COS_PRESIGNED_EXPIRES_SECONDS` | `86400`; allowed 60–604800 seconds; temporary credentials can expire earlier |
| `TENCENT_COS_PUBLIC_BASE_URL` | Existing HTTPS public/CDN base, unsigned mode only |
| `COS_UPLOAD_ALLOWED_ROOT` | Optional absolute directory restricting upload sources and download destinations; restrict remote object scope through COS credential permissions |
| `COS_UPLOAD_TIMEOUT_SECONDS` | `120`; SDK network timeout, 5–1800, not a whole-job deadline |
| `COS_UPLOAD_READ_USER_ENV` | Process-only switch, default `1`; `0` disables Windows user-variable fallback |

Aliases: `COS_SECRET_ID`, `COS_SECRET_KEY`, `COS_TOKEN`, `COS_BUCKET`, `COS_REGION`. Within a configuration layer, canonical names win.

Precedence: **present process variables (even empty) → current Windows user variables → defaults**. Values are read per call without modifying the environment or registry. No automatic `.env`, factory-settings or other-user access. Migrate existing file-based settings yourself.

A stale nonempty process value overrides a newer user value: fully restart the client in that case. Do not put empty secret placeholders into MCP `env`; they intentionally prevent fallback. Linux/macOS require inherited environment or client-provided MCP `env` values.

## Upload

```json
{"file_path":"D:/videos/clip-01.mp4","prefix":"references/job-001","presign":true,"expires_seconds":86400}
```

Only `file_path` is required. One absolute local file; no directory scan, wildcard or remote URL. MP4/M4V/MOV/WebM/MKV/AVI/MPEG/MPG/TS/MTS, nonempty, at most 5 GiB. Extension/size checks only, not media decoding or content validation. Bytes are streamed unchanged, without loading the entire video into memory.

Success returns `status=uploaded`, `url`, `object_key`, `bucket`, `region`, `size_bytes`, `content_type`, `etag`, `url_type`, `expires_seconds` and `expires_at`. Date/UUID keys avoid collisions and do not disclose the source filename. A forbid-overwrite header is also sent; COS may ignore that header when bucket versioning is enabled/suspended.

Pass `url` unchanged to the intended downstream video API. Signed links authorize **GET**, not necessarily HEAD. No extra download probe is performed; `access_verified=false`.

## Download

Call `download_object` with the upload result's `object_key`, not a full URL:

```json
{"object_key":"references/clip-01.mp4","file_path":"D:/videos/downloaded-clip.mp4"}
```

Absolute destination, existing parent directory, nonexistent target file. Streams to a temporary file, checks ETag/length, then publishes without overwriting existing files. Returns `status=downloaded`, local path, size and local SHA-256. No transcoding, maximum 5 GiB per object.

## Delete

Preview via `delete_object`:

```json
{"object_key":"references/clip-01.mp4"}
```

Default `confirm=false` only inspects. After explicit user authorization for this exact object, execute with the preview ETag:

```json
{"object_key":"references/clip-01.mp4","confirm":true,"expected_etag":"etag-from-preview"}
```

One exact key; no URLs, directories, wildcards, batches or explicit historical-version deletion. Deletion may be permanent in unversioned buckets or create a delete marker in versioned buckets; the result reports this when available. Old URLs may stop working. **Preview is not user consent.** ETag checks are optimistic, not atomic conditional deletion; stop concurrent writers to the key.

## Rename / same-bucket move

Preview via `rename_object`:

```json
{"object_key":"references/old.mp4","new_object_key":"references/new.mp4"}
```

After explicit user authorization, repeat with `confirm=true` and preview `etag` as `expected_etag`.

Checks source/target, copies server-side, verifies copied ETag/size and unchanged source, then deletes the source and returns a new signed URL. **Not atomic.** Stop concurrent writers to both keys and do not change bucket versioning/ACL during the operation.

Limited to the configured bucket, at most 5 GiB, nonexistent target, never-versioned buckets and source objects inheriting default bucket ACL. Custom object ACLs or enabled/suspended versioning are rejected without mutation; no silent overwrite/access-policy change. Metadata follows COS COPY semantics; creation time and old URLs are not preserved.

Partial states: `copy_outcome_unknown` = copy uncertain, no source delete attempted; `copied_source_retained` = verification failed, no source delete attempted; `copied_delete_outcome_unknown` = verified copy but uncertain source deletion, inspect both keys before retrying. `renamed` with `url=null` = rename complete, only signing failed. No automatic rollback deletion.

## Security, costs and failures

- Uploads incur Tencent COS storage/request costs and later download/egress costs. Offline tests do not upload.
- A signed URL is a bearer credential. Anyone holding it may read the video while authorized; a downstream model service receiving the URL gets that access. Signing query parameters include identity/token information. Do not publish full URLs in logs.
- URL expiration does not delete the object. Only explicitly invoked delete/rename operations request deletion of selected objects. No automatic cleanup or lifecycle changes.
- Unsigned mode never changes ACLs. Existing bucket/CDN public-read policy must allow access, otherwise 403 is possible.
- `upload_outcome_unknown`: the object may already exist; inspect `object_key` before retrying. `uploaded_url_failed`: upload succeeded but signing failed; retain the object key and do not reupload automatically. `source_changed`: verify the source and uploaded object before use.
- No plugin-level full-file retry. The official SDK may retry network requests. Client cancellation/timeouts do not guarantee an in-flight upload has stopped. Wait for upstream export/cutting to finish before uploading.
- `delete_outcome_unknown` requires inspecting COS before any retry. Downloads, copies and read-only previews can also incur request/traffic costs.
- Raw SDK errors/debug logs are suppressed. Your MCP client may still retain input paths and result URLs; redact them before sharing.
- No credentials, bucket settings, media or application source are bundled. The plugin runs independently.

Official references: [Python SDK](https://github.com/tencentyun/cos-python-sdk-v5), [presigned URLs](https://intl.cloud.tencent.com/zh/document/product/436/31548), [copy/move](https://cloud.tencent.com/document/product/436/65826).
