"""Local video face-to-plaster prototype: tracked 3D geometry + white material.

The output is a composited video, not a watertight or metric 3D head scan.
"""
from pathlib import Path
import argparse, json, math, subprocess, time, sys
import cv2
import numpy as np
import mediapipe as mp
import moderngl
from video_face_stylizer.processes import ffmpeg_executable,hidden_process_options
from video_face_stylizer.models import ProcessingOptions
from video_face_stylizer.engine.head_coverage import (
    TemporalCoverage, clean_head_mask, ellipse_mask, manual_mask,
    hair_supported_probability, head_proposals, map_landmarks, person_head_probability, plaster_fill, tile_boxes,
)

ROOT = Path(__file__).resolve().parent
EYES = [[33,246,161,160,159,158,157,173,133,155,154,153,145,144,163,7],
        [263,466,388,387,386,385,384,398,362,382,381,380,374,373,390,249]]
MOUTH = [78,191,80,81,82,13,312,311,310,415,308,324,318,402,317,14,87,178,88,95]
FOREHEAD = [162,21,54,103,67,109,10,338,297,332,284,251,389]

def unit(v):
    return v / np.maximum(np.linalg.norm(v, axis=-1, keepdims=True), 1e-8)

def load_topology(model_dir):
    text=(model_dir/'canonical_face_model.obj').read_text()
    faces=[[int(x.split('/')[0])-1 for x in line.split()[1:4]]
           for line in text.splitlines() if line.startswith('f ')]
    apertures=[set(ring) for ring in EYES+[MOUTH]]
    return np.array([f for f in faces if not any(set(f)<=ring for ring in apertures)],np.int32)

