"""GPU pixel operations. Host work is limited to small coordinate/status arrays.

Textures use top-down image coordinates, except the shared 3D renderer texture.
There is no OpenCV/NumPy image-processing fallback in this module.
"""
from __future__ import annotations

import math
import numpy as np
import moderngl

VERTEX = '''#version 430
out vec2 uv;
void main(){vec2 p=vec2((gl_VertexID<<1)&2,gl_VertexID&2);uv=p;gl_Position=vec4(p*2.-1.,0,1);}
'''
DECODE_NV12 = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D luma,chroma;
uniform vec2 weights;uniform bool full_range;
void main(){float y=texture(luma,uv).r;vec2 c=texture(chroma,uv).rg-vec2(128./255.);
 if(!full_range){y=(y-16./255.)*255./219.;c*=255./224.;}
 float kr=weights.x,kb=weights.y,kg=1.-kr-kb;
 float r=y+2.*(1.-kr)*c.y,b=y+2.*(1.-kb)*c.x;
 color=vec4(clamp(vec3(r,(y-kr*r-kb*b)/kg,b),0.,1.),1);}
'''
ENCODE_NV12 = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D source;uniform ivec2 resolution;
void main(){ivec2 p=ivec2(gl_FragCoord.xy);float value;
 if(p.y<resolution.y){vec3 rgb=texelFetch(source,p,0).bgr;
  value=(16.+219.*dot(rgb,vec3(.2126,.7152,.0722)))/255.;
 }else{ivec2 at=ivec2((p.x/2)*2,(p.y-resolution.y)*2);vec3 rgb=vec3(0);
  for(int y=0;y<2;y++)for(int x=0;x<2;x++)rgb+=texelFetch(source,at+ivec2(x,y),0).bgr*.25;
  float l=dot(rgb,vec3(.2126,.7152,.0722));
  float c=(p.x%2==0)?(rgb.b-l)/(2.*(1.-.0722)):(rgb.r-l)/(2.*(1.-.2126));
  value=(128.+224.*c)/255.;}
 color=vec4(value,0,0,1);}
'''
TENSOR = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D source;
uniform vec4 box;uniform vec2 resolution;uniform vec3 mean,stddev;
uniform float rotation;uniform bool letterbox;
void main(){
 vec2 size=(box.zw-box.xy)*resolution;
 vec2 p=(uv-.5)*(letterbox?vec2(max(size.x,size.y)):size);
 p=mat2(cos(rotation),sin(rotation),-sin(rotation),cos(rotation))*p;
 vec2 at=(box.xy+box.zw)*.5+p/resolution;
 vec3 rgb=(any(lessThan(at,vec2(0)))||any(greaterThan(at,vec2(1))))?vec3(0):texture(source,at).rgb*255.;
 if(letterbox&&any(greaterThan(abs(p),size*.5)))rgb=vec3(0);
 color=vec4((rgb-mean)/stddev,1);
}
'''
SEGMENT = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D previous,patch_data;uniform vec4 box;
void main(){vec4 p=texture(previous,uv);
 if(all(greaterThanEqual(uv,box.xy))&&all(lessThanEqual(uv,box.zw))){
 vec3 v=texture(patch_data,(uv-box.xy)/(box.zw-box.xy)).rgb;
 p=vec4(max(p.rgb,v),max(p.a,v.r+v.g));}
 color=p;}
'''
COMPONENTS = '''#version 430
layout(local_size_x=16,local_size_y=16)in;
layout(std430,binding=0)buffer Parents{uint parent[];};
layout(std430,binding=1)buffer Stats{uint stats[];};
layout(std430,binding=2)buffer Changed{uint changed;};
uniform sampler2D probability;uniform int step;uniform float threshold;
const uint N=256u;const uint EMPTY=65536u;
uint root(uint k){for(int n=0;n<32&&parent[k]!=k&&parent[k]!=EMPTY;n++)k=parent[k];return k;}
void main(){uvec2 p=gl_GlobalInvocationID.xy;uint i=p.y*N+p.x;
 if(step==0){vec4 v=texelFetch(probability,ivec2(p),0);parent[i]=(v.a>=threshold)?i:EMPTY;return;}
 if(parent[i]==EMPTY)return;
 if(step==1){uint r=root(i);for(int y=-1;y<=0;y++)for(int x=-1;x<=1;x++){
  ivec2 q=ivec2(p)+ivec2(x,y);if(any(lessThan(q,ivec2(0)))||any(greaterThanEqual(q,ivec2(N))))continue;
  uint j=uint(q.y)*N+uint(q.x);if(parent[j]==EMPTY)continue;uint s=root(j);
  if(r!=s){uint old=atomicMin(parent[max(r,s)],min(r,s));if(old>min(r,s))atomicOr(changed,1u);}}
  return;}
 if(step==2){parent[i]=root(i);return;}
 uint base=parent[i]*8u;
 atomicAdd(stats[base],1u);atomicAdd(stats[base+1u],p.x);atomicAdd(stats[base+2u],p.y);
 atomicMax(stats[base+3u],N-p.x);atomicMax(stats[base+4u],N-p.y);
 atomicMax(stats[base+5u],p.x+1u);atomicMax(stats[base+6u],p.y+1u);
 float hair=0.;for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++)
 hair=max(hair,texelFetch(probability,clamp(ivec2(p)+ivec2(x,y),ivec2(0),ivec2(255)),0).r);
 if(hair>=max(.15,threshold*.5))atomicAdd(stats[base+7u],1u);
}
'''
FILTER = '''#version 430
layout(local_size_x=256)in;
layout(std430,binding=1)buffer Stats{uint stats[];};
layout(std430,binding=3)buffer Keep{uint keep[];};
layout(std430,binding=4)buffer Proposals{uint count;uint pad[3];vec4 boxes[];};
uniform bool require_hair,person_filter;uniform vec4 persons[10];uniform int person_count;
uniform float head_fraction;uniform vec2 resolution;const float N=256.;
void main(){uint i=gl_GlobalInvocationID.x;uint b=i*8u;float area=float(stats[b]);bool valid=area>=max(8.*N*N/(resolution.x*resolution.y),N*N*.00002);
 if(require_hair&&float(stats[b+7u])<max(3.,area*.02))valid=false;
 vec4 box=vec4(N-float(stats[b+3u]),N-float(stats[b+4u]),stats[b+5u],stats[b+6u])/N;
 vec2 center=vec2(stats[b+1u],stats[b+2u])/max(area,1.)/N;
 if(person_filter){bool match=false;for(int n=0;n<person_count;n++){
 vec4 p=persons[n];vec2 size=box.zw-box.xy;
 if(center.x>=p.x&&center.x<p.z&&center.y>=p.y&&center.y<=p.y+(p.w-p.y)*head_fraction
 &&size.x>=max(3./resolution.x,(p.z-p.x)*.12)&&size.y>=max(3./resolution.y,(p.w-p.y)*.045))match=true;}
 valid=valid&&match;}
 keep[i]=valid?1u:0u;if(valid){uint index=atomicAdd(count,1u);if(index<128u)boxes[index]=box;}}
'''
PAINT = '''#version 430
in vec2 uv;out vec4 color;
layout(std430,binding=0)buffer Parents{uint parent[];};
layout(std430,binding=1)buffer Stats{uint stats[];};
layout(std430,binding=3)buffer Keep{uint keep[];};
layout(std430,binding=5)buffer Counts{uint automatic_pixels,manual_pixels,covered_pixels;};
uniform vec4 persons[10],manual[64],geometry;uniform int person_count,manual_count;
uniform bool person_filter,has_geometry;uniform float padding;uniform vec2 resolution;
bool ellipse(vec4 b){vec2 q=(uv-(b.xy+b.zw)*.5)/max((b.zw-b.xy)*.5,vec2(.00001));return dot(q,q)<=1.;}
bool allowed(){if(!person_filter)return true;for(int k=0;k<person_count;k++)
 if(all(greaterThanEqual(uv,persons[k].xy))&&all(lessThanEqual(uv,persons[k].zw)))return true;return false;}
void main(){ivec2 p=clamp(ivec2(uv*256.),ivec2(0),ivec2(255));uint label=parent[p.y*256+p.x];
 bool found=label<65536u&&keep[label]>0u;
 if(!found){int r=int(ceil(256.*padding));for(int y=-r;y<=r&&!found;y++)for(int x=-r;x<=r&&!found;x++){
 ivec2 q=p+ivec2(x,y);if(any(lessThan(q,ivec2(0)))||any(greaterThan(q,ivec2(255))))continue;
 uint l=parent[q.y*256+q.x];if(l>=65536u||keep[l]==0u)continue;uint b=l*8u;
 vec2 size=vec2(stats[b+5u]+stats[b+3u]-256u,stats[b+6u]+stats[b+4u]-256u)/256.*resolution;
 float radius=round(min(size.x,size.y)*padding);vec2 delta=vec2(x,y)/256.*resolution;
 if(dot(delta,delta)<=radius*radius){label=l;found=true;}}}
 vec4 box=geometry;bool automatic=allowed()&&(found||(has_geometry&&ellipse(geometry)));
 if(found){uint b=label*8u;box=vec4(256.-float(stats[b+3u]),256.-float(stats[b+4u]),stats[b+5u],stats[b+6u])/256.;}
 bool user=false;for(int k=0;k<manual_count;k++)if(ellipse(manual[k])){box=manual[k];user=true;}
 bool masked=automatic||user;
 if(automatic)atomicAdd(automatic_pixels,1u);if(user)atomicAdd(manual_pixels,1u);if(masked)atomicAdd(covered_pixels,1u);
 vec2 q=(uv-(box.xy+box.zw)*.5)/max((box.zw-box.xy)*.55,vec2(.00001));
 float z=sqrt(max(0.,1.-dot(q,q)));float light=clamp(.62+.3*z-.09*q.x-.09*q.y,.45,1.);
 color=masked?vec4(vec3(252,248,240)/255.*light,1):vec4(0);
}
'''
COMPOSITE = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D source,material,mesh,probability;
 uniform bool face_only,has_mesh;uniform float brow;
