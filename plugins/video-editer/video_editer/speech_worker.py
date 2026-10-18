"""Private bounded child process for local ASR. Parent owns the heavy-work slot."""
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import sys
import wave

from . import speech


def choose_device(requested, policy, cuda_count):
    if requested == 'cpu':
        if not policy['allow_cpu_fallback'] or policy.get('encoder') == 'gpu_required': raise ValueError('CPU ASR is disabled by the resource policy')
        return 'cpu', 'int8', 'CPU explicitly requested'
    if cuda_count > 0: return 'cuda', 'float16', None
    if requested == 'cuda' or not policy['allow_cpu_fallback'] or policy.get('encoder') == 'gpu_required':
        raise ValueError('No supported CUDA ASR device; GPU is required. Fix runtime or explicitly allow CPU fallback; retry through the queue.')
    return 'cpu', 'int8', 'No supported CUDA ASR device; using bounded CPU. QSV/AMF/VideoToolbox encoding does not imply CUDA ASR support.'


def load_pcm(path):
    # FFmpeg already normalized this audio. Avoid decoding again through a second
    # library with different time handling / incompatible PyAV versions.
    import numpy as np
    with wave.open(str(path), 'rb') as stream:
        if (stream.getnchannels(), stream.getframerate(), stream.getsampwidth()) != (1, 16000, 2):
            raise ValueError('ASR worker requires normalized mono 16kHz PCM16 audio')
        return np.frombuffer(stream.readframes(stream.getnframes()), dtype='<i2').astype(np.float32) / 32768.0


def main():
    source, target = map(Path, sys.argv[1:3])
    request = json.loads(source.read_text('utf-8'))
    directory = Path(request['model_path'])
    def model_identity():
        result = {}
        for name in speech.MODEL_FILES:
            with (directory / name).open('rb') as stream:
                result[name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return result
    if request['action'] == 'prepare':
        offline = request.get('source_model_dir')
        if offline and Path(offline).resolve() != directory.resolve():
            directory.mkdir(parents=True, exist_ok=True)
            for name in speech.MODEL_FILES:
                origin, destination = Path(offline) / name, directory / name
                if destination.exists():
                    with origin.open('rb') as a, destination.open('rb') as b:
                        if hashlib.file_digest(a, 'sha256').digest() != hashlib.file_digest(b, 'sha256').digest():
                            raise ValueError('Existing model differs; configure a new VIDEO_EDITER_ASR_MODEL_DIR instead of overwriting')
                else:
                    temporary = directory / (name + '.partial')
                    shutil.copyfile(origin, temporary)
                    temporary.replace(destination)
        if not all((directory / name).is_file() for name in speech.MODEL_FILES):
            from huggingface_hub import snapshot_download
            snapshot_download(repo_id=request['model_repo'], revision=request['model_revision'],
                              allow_patterns=list(speech.MODEL_FILES), local_dir=str(directory), max_workers=1)
        identity = model_identity()
        result = {'model_path': str(directory), 'model_identity': identity, 'files_ready': True,
                  'scope': 'Local model files prepared; actual inference/device verified by speech_transcribe_submit.'}
    else:
        import ctranslate2
        from faster_whisper import WhisperModel
        policy = request['resource_policy']
        device, compute, reason = choose_device(request['device'], policy, ctranslate2.get_cuda_device_count())
        identity = model_identity()
        model = WhisperModel(str(directory), device=device, compute_type=compute,
                             cpu_threads=policy['cpu_threads'], num_workers=1, local_files_only=True)
        samples = load_pcm(request['audio_path'])
        segments, info = model.transcribe(samples, language=request['language'], task='transcribe',
             beam_size=5, word_timestamps=True, condition_on_previous_text=False, vad_filter=True)
        rows = []
        for segment in segments:
            rows.append({'text': segment.text, 'words': [w._asdict() for w in segment.words or []]})
        result = {'engine': 'faster-whisper ' + importlib.metadata.version('faster-whisper'),
                  'model': str(directory), 'model_identity': identity,
                  'device': device, 'compute_type': compute, 'fallback_reason': reason,
                  'language': info.language, 'segments': rows}
    target.write_text(json.dumps(result, ensure_ascii=False, allow_nan=False), encoding='utf-8')


if __name__ == '__main__':
    main()
