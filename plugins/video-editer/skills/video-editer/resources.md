# Resource controls

Use background `media_register`, `media_prepare`, `render_submit` and
`render_retry` for expensive work. Render retry now returns a queued job, not a
finished video. Poll status and use only `succeeded.result`. A resource wait is
still `queued`, with `resource_wait` explaining memory, CPU or lock contention.
Use `task_queue_list` to inspect the combined media/render order and wait reasons.
It is not a failed task and does not need resubmission. Cancellation works while
waiting. On MCP startup/reconnection, existing queued jobs are automatically
drained without creating new attempts; either queue-start tool can also restart
the dispatcher. Interrupted jobs require an explicit queued retry. Some hosts
terminate all child processes on disconnect (including Windows Job Objects), so
offline execution is not guaranteed. Queued work resumes on reconnection; this
plugin does not install an independent OS service or alter host process controls.

`resource_status` reads policy and the last GPU probe without starting work.
`resource_configure(settings)` changes portable defaults for subsequent heavy
slots. `resource_probe` tests four synthetic frames per candidate encoder under
the same resource lock; it never edits a project. Inspect returned failures.

| Setting | Default and meaning |
|---|---|
| heavy_concurrency | Fixed at 1; includes rendering, media jobs and guarded synchronous operations |
| cpu_threads | 0 means auto: one quarter of logical CPUs, at least 1 and at most 4 |
| min_free_memory_mb | 1024; minimum free RAM for admitting work |
| reserve_memory_percent | 20; admission keeps the larger of this reserve and min_free_memory_mb |
| max_worker_memory_mb | 0 means auto: 25% RAM, capped at 4096 MiB; sampled worker-and-children RSS |
| max_system_cpu_percent | 85; wait before admitting a new heavy operation when the system is busy |
| encoder | auto, cpu, gpu_required, h264_nvenc, h264_qsv, h264_amf or h264_videotoolbox |
| allow_cpu_fallback | true; a failed GPU probe can select bounded CPU encoding |

GPU selection uses the running FFmpeg's encoder list and a real short encode;
an encoder being compiled in is insufficient. `auto` tries NVIDIA NVENC, Intel
QSV, AMD AMF and Apple VideoToolbox when available. The probe cache expires after
10 minutes and is tied to the executable and encoding policy. Driver/device
failures during H.264 encoding can trigger one CPU fallback; the reason is saved
in job resource events. Set `gpu_required` or `allow_cpu_fallback=false` to fail
instead. No drivers or FFmpeg builds are automatically downloaded.

This accelerates H.264 encoding. Decoding, filters, Python graphics and lossless
intermediate masks can still use CPU. Do not promise an entirely GPU pipeline,
identical CPU/GPU encoded bytes, or zero effect on other applications. Portable
backend options follow [FFmpeg codec documentation](https://ffmpeg.org/ffmpeg-codecs.html)
and are validated against the actual runtime.

Child encoders run at low priority with bounded decoder/encoder/filter threads.
Detached Python workers also lower priority and apply CPU affinity where the OS
supports it. RAM is sampled; excess job memory or critically low system memory
stops only this job and retains checkpoints for queued retry. This is not an OS
memory sandbox or a precise GPU-utilization quota.

Policy, cached probes and the OS lock live in `~/.video_editer_runtime`, outside
the plugin. `VIDEO_EDITER_RESOURCE_DIR` selects a shared directory for processes
that must obey one limit. By default the scope is one OS user on one host,
including different `VIDEO_EDITER_DATA_DIR` values. Separate resource directories
are separate budgets; this is not distributed scheduling across machines/users.

Changing settings does not restart tasks or edit timelines. Do not start a real
render just to inspect resources. Use an isolated synthetic project for software
verification, and preserve the user's existing projects and media.