void main(){vec3 original=texture(source,uv).rgb;vec4 base=texture(material,uv);
 vec4 face=has_mesh?texture(mesh,vec2(uv.x,1.-uv.y)):vec4(0);float a=face.a;
 if(face_only){vec3 semantic=texture(probability,uv).rgb;
  float protect=max(clamp((semantic.r-.12)/.55,0.,1.),clamp((semantic.b-.55)/.4,0.,1.));
  if(uv.y<brow){vec2 pixel=1./vec2(textureSize(source,0));float blur=0.,hair_near=0.;
   for(int y=-1;y<=1;y++)for(int x=-1;x<=1;x++){
    blur+=dot(texture(source,uv+vec2(x,y)*pixel*4.).rgb,vec3(.299,.587,.114))/9.;
    hair_near=max(hair_near,texture(probability,uv+vec2(x,y)*pixel*6.).r);}
   if(hair_near>.08)protect=max(protect,clamp((blur-dot(original,vec3(.299,.587,.114))-.02)*7.,0.,1.));}
  a*=1.-protect;color=vec4(mix(original,face.rgb/max(face.a,1./255.),a).bgr,1);return;}
 vec3 painted=mix(original,base.rgb/max(base.a,1./255.),base.a);a*=base.a;
 color=vec4(mix(painted,face.rgb/max(face.a,1./255.),a).bgr,1);}
