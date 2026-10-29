"""Bounded, isolated full-GPU capability probe used by the MCP host."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
import numpy as np


def probe():
    from .gpu_pipeline import GPUPipeline
    from .gpu_video import probe_codecs
    from .plaster_face import ROOT
    from video_face_stylizer.models import ProcessingOptions
    options = ProcessingOptions(tiled_detection=False, tiled_segmentation=False)
    pipeline = GPUPipeline(256, 256, ROOT/'models', options, ROOT.parents[2]/'.gpu-cache')
    try:
        # Run every model, including models that an empty shot would skip.
        pipeline.frames.upload(bytes([40, 50, 60, 255])*256*256)
        for name in pipeline.vision.models:
            pipeline.vision.infer(name, pipeline.vision.input(pipeline.frames, name))
        pipeline.process(bytes([40, 50, 60, 255])*256*256, 0, 0)
        pipeline.process(bytes([40, 50, 60, 255])*256*256, 100, .1)
        with tempfile.TemporaryDirectory(prefix='video-face-gpu-codecs-') as directory:
            codecs = probe_codecs(Path(directory), pipeline.frames, pipeline.backend, pipeline.codec_device)
        return {'checked': True, 'available': True, 'device': pipeline.vision.device,
                'backend': pipeline.backend,
                'renderer': pipeline.renderer.renderer, 'inference': pipeline.vision.inference_delegate,
                **codecs, 'pipeline_devices': {stage: 'gpu' for stage in
                ['decode', 'preprocess', 'inference', 'mask', 'temporal', 'render', 'composite', 'encode']},
                'fallback_allowed': False}
    finally:
        pipeline.close()


if __name__ == '__main__':
    try:
        result = probe()
    except Exception as error:
        result = {'checked': True, 'available': False, 'error': str(error)[-1800:],
                  'fallback_allowed': False,
                  'requirement': 'Hardware OpenGL 4.3 and either Intel OpenVINO/QSV or NVIDIA DirectML/NVDEC/NVENC. Run in a logged-in desktop session.'}
    print(json.dumps(result, ensure_ascii=False), flush=True)
