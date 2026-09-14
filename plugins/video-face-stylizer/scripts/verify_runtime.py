"""Check imports, bundled models, FFmpeg and a real CPU inference before ready."""
import hashlib,json,subprocess
import cv2,imageio_ffmpeg,mediapipe,numpy as np,numba,moderngl,psutil
from video_face_stylizer.server import create_server,capabilities
from video_face_stylizer.engine.plaster_face import FacePipeline,ROOT
from video_face_stylizer.models import ProcessingOptions
from video_face_stylizer.processes import ffmpeg_executable,hidden_process_options

def verify():
    create_server()
    caps=capabilities(False)
    if not caps['models_present'] or any(value is None for value in caps['packages'].values()):
        raise RuntimeError('Runtime dependencies or bundled models are missing.')
    for asset in json.loads((ROOT/'model_sources.json').read_text(encoding='utf-8')):
        if hashlib.sha256((ROOT/'models'/asset['file']).read_bytes()).hexdigest()!=asset['sha256']:
            raise RuntimeError('Bundled model checksum mismatch: '+asset['file'])
    pipeline=FacePipeline(64,64,ROOT/'models','cpu',ProcessingOptions(tiled_detection=False,tiled_segmentation=False))
    try:
        result,_=pipeline.process(np.zeros((64,64,3),np.uint8),0)
        assert result.shape==(64,64,3)
    finally:pipeline.close()
    from video_face_stylizer.engine.cpu_raster import rasterize
    vertices=np.array([[0,0,0,0,0,1,1],[7,0,0,0,0,1,1],[0,7,0,0,0,1,1]],np.float32)
    rasterize(vertices,np.array([[0,1,2]],np.int32),vertices[:,:2].copy(),8,8,
              np.array([0,0,1],np.float32),np.array([0,0,1],np.float32),np.array([0,0,1],np.float32))
    result=subprocess.run([ffmpeg_executable(),'-hide_banner','-encoders'],stdin=subprocess.DEVNULL,capture_output=True,text=True,timeout=20,**hidden_process_options())
    if result.returncode or 'libx264' not in result.stdout:raise RuntimeError('Bundled FFmpeg is missing the libx264 encoder.')
    return {'ready':True,'capabilities':caps}

if __name__=='__main__':print(json.dumps(verify(),ensure_ascii=False))