'''
MANUAL = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D previous;uniform vec4 boxes[64];uniform int count;
layout(std430,binding=5)buffer Counts{uint automatic_pixels,manual_pixels,covered_pixels;};
void main(){color=texture(previous,uv);bool found=false;
 for(int k=0;k<count;k++){vec4 b=boxes[k];vec2 q=(uv-(b.xy+b.zw)*.5)/max((b.zw-b.xy)*.5,vec2(.00001));
 if(dot(q,q)<=1.){float z=sqrt(max(0.,1.-dot(q,q)/1.21));float light=clamp(.62+.3*z-.09*q.x/1.1-.09*q.y/1.1,.45,1.);
 color=vec4(vec3(252,248,240)/255.*light,1);found=true;}}
 if(found)atomicAdd(manual_pixels,1u);if(color.a>.5)atomicAdd(covered_pixels,1u);}
'''
FLOW = '''#version 430
layout(local_size_x=1)in;uniform sampler2D previous,current,material;uniform bool backward;
layout(std430,binding=6)buffer Costs{vec4 costs[];};
void main(){uint k=gl_GlobalInvocationID.x;ivec2 offset=ivec2(int(k)%13-6,int(k)/13-6);
 float sum=0.,sum2=0.,error=0.,count=0.,cut=0.;
 for(int y=0;y<48;y++)for(int x=0;x<64;x++){
 ivec2 p=ivec2(x,y),q=p+offset;float a=texelFetch(previous,p,0).r,b=texelFetch(current,p,0).r;
 cut+=abs(a-b);if(texture(material,(vec2(p)+.5)/vec2(64,48)).a<.5)continue;
 if(any(lessThan(q,ivec2(0)))||any(greaterThanEqual(q,ivec2(64,48))))continue;
 float target=backward?texelFetch(previous,q,0).r:texelFetch(current,q,0).r;
 float value=backward?b:a;sum+=value;sum2+=value*value;error+=abs(value-target);count++;}
 costs[k]=vec4(error/max(count,1.),sum2/max(count,1.)-pow(sum/max(count,1.),2.),count,cut/3072.);}
'''
WARP = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D previous;uniform vec2 shift;
void main(){vec2 p=uv-shift;color=any(lessThan(p,vec2(0)))||any(greaterThan(p,vec2(1)))?vec4(0):texture(previous,p);}
'''
GRAY = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D source;
void main(){float g=dot(texture(source,uv).rgb,vec3(.299,.587,.114));color=vec4(g,g,g,1);}
'''
CLIP = '''#version 430
in vec2 uv;out vec4 color;uniform sampler2D previous;uniform vec4 persons[10];uniform int count;
layout(std430,binding=5)buffer Counts{uint automatic_pixels,manual_pixels,covered_pixels;};
void main(){bool allowed=false;for(int k=0;k<count;k++)
 if(all(greaterThanEqual(uv,persons[k].xy))&&all(lessThanEqual(uv,persons[k].zw)))allowed=true;
 color=allowed?texture(previous,uv):vec4(0);if(color.a>.5)atomicAdd(covered_pixels,1u);}
'''


