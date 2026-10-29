"""Greedy detector NMS on OpenGL, including score filtering and sorting.

DirectML cannot execute ONNX's dynamically sized NMS output without CPU nodes.
Keep fixed model outputs on DirectML and this final stage on the same GPU's GL
compute queue. The host only transports tensors and gathers sparse coordinates.
"""
import numpy as np

SUPPRESS = '''#version 430
layout(local_size_x=256) in;
layout(std430,binding=0) readonly buffer Boxes {vec4 boxes[];};
layout(std430,binding=1) buffer Scores {float scores[];};
layout(std430,binding=2) buffer Result {uint count; uint selected[];};
uniform uint length,limit; uniform float threshold;
shared float best[256]; shared uint indices[256]; shared uint done;
float overlap(vec4 a,vec4 b){vec2 v=max(vec2(0),min(a.zw,b.zw)-max(a.xy,b.xy));
 float intersection=v.x*v.y;vec2 sa=max(vec2(0),a.zw-a.xy),sb=max(vec2(0),b.zw-b.xy);
 float area=sa.x*sa.y+sb.x*sb.y-intersection;return area>0?intersection/area:0;}
void main(){uint lane=gl_LocalInvocationID.x;
 if(lane==0){count=0;done=0;}barrier();
 for(uint rank=0;rank<limit;rank++){
  float value=-1;uint at=0xffffffffu;
  for(uint i=lane;i<length;i+=256){float s=scores[i];vec4 b=boxes[i];
   if(isnan(s)||isinf(s)||any(isnan(b))||any(isinf(b))||s<threshold||s<=0)s=-1;
   if(rank>0&&(i==selected[rank-1]||overlap(b,boxes[selected[rank-1]])>.3))s=-1;
   scores[i]=s;if(s>value||(s==value&&i<at)){value=s;at=i;}}
  best[lane]=value;indices[lane]=at;barrier();
  for(uint step=128;step>0;step/=2){if(lane<step){uint j=lane+step;
   if(best[j]>best[lane]||(best[j]==best[lane]&&indices[j]<indices[lane])){
    best[lane]=best[j];indices[lane]=indices[j];}}barrier();}
  if(lane==0){if(best[0]<0)done=1;else{selected[rank]=indices[0];count=rank+1;}}
  memoryBarrierBuffer();barrier();if(done==1)break;
 }
}
'''


class GPUDetectionFilter:
    def __init__(self, context):
        self.ctx = context
        self.program = context.compute_shader(SUPPRESS)

    def run(self, boxes, scores, limit, threshold, points=None):
        if boxes.ndim != 3 or boxes.shape[0] != 1 or boxes.shape[2] != 4 or scores.shape != boxes.shape[:2]:
            raise ValueError('Invalid detector output dimensions.')
        if not 1 <= limit <= 16:
            raise ValueError('Invalid detection limit.')
        if not boxes.shape[1]:
            selected = np.empty(0, dtype=np.uint32)
        else:
            buffers = []
            try:
                buffers.append(self.ctx.buffer(np.asarray(boxes, dtype=np.float32).tobytes()))
                buffers.append(self.ctx.buffer(np.asarray(scores, dtype=np.float32).tobytes()))
                buffers.append(self.ctx.buffer(reserve=(limit+1)*4))
                for binding, buffer in enumerate(buffers):
                    buffer.bind_to_storage_buffer(binding)
                self.program['length'] = boxes.shape[1]
                self.program['limit'] = limit
                self.program['threshold'] = threshold
                self.program.run()
                self.ctx.memory_barrier()
                result = np.frombuffer(buffers[2].read(), np.uint32)
                if result[0] > limit:
                    raise RuntimeError('GPU detection filter returned invalid count.')
                selected = result[1:1+int(result[0])]
                if np.any(selected >= boxes.shape[1]):
                    raise RuntimeError('GPU detection filter returned invalid indices.')
            finally:
                for buffer in buffers:
                    buffer.release()
        result = [boxes[0, selected], scores[0, selected]]
        if points is not None:
            result.append(points[0, selected])
        return result

    def close(self):
        self.program.release()