class PlasterRenderer:
    def __init__(self, width, height, model_dir):
        self.w, self.h = width, height
        self.base_faces=load_topology(model_dir)
        self.ctx=moderngl.create_standalone_context(require=330)
        self.renderer=self.ctx.info['GL_RENDERER']
        self.program=self.ctx.program(vertex_shader='''
            #version 330
            in vec3 in_position; in vec3 in_normal; in float in_shade;
            uniform vec2 resolution;
            out vec3 normal; out float shade;
            void main() {
                gl_Position=vec4(2.0*in_position.x/resolution.x-1.0,
                  1.0+2.0*in_position.y/resolution.y, -in_position.z/resolution.x, 1.0);
                normal=in_normal; shade=in_shade;
            }
        ''',fragment_shader='''
            #version 330
            in vec3 normal; in float shade; out vec4 color;
            void main() {
                vec3 n=normalize(normal);
                vec3 key=normalize(vec3(-0.55,0.75,1.1));
                vec3 fill=normalize(vec3(0.9,0.0,0.5));
                float diffuse=max(dot(n,key),0.0);
                float light=0.37+0.57*diffuse+0.11*max(dot(n,fill),0.0);
                float spec=0.035*pow(max(dot(n,normalize(key+vec3(0,0,1))),0.0),12.0);
                float s=clamp(light*shade+spec,0.0,1.0);
                color=vec4(vec3(s)*vec3(1.0,0.993,0.982),1.0);
            }
        ''')
        self.program['resolution'].value=(width,height)
        self.vbo=self.ctx.buffer(reserve=20000*7*4)
        self.ibo=self.ctx.buffer(reserve=50000*3*4)
        self.vao=self.ctx.vertex_array(self.program,[(self.vbo,'3f 3f 1f','in_position','in_normal','in_shade')],self.ibo)
        self.msaa=self.ctx.framebuffer(self.ctx.renderbuffer((width,height),4,samples=4),self.ctx.depth_renderbuffer((width,height),samples=4))
        self.output=self.ctx.simple_framebuffer((width,height),components=4)
        self.ctx.enable(moderngl.DEPTH_TEST)

    def mesh(self, lm):
        verts=(lm[:468]*np.array([self.w,-self.h,-self.w])).astype(np.float32)
        shade=np.ones(468,np.float32)
        # Slight sculpted recess at nostrils; all albedo remains neutral white.
        shade[[98,97,2,326,327]]=0.82
        faces=self.base_faces.tolist()
        verts=verts.tolist(); shade=shade.tolist()
        # Landmarks stop in the visible forehead. Extend behind the hair matte
        # so this boundary does not leave a horizontal skin-colored band.
        brow=(np.array(verts[105])+np.array(verts[334]))*.5
        top=np.array(verts[10]); chin=np.array(verts[152])
        up=unit(top[:2]-chin[:2]); fh=np.linalg.norm(top[:2]-chin[:2])
        denom=max(np.dot(top[:2]-brow[:2],up),1)
        outer=[]
        for idx in FOREHEAD:
            q=np.array(verts[idx]); ratio=np.clip(np.dot(q[:2]-brow[:2],up)/denom,0,1)
            q[:2]+=up*fh*.23*ratio; q[2]-=fh*.09*ratio
            outer.append(len(verts));verts.append(q.tolist());shade.append(1.)
        for k in range(len(outer)-1):
            faces.extend([[FOREHEAD[k],FOREHEAD[k+1],outer[k+1]],
                          [FOREHEAD[k],outer[k+1],outer[k]]])
        for ring in EYES:
            for i in ring: shade[i]=.9
            pts=np.array([verts[i] for i in ring],np.float32)
            center=pts.mean(axis=0)
            normal=unit(np.cross(pts[3]-pts[-3],pts[len(pts)//2]-pts[0]))
            if normal[2]<0: normal=-normal
            width=np.linalg.norm(pts[0]-pts[len(pts)//2])
            prev=ring
            # Concentric domed rings make blank plaster eyes without pupils.
            for radius in [.72,.38,0.06]:
                new=[]
                for p in pts:
                    q=center+radius*(p-center)+normal*width*.105*(1-radius*radius)
                    new.append(len(verts)); verts.append(q.tolist()); shade.append(.97)
                for k in range(len(ring)):
                    j=(k+1)%len(ring)
                    faces.extend([[prev[k],prev[j],new[j]],[prev[k],new[j],new[k]]])
                prev=new
            ci=len(verts); verts.append((center+normal*width*.105).tolist()); shade.append(.99)
            for k in range(len(ring)): faces.append([prev[k],prev[(k+1)%len(ring)],ci])
        # A recessed neutral cavity follows the tracked inner lip contour.
        mouth_pts=np.array([verts[i] for i in MOUTH])
        inner=[]
        for p in mouth_pts:
            q=p.copy(); q[2]-=2
            inner.append(len(verts)); verts.append(q.tolist()); shade.append(.36)
        ci=len(verts); q=mouth_pts.mean(axis=0); q[2]-=4
        verts.append(q.tolist()); shade.append(.23)
        for k in range(len(inner)): faces.append([inner[k],inner[(k+1)%len(inner)],ci])
        verts=np.asarray(verts,np.float32); faces=np.asarray(faces,np.int32)
        tri=verts[faces]
        n=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
        # New aperture fills may have opposite winding; lighting is outward.
        base=len(self.base_faces)
        n[base:]=np.where(n[base:,2:3]<0,-n[base:],n[base:])
        normals=np.zeros_like(verts)
        for k in range(3): np.add.at(normals,faces[:,k],n)
        normals=unit(normals)
        return np.column_stack([verts,normals,np.array(shade,np.float32)]).astype(np.float32),faces

    def render(self,lm):
        vertices,faces=self.mesh(lm)
        self.vbo.write(vertices.tobytes()); self.ibo.write(faces.tobytes())
        self.msaa.use(); self.msaa.clear(0,0,0,0,depth=1)
        self.vao.render(moderngl.TRIANGLES,vertices=faces.size)
        self.ctx.copy_framebuffer(self.output,self.msaa)
        rgba=np.frombuffer(self.output.read(components=4,alignment=1),np.uint8).reshape(self.h,self.w,4)
        return np.flipud(rgba).copy()

    def close(self):
        self.vao.release(); self.vbo.release(); self.ibo.release()
        self.msaa.release(); self.output.release(); self.program.release(); self.ctx.release()

class CPUPlasterRenderer(PlasterRenderer):
    """Software z-buffer and interpolated-normal shader; no OpenGL context.

    Uses four subpixel samples (2x SSAA), approximating the GPU's 4x MSAA.
    Geometry, normals, material and lighting are shared with the GPU renderer.
    """
    def __init__(self,width,height,model_dir):
        self.w,self.h=width,height
        self.base_faces=load_topology(model_dir)
        self.renderer='CPU software rasterizer (Numba compiled, 2x SSAA; no GPU context)'
        from video_face_stylizer.engine.cpu_raster import rasterize
        self.rasterize=rasterize
        self.key=unit(np.array([-.55,.75,1.1],np.float32))
        self.fill=unit(np.array([.9,0.,.5],np.float32))
        self.half=unit(self.key+np.array([0.,0.,1.],np.float32))

    def render(self,lm):
        vertices,faces=self.mesh(lm)
        screen=vertices[:,:2]*[1,-1]
        x0=max(0,int(np.floor(screen[:,0].min()))-1)
        y0=max(0,int(np.floor(screen[:,1].min()))-1)
        x1=min(self.w,int(np.ceil(screen[:,0].max()))+1)
        y1=min(self.h,int(np.ceil(screen[:,1].max()))+1)
        result=np.zeros((self.h,self.w,4),np.uint8)
        if x1<=x0 or y1<=y0:return result
        scale=2
        rw,rh=(x1-x0)*scale,(y1-y0)*scale
        xy=((screen-[x0,y0])*scale).astype(np.float32)
        rgba=self.rasterize(vertices,faces,xy,rw,rh,self.key,self.fill,self.half)
        result[y0:y1,x0:x1]=cv2.resize(rgba,(x1-x0,y1-y0),interpolation=cv2.INTER_AREA)
        return result

    def close(self):
        pass

class FacePipeline:
    def __init__(self,w,h,model_dir,renderer='gpu',options=None):
        self.w,self.h=w,h
        self.options=options or ProcessingOptions()
        self.model_dir=model_dir
        self.crop_tracker=None
        self.last_box=None
        self.temporal=TemporalCoverage(self.options.temporal_hold_seconds)
        v=mp.tasks.vision
        # Model buffers avoid native Windows path decoding failures in Chinese directories.
        self.tracker=v.FaceLandmarker.create_from_options(v.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=(model_dir/'face_landmarker.task').read_bytes(),delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=v.RunningMode.VIDEO,num_faces=1,
            min_face_detection_confidence=self.options.min_face_detection_confidence,
            min_face_presence_confidence=self.options.min_face_presence_confidence,
            min_tracking_confidence=self.options.min_tracking_confidence))
        self.segmenter=v.ImageSegmenter.create_from_options(v.ImageSegmenterOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=(model_dir/'selfie_multiclass.tflite').read_bytes(),delegate=mp.tasks.BaseOptions.Delegate.CPU),
            output_confidence_masks=True,output_category_mask=False))
        self.person_detector=v.ObjectDetector.create_from_options(v.ObjectDetectorOptions(
            base_options=mp.tasks.BaseOptions(model_asset_buffer=(model_dir/'efficientdet_lite0.tflite').read_bytes(),delegate=mp.tasks.BaseOptions.Delegate.CPU),
            category_allowlist=['person'],score_threshold=self.options.person_detection_confidence,max_results=10)) if self.options.person_filter else None
        self.renderer=(CPUPlasterRenderer if renderer=='cpu' else PlasterRenderer)(w,h,model_dir)
        self.detected=0
        self.coverage_counts={'person_frames':0,'no_person_frames':0,'full_frame_faces':0,'crop_faces':0,'segmentation_crop_frames':0,'segmentation_fallback_frames':0,
                              'temporal_fallback_frames':0,'manual_mask_frames':0,'covered_frames':0,'uncovered_frames':0,
                              'no_mask_frames':0,'fallback_only_frames':0}
        self.review_intervals=[]
        self.timings={'tracking':0.,'segmentation':0.,'render_composite':0.}

    def _person_mask(self,rgb):
        self.person_boxes=[]
        if self.person_detector is None:return np.full((self.h,self.w),255,np.uint8)
        result=self.person_detector.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(rgb)))
        allowed=np.zeros((self.h,self.w),np.uint8)
        for detection in result.detections:
            box=detection.bounding_box
            padx,pady=box.width*.03,box.height*.04
            x0=max(0,int(box.origin_x-padx));y0=max(0,int(box.origin_y-pady))
            x1=min(self.w,int(box.origin_x+box.width+padx));y1=min(self.h,int(box.origin_y+box.height+pady))
            allowed[y0:y1,x0:x1]=255
            if x1>x0 and y1>y0:self.person_boxes.append((x0,y0,x1,y1))
        return allowed

    def _filter_probability(self,probability):
        if self.person_detector is None:return probability
        return person_head_probability(probability,self.person_boxes,self.options.segmentation_confidence,self.options.segmentation_head_fraction)

    def _detect(self,rgb,timestamp_ms,proposals=(),allowed=None):
        def accepted(points):
            if allowed is None:return True
            center=np.mean(points[:468,:2],axis=0)
            x,y=np.floor(center*[self.w,self.h]).astype(int)
            return 0<=x<self.w and 0<=y<self.h and allowed[y,x]>0
        scale=min(1.,self.options.detection_max_side/max(rgb.shape[:2]))
        small=cv2.resize(rgb,None,fx=scale,fy=scale) if scale<1 else rgb
        result=self.tracker.detect_for_video(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(small)),timestamp_ms)
        if result.face_landmarks:
            points=np.array([[p.x,p.y,p.z] for p in result.face_landmarks[0]],np.float32)
            if accepted(points):
                self.coverage_counts['full_frame_faces']+=1
                return points
        if not self.options.tiled_detection:return None
        if self.crop_tracker is None:
            v=mp.tasks.vision
            self.crop_tracker=v.FaceLandmarker.create_from_options(v.FaceLandmarkerOptions(
                base_options=mp.tasks.BaseOptions(model_asset_buffer=(self.model_dir/'face_landmarker.task').read_bytes(),delegate=mp.tasks.BaseOptions.Delegate.CPU),
                running_mode=v.RunningMode.IMAGE,num_faces=1,
                min_face_detection_confidence=self.options.min_face_detection_confidence,
                min_face_presence_confidence=self.options.min_face_presence_confidence,
                min_tracking_confidence=self.options.min_tracking_confidence))
        priority=([self.last_box] if self.last_box else [])+list(proposals)
        boxes=priority+tile_boxes(self.w,self.h,self.options.detection_tile_grid)
        candidates=[]
        for box in dict.fromkeys(boxes):
            x0,y0,x1,y1=box
            if allowed is not None and not np.any(allowed[y0:y1,x0:x1]):continue
            crop=rgb[y0:y1,x0:x1]
            if min(crop.shape[:2])<8:continue
            scale=min(2.,self.options.detection_max_side/max(crop.shape[:2]))
            crop=cv2.resize(crop,None,fx=scale,fy=scale)
            result=self.crop_tracker.detect(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(crop)))
            if result.face_landmarks:
                local=np.array([[p.x,p.y,p.z] for p in result.face_landmarks[0]],np.float32)
                points=map_landmarks(local,box,self.w,self.h)
                if not accepted(points):continue
                candidates.append(points)
                if box in priority:break
        if not candidates:return None
        self.coverage_counts['crop_faces']+=1
        return max(candidates,key=lambda lm: np.prod(np.ptp(lm[:468,:2],axis=0)))

    def _head_probability(self,rgb):
        result=self.segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB,data=np.ascontiguousarray(cv2.resize(rgb,(256,256)))))
        # Model categories: 1 hair, 2 body skin, 3 face skin. Never include body skin.
        hair=result.confidence_masks[1].numpy_view();skin=result.confidence_masks[3].numpy_view()
        probability=hair_supported_probability(hair,skin,self.options.segmentation_confidence) if self.options.segmentation_requires_hair else hair+skin
        return cv2.resize(probability,(rgb.shape[1],rgb.shape[0]))

    def _review(self,reason,source_seconds):
        if self.review_intervals and self.review_intervals[-1]['reason']==reason and self.review_intervals[-1]['last_frame']==self.frames_seen-1:
            self.review_intervals[-1].update(end_seconds=source_seconds,last_frame=self.frames_seen)
        else:self.review_intervals.append({'start_seconds':source_seconds,'end_seconds':source_seconds,'last_frame':self.frames_seen,'reason':reason})

    def _record_coverage(self,covered,has_face):
        self.coverage_counts['covered_frames' if covered else 'uncovered_frames']+=1
        # Count final masks, not candidate detections. Empty shots can also have no mask.
        if not covered:
            self.coverage_counts['no_mask_frames']+=1
        elif not has_face:
            self.coverage_counts['fallback_only_frames']+=1

    def _head(self,frame,rgb,lm,timestamp_ms,source_seconds,probability,allowed):
        t=time.perf_counter()
        geometry=np.zeros((self.h,self.w),np.uint8)
        if lm is not None:
            points=lm[:468,:2]
            lo=points.min(axis=0);hi=points.max(axis=0);size=hi-lo
            box=np.clip([lo[0]-size[0]*.12,lo[1]-size[1]*.28,hi[0]+size[0]*.12,hi[1]+size[1]*.06],0,1)
            geometry=ellipse_mask(frame.shape,box)
            x0,y0,x1,y1=self.last_box
            if x1>x0 and y1>y0:
                probability[y0:y1,x0:x1]=np.maximum(probability[y0:y1,x0:x1],self._head_probability(rgb[y0:y1,x0:x1]))
        probability=self._filter_probability(probability)
        observed=clean_head_mask(probability,self.options.segmentation_confidence,self.options.mask_padding)
        observed=cv2.bitwise_or(observed,geometry)
        observed=cv2.bitwise_and(observed,allowed)
        self.timings['segmentation']+=time.perf_counter()-t
        t=time.perf_counter()
        mask,bridged,cut=self.temporal.apply(frame,observed,timestamp_ms)
        mask=cv2.bitwise_and(mask,allowed)
        bridged=bridged and bool(np.any(mask))
        if cut and lm is None:self.last_box=None
        manual=manual_mask(frame.shape,self.options.head_regions,source_seconds)
        mask=cv2.bitwise_or(mask,manual)
        if np.any(manual):self.coverage_counts['manual_mask_frames']+=1
        if bridged:self.coverage_counts['temporal_fallback_frames']+=1
        elif lm is None and np.any(observed):self.coverage_counts['segmentation_fallback_frames']+=1
        covered=bool(np.any(mask))
        self._record_coverage(covered,lm is not None)
        reason=('person_not_detected' if self.options.person_filter and not np.any(allowed) else 'no_head_mask') if not covered else 'optical_flow' if bridged else 'segmentation_only' if lm is None and not np.any(manual) else None
        if reason:self._review(reason,source_seconds)
        out=plaster_fill(frame,mask)
        if lm is not None:
            rgba=self.renderer.render(lm)
            alpha=rgba[:,:,3:4].astype(np.float32)/255
            alpha*= (mask>0)[:,:,None]
            # The base head is already opaque plaster, so MSAA never reveals original facial texture.
            color=rgba[:,:,:3][:,:,::-1].astype(np.float32)/np.maximum(rgba[:,:,3:4]/255,1/255)
            out=np.clip(out*(1-alpha)+color*alpha,0,255).astype(np.uint8)
        self.timings['render_composite']+=time.perf_counter()-t
        return out,{'landmarks':lm,'covered':covered,'temporal_fallback':bridged}

    def process(self,frame,timestamp_ms,source_seconds=None):
        t=time.perf_counter()
        rgb=cv2.cvtColor(frame,cv2.COLOR_BGR2RGB)
        allowed=self._person_mask(rgb)
        person_present=bool(np.any(allowed))
        if self.options.person_filter:
            key='person_frames' if person_present else 'no_person_frames'
            self.coverage_counts[key]=self.coverage_counts.get(key,0)+1
        probability=(self._head_probability(rgb) if person_present else np.zeros((self.h,self.w),np.float32)) if self.options.coverage=='head' else None
        if probability is not None:probability=self._filter_probability(probability*(allowed/255.))
        proposals=head_proposals(probability,self.options.segmentation_confidence) if probability is not None else []
        if person_present and probability is not None and not proposals and self.options.tiled_segmentation:
            for x0,y0,x1,y1 in tile_boxes(self.w,self.h,self.options.detection_tile_grid):
                probability[y0:y1,x0:x1]=np.maximum(probability[y0:y1,x0:x1],self._head_probability(rgb[y0:y1,x0:x1]))
            probability=self._filter_probability(probability*(allowed/255.))
            proposals=head_proposals(probability,self.options.segmentation_confidence)
            if proposals:self.coverage_counts['segmentation_crop_frames']+=1
        self.timings['segmentation']+=time.perf_counter()-t
        t=time.perf_counter()
        lm=self._detect(rgb,timestamp_ms,proposals,allowed) if person_present else None
        if not person_present:self.last_box=None
        self.timings['tracking']+=time.perf_counter()-t
        self.frames_seen=getattr(self,'frames_seen',0)+1
        if lm is not None:
            pts=lm[:468,:2]*[self.w,self.h]
            lo=pts.min(axis=0);hi=pts.max(axis=0);size=hi-lo
            self.last_box=(max(0,int(lo[0]-size[0]*.5)),max(0,int(lo[1]-size[1]*.8)),
                           min(self.w,int(hi[0]+size[0]*.5)),min(self.h,int(hi[1]+size[1]*.3)))
            self.detected+=1
        if self.options.coverage=='head':
            return self._head(frame,rgb,lm,timestamp_ms,source_seconds if source_seconds is not None else timestamp_ms/1000,probability,allowed)
        if lm is None:
            self._record_coverage(False,False)
            self._review('no_face_mask',source_seconds if source_seconds is not None else timestamp_ms/1000)
            return frame,None
        self._record_coverage(True,True)
        points=lm[:468,:2]*[self.w,self.h]
        xmin,ymin=points.min(axis=0); xmax,ymax=points.max(axis=0)
        fw,fh=xmax-xmin,ymax-ymin
        x0=max(0,int(xmin-fw*.32));x1=min(self.w,int(xmax+fw*.32))
        y0=max(0,int(ymin-fh*.35));y1=min(self.h,int(ymax+fh*.15))
        t=time.perf_counter()
        crop=cv2.resize(rgb[y0:y1,x0:x1],(320,320))
        segment=self.segmenter.segment(mp.Image(image_format=mp.ImageFormat.SRGB,data=crop))
        hair=cv2.resize(segment.confidence_masks[1].numpy_view(),(x1-x0,y1-y0))
        body=cv2.resize(segment.confidence_masks[2].numpy_view(),(x1-x0,y1-y0))
        self.timings['segmentation']+=time.perf_counter()-t
        t=time.perf_counter()
        rgba=self.renderer.render(lm)
        raw_alpha=rgba[y0:y1,x0:x1,3].astype(np.float32)/255
        alpha=raw_alpha.copy()
        # Preserve segmented hair and foreground body skin (e.g. a hand).
        protect=np.clip((hair-.12)/.55,0,1)
        # Hair has thin subpixel strands. Refine the matte with source brightness
        # only above the eyebrows and close to the semantic hair mask.
        gray=cv2.cvtColor(frame[y0:y1,x0:x1],cv2.COLOR_BGR2GRAY).astype(np.float32)/255
        blur=cv2.GaussianBlur(gray,(0,0),4)
        strand=np.clip((blur-gray-.02)*7,0,1)
        yy=np.arange(y0,y1)[:,None]
        brow_y=(points[105,1]+points[334,1])/2
        upper=(yy<brow_y).astype(np.float32)
        hair_near=cv2.dilate((hair>.08).astype(np.float32),np.ones((13,13),np.uint8))
        protect=np.maximum(protect,strand*upper*hair_near)
        protect=np.maximum(protect,np.clip((body-.55)/.4,0,1))
        alpha*=1-protect
        # Keep the outer boundary narrow to avoid a halo around the jaw.
        alpha=cv2.GaussianBlur(alpha,(3,3),.55)[...,None]
        patch=frame[y0:y1,x0:x1].astype(np.float32)
        plaster=rgba[y0:y1,x0:x1,:3][:,:,::-1].astype(np.float32)/np.maximum(raw_alpha[...,None],1/255)
        # Extend edge color into the one-pixel feather region.
        plaster=np.where((raw_alpha==0)[...,None],cv2.dilate(plaster,np.ones((3,3),np.uint8)),plaster)
        out=frame.copy()
        out[y0:y1,x0:x1]=np.clip(patch*(1-alpha)+plaster*alpha,0,255).astype(np.uint8)
        self.timings['render_composite']+=time.perf_counter()-t
        return out,{'landmarks':lm,'bbox':[x0,y0,x1,y1]}

    def close(self):
        self.tracker.close();self.segmenter.close();self.renderer.close()
        if self.person_detector is not None:self.person_detector.close()
        if self.crop_tracker is not None:self.crop_tracker.close()