class GPUFrameOps:
    def __init__(self, width, height):
        self.w, self.h = width, height
        self.ctx = moderngl.create_standalone_context(require=430)
        if 'intel' not in self.ctx.info['GL_RENDERER'].lower():
            self.ctx.release()
            raise RuntimeError('This GPU backend requires a hardware Intel OpenGL renderer; software/non-Intel renderers are not enabled.')
        self.programs = {}
        self.targets = {}
        self.source = self.ctx.texture((width, height), 4)
        self.source.swizzle = 'BGRA'
        self.source.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.source.repeat_x = self.source.repeat_y = False
        self.bgra_source = self.source
        self.yuv_planes = None
        self.patch = self.ctx.texture((256, 256), 3, dtype='f4')
        self.patch.repeat_x = self.patch.repeat_y = False
        self.buffers = {0: self.ctx.buffer(reserve=65536*4), 1: self.ctx.buffer(reserve=65536*8*4),
                        2: self.ctx.buffer(reserve=4), 3: self.ctx.buffer(reserve=65536*4),
                        4: self.ctx.buffer(reserve=16+128*16), 5: self.ctx.buffer(reserve=12),
                        6: self.ctx.buffer(reserve=169*16)}
        self.component = self.ctx.compute_shader(COMPONENTS)
        self.filter = self.ctx.compute_shader(FILTER)
        self.flow = self.ctx.compute_shader(FLOW)
        self.last_observed_ms = None
        self.have_previous = False

    def target(self, name, size, dtype='f4'):
        key = (name, size, dtype)
        if key not in self.targets:
            texture = self.ctx.texture(size, 4, dtype=dtype)
            texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
            texture.repeat_x = texture.repeat_y = False
            self.targets[key] = (texture, self.ctx.framebuffer([texture]))
        return self.targets[key]

    def draw(self, name, shader, size, textures, values=None, dtype='f4'):
        if name not in self.programs:
            program = self.ctx.program(vertex_shader=VERTEX, fragment_shader=shader)
            self.programs[name] = (program, self.ctx.vertex_array(program, []))
        program, vao = self.programs[name]
        texture, framebuffer = self.target(name, size, dtype)
        framebuffer.use()
        self.ctx.disable(moderngl.DEPTH_TEST)
        for unit, (key, value) in enumerate(textures.items()):
            if key in program:
                value.use(unit)
                program[key].value = unit
        for key, value in (values or {}).items():
            if key not in program:
                continue
            if isinstance(value, np.ndarray):
                program[key].write(value.astype('f4').tobytes())
            else:
                program[key].value = value
        vao.render(moderngl.TRIANGLES, vertices=3)
        self.ctx.memory_barrier()
        return texture

    def upload(self, frame):
        self.source = self.bgra_source
        self.source.write(frame)
        self.reset_probability()

    def upload_nv12(self, frame, weights=(.299, .114), full_range=False):
        if self.w % 2 or self.h % 2 or len(frame) != self.w*self.h*3//2:
            raise ValueError('NV12 requires a complete frame with even dimensions.')
        if self.yuv_planes is None:
            self.yuv_planes = (self.ctx.texture((self.w, self.h), 1),
                               self.ctx.texture((self.w//2, self.h//2), 2))
            for texture in self.yuv_planes:
                texture.repeat_x = texture.repeat_y = False
                texture.filter = (moderngl.LINEAR, moderngl.LINEAR)
        luma, chroma = self.yuv_planes
        view = memoryview(frame)
        luma.write(view[:self.w*self.h], alignment=1)
        chroma.write(view[self.w*self.h:], alignment=1)
        self.source = self.draw('decode-nv12', DECODE_NV12, (self.w, self.h),
                                {'luma': luma, 'chroma': chroma},
                                {'weights': weights, 'full_range': full_range}, 'f1')
        self.reset_probability()

    def reset_probability(self):
        _, framebuffer = self.target('probability-a', (256, 256))
        framebuffer.clear()
        self.probability = self.targets[('probability-a', (256, 256), 'f4')][0]

    def tensor(self, shape, box, rotation, mean, stddev, letterbox):
        h, w = shape
        texture = self.draw('tensor', TENSOR, (w, h), {'source': self.source},
                            {'box': tuple(box), 'resolution': (self.w, self.h), 'rotation': rotation,
                             'mean': tuple(mean), 'stddev': tuple(stddev), 'letterbox': letterbox})
        framebuffer = self.targets[('tensor', (w, h), 'f4')][1]
        return np.frombuffer(framebuffer.read(components=3, dtype='f4', alignment=1), np.float32).reshape(1, h, w, 3)

    def unletterbox(self, coordinates, box, points=False):
        result = np.array(coordinates, copy=True)
        x0, y0, x1, y1 = box
        size = np.array([(x1-x0)*self.w, (y1-y0)*self.h], np.float32)
        ratio = max(size)/size
        center = np.array([(x0+x1)/2, (y0+y1)/2], np.float32)
        if points:
            return center+(result-.5)*ratio*[x1-x0, y1-y0]
        result[:, :2] = center+(result[:, :2]-.5)*ratio*[x1-x0, y1-y0]
        result[:, 2:] = center+(result[:, 2:]-.5)*ratio*[x1-x0, y1-y0]
        return result

    def add_segmentation(self, pixels, box=(0, 0, 1, 1)):
        self.patch.write(np.ascontiguousarray(pixels).tobytes())
        name = 'probability-b' if self.probability is self.target('probability-a', (256, 256))[0] else 'probability-a'
        self.probability = self.draw(name, SEGMENT, (256, 256), {'previous': self.probability, 'patch_data': self.patch}, {'box': tuple(box)})

    def components(self, options, persons):
        for index, buffer in self.buffers.items():
            buffer.bind_to_storage_buffer(index)
        self.buffers[1].clear()
        self.component['probability'].value = 0
        self.probability.use(0)
        self.component['threshold'].value = options.segmentation_confidence
        self.component['step'].value = 0
        self.component.run(16, 16)
        self.ctx.memory_barrier()
        for attempt in range(32):
            self.buffers[2].clear()
            self.component['step'].value = 1
            self.component.run(16, 16)
            self.ctx.memory_barrier()
            self.component['step'].value = 2
            self.component.run(16, 16)
            self.ctx.memory_barrier()
            if int.from_bytes(self.buffers[2].read(), 'little') == 0:
                break
        else:
            raise RuntimeError('GPU component labeling did not converge; no CPU fallback was used.')
        self.component['step'].value = 3
        self.component.run(16, 16)
        self.ctx.memory_barrier()
        self.buffers[4].clear()
        self.filter['require_hair'].value = options.segmentation_requires_hair
        self.filter['person_filter'].value = options.person_filter
        self.filter['head_fraction'].value = options.segmentation_head_fraction
        self.filter['resolution'].value = (self.w, self.h)
        self.filter['person_count'].value = len(persons)
        boxes = np.zeros((10, 4), np.float32)
        if persons:
            boxes[:len(persons)] = persons
        self.filter['persons'].write(boxes.tobytes())
        self.filter.run(256)
        self.ctx.memory_barrier()
        raw = self.buffers[4].read()
        count = int.from_bytes(raw[:4], 'little')
        proposals = np.frombuffer(raw, np.float32, offset=16, count=min(count, 128)*4).reshape(-1, 4).copy()
        return proposals, boxes

    def paint(self, options, persons, geometry, manual):
        manual_values = np.zeros((64, 4), np.float32)
        if manual:
            manual_values[:len(manual)] = manual
        self.buffers[5].clear()
        self.material = self.draw('material', PAINT, (256, 256), {},
                                 {'persons': persons, 'person_count': int(np.count_nonzero(persons[:, 2] > persons[:, 0])),
                                  'person_filter': options.person_filter, 'manual': manual_values, 'manual_count': len(manual),
                                  'has_geometry': geometry is not None, 'geometry': tuple(geometry) if geometry is not None else (0, 0, 0, 0),
                                  'padding': options.mask_padding, 'resolution': (self.w, self.h)})
        counts = np.frombuffer(self.buffers[5].read(), np.uint32)
        return bool(counts[0]), bool(counts[1]), bool(counts[2])

    def empty_material(self):
        self.material, framebuffer = self.target('material', (256, 256))
        framebuffer.clear()

    def temporal(self, timestamp_ms, hold_seconds, observed, persons=None):
        current = self.draw('gray-current', GRAY, (64, 48), {'source': self.source})
        bridged = cut = False
        if self.have_previous:
            self.flow['previous'].value = 0
            self.flow['current'].value = 1
            self.flow['material'].value = 2
            self.previous_gray.use(0)
            current.use(1)
            self.previous_material.use(2)
            self.flow['backward'].value = False
            self.flow.run(169)
            self.ctx.memory_barrier()
            costs = np.frombuffer(self.buffers[6].read(), np.float32).reshape(169, 4)
            cut = float(costs[0, 3]) > 32/255
            if (not observed and not cut and self.last_observed_ms is not None
                    and 0 < timestamp_ms-self.last_observed_ms <= hold_seconds*1000):
                best = int(np.argmin(costs[:, 0]))
                offset = np.array([best % 13-6, best//13-6])
                score, variance, count, _ = costs[best]
                if score < 22/255 and variance > .0004 and count >= 6:
                    self.flow['backward'].value = True
                    self.flow.run(169)
                    self.ctx.memory_barrier()
                    backward = np.frombuffer(self.buffers[6].read(), np.float32).reshape(169, 4)
                    back = int(np.argmin(backward[:, 0]))
                    back_offset = np.array([back % 13-6, back//13-6])
                    if np.linalg.norm(offset+back_offset) < 1.5 and backward[back, 0] < 22/255:
                        self.material = self.draw('material-flow', WARP, (256, 256), {'previous': self.previous_material},
                                                  {'shift': tuple(offset/np.array([64., 48.]))})
                        bridged = True
                        if persons is not None:
                            values = np.zeros((10, 4), np.float32)
                            if persons:
                                values[:len(persons)] = persons
                            self.buffers[5].clear()
                            self.material = self.draw('material-clipped', CLIP, (256, 256), {'previous': self.material},
                                                      {'persons': values, 'count': len(persons)})
                            bridged = bool(np.frombuffer(self.buffers[5].read(), np.uint32)[2])
        if observed:
            self.last_observed_ms = timestamp_ms
        elif cut:
            self.last_observed_ms = None
        self.previous_gray = self.draw('gray-previous', WARP, (64, 48), {'previous': current}, {'shift': (0., 0.)})
        self.previous_material = self.draw('material-previous', WARP, (256, 256), {'previous': self.material}, {'shift': (0., 0.)})
        self.have_previous = True
        return bridged, cut

    def manual(self, boxes):
        if not boxes:
            return False
        values = np.zeros((64, 4), np.float32)
        values[:len(boxes)] = boxes
        self.buffers[5].clear()
        self.material = self.draw('material-manual', MANUAL, (256, 256), {'previous': self.material},
                                 {'boxes': values, 'count': len(boxes)})
        return bool(np.frombuffer(self.buffers[5].read(), np.uint32)[1])

    def composite(self, mesh=None, face_only=False, brow=0, nv12=False):
        textures = {'source': self.source, 'material': self.material, 'probability': self.probability,
                    'mesh': mesh if mesh is not None else self.material}
        output = self.draw('output', COMPOSITE, (self.w, self.h), textures,
                           {'has_mesh': mesh is not None, 'face_only': face_only, 'brow': brow}, 'f1')
        if nv12:
            size = (self.w, self.h*3//2)
            self.draw('encode-nv12', ENCODE_NV12, size, {'source': output},
                      {'resolution': (self.w, self.h)}, 'f1')
            return self.targets[('encode-nv12', size, 'f1')][1].read(components=1, alignment=1)
        return self.targets[('output', (self.w, self.h), 'f1')][1].read(components=4, alignment=1)

    def close(self):
        for program, vao in self.programs.values():
            vao.release()
            program.release()
        for texture, framebuffer in self.targets.values():
            framebuffer.release()
            texture.release()
        for buffer in self.buffers.values():
            buffer.release()
        self.component.release()
        self.filter.release()
        self.flow.release()
        self.patch.release()
        self.bgra_source.release()
        for texture in self.yuv_planes or ():
            texture.release()
        self.ctx.release()