def run(args):
    start=time.perf_counter()
    source=Path(args.input).resolve(); target=Path(args.output).resolve()
    if source==target: raise ValueError('Input and output paths must differ')
    target.parent.mkdir(parents=True,exist_ok=True)
    cv2.ocl.setUseOpenCL(False)
    cap=cv2.VideoCapture(str(source),cv2.CAP_FFMPEG,[cv2.CAP_PROP_HW_ACCELERATION,cv2.VIDEO_ACCELERATION_NONE])
    if not cap.isOpened(): raise ValueError('Cannot open input video: '+str(source))
    decoder_acceleration=cap.get(cv2.CAP_PROP_HW_ACCELERATION)
    fps=cap.get(cv2.CAP_PROP_FPS); w=int(cap.get(3)); h=int(cap.get(4)); n=int(cap.get(7))
    if not math.isfinite(fps) or fps<=0 or w<=0 or h<=0 or n<=0:
        cap.release()
        raise ValueError('Input video has invalid dimensions, frame rate or frame count.')
    if args.max_height and h>args.max_height:
        w=int(round(w*args.max_height/h/2)*2); h=int(args.max_height//2*2)
    n=min(n,round(args.seconds*fps)) if args.seconds else n
    if args.start:
        cap.set(cv2.CAP_PROP_POS_MSEC,args.start*1000)
        n=min(n,max(0,int(cap.get(7))-round(args.start*fps)))
    if n<=0:
        cap.release()
        raise ValueError('Requested time range contains no frames.')
    options=ProcessingOptions.model_validate(json.loads(args.settings))
    pipeline=FacePipeline(w,h,Path(args.models),args.renderer,options)
    ffmpeg=ffmpeg_executable()
    cmd=[ffmpeg,'-hide_banner','-loglevel','error','-y','-f','rawvideo','-pix_fmt','bgr24',
         '-s',f'{w}x{h}','-r',str(fps),'-i','pipe:0','-ss',str(args.start),'-i',str(source),
         '-map','0:v:0','-map','1:a:0?','-c:v','libx264','-preset','fast','-crf','18',
         '-pix_fmt','yuv420p','-c:a','aac','-b:a','128k','-t',str(n/fps),'-movflags','+faststart',str(target)]
    logpath=target.with_suffix('.ffmpeg.log')
    log=logpath.open('w')
    encoder=subprocess.Popen(cmd,stdin=subprocess.PIPE,stdout=subprocess.DEVNULL,stderr=log,**hidden_process_options())
    print(json.dumps({'stage':'start','frames':n,'fps':fps,'size':[w,h],
                      'renderer':pipeline.renderer.renderer},ensure_ascii=False),flush=True)
    processed=0; preview_indices={0,int(fps*5),int(fps*10),int(fps*20),n-1}
    try:
        for idx in range(n):
            ok,frame=cap.read()
            if not ok: break
            if frame.shape[:2]!=(h,w):frame=cv2.resize(frame,(w,h),interpolation=cv2.INTER_AREA)
            out,meta=pipeline.process(frame,round(idx*1000/fps),args.start+idx/fps)
            encoder.stdin.write(out.tobytes())
            processed+=1
            if args.previews and idx in preview_indices:
                pre=Path(args.previews); pre.mkdir(parents=True,exist_ok=True)
                cv2.imwrite(str(pre/f'compare_{idx:04d}.jpg'),np.hstack([frame,out]))
            if idx%90==0:
                elapsed=time.perf_counter()-start
                print(json.dumps({'frame':idx+1,'total':n,'elapsed_s':round(elapsed,2),
                      'detected':pipeline.detected,'coverage':pipeline.coverage_counts}),flush=True)
    finally:
        cap.release();encoder.stdin.close();encoder.wait();log.close();pipeline.close()
    if encoder.returncode: raise RuntimeError(logpath.read_text(errors='replace'))
    if processed!=n:raise RuntimeError(f'Video decoding stopped early: expected {n} frames, processed {processed}.')
    elapsed=time.perf_counter()-start
    stats={'source':str(source),'output':str(target),'width':w,'height':h,'fps':fps,
           'frames_processed':processed,'video_seconds':processed/fps,
           'faces_detected_frames':pipeline.detected,'missed_frames':processed-pipeline.detected,
           'coverage':pipeline.coverage_counts,'settings':options.model_dump(),
           'review_intervals':[{**{k:v for k,v in item.items() if k!='last_frame'},
                                'end_seconds':round(item['end_seconds']+1/fps,6)} for item in pipeline.review_intervals],
           'wall_seconds':elapsed,'processing_fps':processed/elapsed,
           'normalized_30s_wall_seconds':elapsed*30/(processed/fps),
           'stage_seconds':pipeline.timings,'renderer':pipeline.renderer.renderer,
           'renderer_mode':args.renderer,'inference_delegate':'CPU / XNNPACK',
           'decoder_hardware_acceleration':decoder_acceleration,
           'opencv_opencl_enabled':cv2.ocl.useOpenCL(),'encoder':'libx264 (CPU)',
           'timing_includes':'model initialization, decoding, tracking, segmentation, rendering, H264 encoding and audio mux; excludes setup/download',
           'limitations':['One 3D face mesh; head mode additionally covers segmented heads with opaque material.',
             'Coverage counts mean at least one mask exists, not that every head is fully covered. Review distant/profile/back-facing shots.',
             'no_head_mask may mean an empty shot or missed head. Use timed head_regions for unresolved shots. CFR input expected.']}
    target.with_suffix('.benchmark.json').write_text(json.dumps(stats,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(stats,ensure_ascii=False,indent=2),flush=True)
    if logpath.stat().st_size==0:logpath.unlink()

def main():
    p=argparse.ArgumentParser(description='Track a face in video and render a white plaster material locally.')
    p.add_argument('input');p.add_argument('output')
    p.add_argument('--models',default=str(ROOT/'models'))
    p.add_argument('--seconds',type=float,default=0,help='0 = entire video')
    p.add_argument('--start',type=float,default=0)
    p.add_argument('--max-height',type=int,default=0)
    p.add_argument('--previews',default='')
    p.add_argument('--renderer',choices=['gpu','cpu'],default='gpu')
    p.add_argument('--settings',default='{}',help='JSON ProcessingOptions (coverage, detection thresholds, head_regions, etc.).')
    args=p.parse_args()
    if args.seconds<0 or args.start<0: p.error('Time values must not be negative')
    run(args)

if __name__=='__main__': main()
