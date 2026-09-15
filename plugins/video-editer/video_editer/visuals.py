"""Code-native graphic templates and evolving transition masks, not model output."""
from contextlib import contextmanager
import math
import os
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageChops

from . import animation, processes

TRANSITIONS = {
    'summer_liquid_flow': {'season': 'summer', 'duration': .80, 'intent': '轻薄、清凉面料或流动裙摆；带白色浪头的液体曲线从左向右溢出。', 'avoid': '冬季保暖、厚重材质', 'coverage': '逐步覆盖全画面；避免同时放关键信息'},
    'summer_bubble_reveal': {'season': 'summer', 'duration': .80, 'intent': '清新配色、透气卖点；多个圆形气泡分别膨胀并融合，揭开下一镜头。', 'avoid': '严肃或低调质感', 'coverage': '多处局部圆形扩散至全屏'},
    'spring_petal_sweep': {'season': 'spring', 'duration': .88, 'intent': '春装、花色、柔和新品亮相；花瓣状轮廓由角落舒展。', 'avoid': '需要极短硬切的口播', 'coverage': '左下角绽放至全屏'},
    'winter_frost_grow': {'season': 'winter', 'duration': .88, 'intent': '针织、毛呢、保暖细节；锯齿冰晶边缘从四周向内生长。', 'avoid': '水感、泳装、炎热场景', 'coverage': '边缘冰霜逐步合拢，不使用水波'},
    'winter_snow_curtain': {'season': 'winter', 'duration': .80, 'intent': '冬装换搭、雪景氛围；分层雪团下落并形成遮盖帘幕。', 'avoid': '春夏清凉商品', 'coverage': '自上而下的不规则雪幕'},
    'graphic_ink_sweep': {'season': 'any', 'duration': .64, 'intent': '跨景别或卖点段落切换；斜向多笔刷条纹依次划开下一画面。', 'avoid': '安静长镜头', 'coverage': '斜向笔刷覆盖全屏'},
}
CALLOUTS = {
    'burst_pop': {'intent': '单个强卖点或情绪重音；漫画爆炸轮廓配粗字', 'avoid': '连续长句、安静材质说明', 'max_chars': 10},
    'minimal_label': {'intent': '材质、版型、工艺等细节；低遮挡圆角标签', 'avoid': '需要强烈促销爆点', 'max_chars': 10},
    'ribbon_tag': {'intent': '新品、搭配建议、段落小标题；折角丝带', 'avoid': '逐字字幕或较长台词', 'max_chars': 10},
    'frost_glass': {'intent': '冬装、保暖或冷色质感；半透明冰晶标签', 'avoid': '夏季水感场景', 'max_chars': 10},
}


def transition_frame(name, width, height, p):
    """Return next-shot mask and coloured moving edge; p=0/1 are exact endpoints."""
    if name not in TRANSITIONS:
        raise ValueError('Unknown procedural transition')
    y, x = np.mgrid[0:height, 0:width].astype(np.float32)
    x /= max(1, width-1)
    y /= max(1, height-1)
    if name == 'summer_liquid_flow':
        boundary = 1.42*p-.21 + .13*np.sin(y*9+p*8) + .055*np.sin(y*21-p*7)
        distance = boundary-x
        color = (228, 253, 255)
    elif name == 'summer_bubble_reveal':
        threshold = np.full_like(x, 5)
        for cx, cy, delay in ((.05,.78,0),(.46,.26,.08),(.86,.66,.17),(.13,.06,.22),(.63,.94,.13)):
            threshold = np.minimum(threshold, delay+np.hypot(x-cx,y-cy))
        distance = p-threshold/threshold.max()
        color = (198, 249, 255)
    elif name == 'spring_petal_sweep':
        xx, yy = x-.02, y-.98
        angle = np.arctan2(yy,xx)
        radius = p*1.95*(1+.13*np.cos(angle*7+p*2))
        distance = radius-np.hypot(xx,yy)
        color = (255, 224, 235)
    elif name == 'winter_frost_grow':
        edge = np.minimum.reduce([x,1-x,y,1-y])
        crystal = .038*np.abs(np.sin(x*43+y*17))+.026*np.abs(np.sin(y*57-x*25))
        distance = p*.66-.065+crystal-edge
        color = (222, 235, 255)
    elif name == 'winter_snow_curtain':
        boundary = p*1.50-.25+.075*np.cos(x*26)+.04*np.cos(x*51+p*4)
        distance = boundary-y
        for index in range(12):
            cx = ((index*37)%101)/100
            cy = (p*1.4+(index%4)*.12-.5)
            distance = np.maximum(distance, .015+.045*p-np.hypot((x-cx)*.7,y-cy))
        color = (249, 250, 255)
    else:
        band = np.floor(y*9)
        leading = p*1.65-.30+(band%3)*.08
        distance = leading-(x+.23*y+.018*np.sin(y*260))
        color = (255, 234, 116)
    mask = np.clip(distance*32000+128,0,255).astype(np.uint8)
    edge = np.clip(1-np.abs(distance)/.035,0,1)*245
    rgba = np.zeros((height,width,4), dtype=np.uint8)
    rgba[:,:,:3] = color
    rgba[:,:,3] = edge.astype(np.uint8)
    if p <= 0: mask[:]=0; rgba[:,:,3]=0
    if p >= 1: mask[:]=255; rgba[:,:,3]=0
    return Image.fromarray(mask), Image.fromarray(rgba)


def encode_frames(output, frames, size, pixel_format='rgba'):
    """Lossless local stream. Bounded memory, cancellation checked every frame."""
    from .engine import ffmpeg_bin
    output = Path(output)
    log = output.with_suffix(output.suffix+'.log')
    command = [ffmpeg_bin(), '-nostdin', '-y', '-f', 'rawvideo', '-pixel_format', pixel_format,
               '-video_size', f'{size[0]}x{size[1]}', '-framerate', '25', '-i', 'pipe:0',
               '-an', '-c:v', 'ffv1', '-pix_fmt', 'gray' if pixel_format == 'gray' else 'bgra', str(output)]
    with log.open('wb') as stderr:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=stderr,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            for frame in frames:
                processes.check()
                child.stdin.write(frame.tobytes())
            child.stdin.close()
            child.stdin = None
            child.wait(timeout=60)
            if child.returncode:
                raise RuntimeError('Frame encoder failed: '+str(log))
        except BaseException:
            processes.stop(child)
            raise


def transition_assets(name, root, size, duration):
    count = max(2, round(duration*25))
    mask, edge = root/'transition-mask.mkv', root/'transition-edge.mkv'
    encode_frames(mask, (transition_frame(name,*size,i/(count-1))[0] for i in range(count)), size, 'gray')
    encode_frames(edge, (transition_frame(name,*size,i/(count-1))[1] for i in range(count)), size)
    return mask, edge


def font_file():
    specified = os.environ.get('VIDEO_EDITER_FONT_FILE')
    if specified:
        if not Path(specified).is_file(): raise ValueError('VIDEO_EDITER_FONT_FILE does not exist')
        return specified
    candidates = [Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts'/'msyhbd.ttc',
                  Path('/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc'),
                  Path('/System/Library/Fonts/PingFang.ttc')]
    found = next((p for p in candidates if p.is_file()), None)
    if found is None:
        raise ValueError('Install a CJK font or explicitly set VIDEO_EDITER_FONT_FILE for procedural text')
    return str(found)


def callout_box(event, size):
    w,h = size
    bw = min(w*.43,h*.68)
    bh = bw*.43
    return (w*.025 if event['position']=='product_left' else w*.975-bw, h*.60, bw, bh)


def callout_image(event, size=(640,280)):
    """Draw editable text and ornament together, with measured safe-area layout."""
    w,h = size
    layer = Image.new('RGBA', size)
    d = ImageDraw.Draw(layer)
    template = event['template']
    theme = {'sunshine':'#FFD54A','coral':'#FF827D','mint':'#8AE3C0','electric_blue':'#5BCBFF','cream':'#FFF1D4'}[event.get('color_theme','sunshine')]
    if template == 'burst_pop':
        pts=[]
        for i in range(24):
            a=i*math.pi/12
            r=1 if i%2==0 else .80
            pts.append((w/2+math.cos(a)*w*.48*r,h/2+math.sin(a)*h*.46*r))
        d.polygon(pts,fill=theme,outline='#131927',width=6)
        fill,stroke = '#FFFFFF','#101725'
        box=(w*.16,h*.20,w*.84,h*.80)
    elif template == 'minimal_label':
        d.rounded_rectangle((4,12,w-4,h-12),radius=30,fill=(18,29,39,230),outline=theme,width=3)
        d.rounded_rectangle((20,40,29,h-40),radius=4,fill=theme)
        fill,stroke = '#FFFFFF',None
        box=(w*.09,h*.15,w*.93,h*.85)
    elif template == 'ribbon_tag':
        d.polygon([(8,32),(w-28,12),(w-8,h-35),(w-60,h-28),(w-55,h-6),(w-100,h-23),(25,h-8)],fill='#16243A')
        d.polygon([(4,15),(w-25,4),(w-5,h-50),(25,h-22)],fill=theme)
        fill,stroke = '#142338',None
        box=(w*.10,h*.14,w*.86,h*.70)
    elif template == 'frost_glass':
        d.rounded_rectangle((8,12,w-8,h-12),radius=35,fill=(230,245,255,224),outline=theme,width=4)
        for cx,cy in ((35,40),(w-37,h-38)):
            for a in range(0,180,60):
                dx,dy=22*math.cos(math.radians(a)),22*math.sin(math.radians(a))
                d.line((cx-dx,cy-dy,cx+dx,cy+dy),fill='#72AAD1',width=3)
        fill,stroke = '#254565',None
        box=(w*.13,h*.17,w*.87,h*.83)
    else:
        raise ValueError('Unknown procedural callout')
    text=event['text']
    if len(text)>6:
        split=(len(text)+1)//2
        text=text[:split]+'\n'+text[split:]
    font_path=font_file()
    for px in range(96,11,-1):
        font=ImageFont.truetype(font_path,px)
        bounds=d.multiline_textbbox((0,0),text,font=font,spacing=4,align='center',stroke_width=3 if stroke else 0)
        if bounds[2]-bounds[0] <= box[2]-box[0] and bounds[3]-bounds[1] <= box[3]-box[1]: break
    else:
        raise ValueError('Text does not fit template; shorten it explicitly')
    x=(box[0]+box[2]-(bounds[2]-bounds[0]))/2-bounds[0]
    y=(box[1]+box[3]-(bounds[3]-bounds[1]))/2-bounds[1]
    d.multiline_text((x,y),text,font=font,fill=fill,spacing=4,align='center',stroke_width=3 if stroke else 0,stroke_fill=stroke)
    return layer


def transformed(image, item, state, size):
    w,h=size
    bw,bh=max(1,round(item['width']*w)),max(1,round(item['height']*h))
    frame=image.resize((bw,bh),Image.Resampling.LANCZOS)
    if item.get('mask','none')!='none':
        mask=Image.new('L',(bw,bh)); d=ImageDraw.Draw(mask)
        if item['mask']=='circle': d.ellipse((0,0,bw-1,bh-1),fill=255)
        else: d.rounded_rectangle((0,0,bw-1,bh-1),radius=min(bw,bh)*.16,fill=255)
        frame.putalpha(ImageChops.multiply(frame.getchannel('A'),mask))
    factor=state.get('scale',1)
    frame=frame.resize((max(1,round(bw*factor)),max(1,round(bh*factor))),Image.Resampling.LANCZOS)
    frame=frame.rotate(-state.get('rotation',0),Image.Resampling.BICUBIC,expand=True)
    alpha=state.get('opacity',1)
    frame.putalpha(frame.getchannel('A').point(lambda v: round(v*alpha)))
    pos=(round(state['x']*w+bw/2-frame.width/2),round(state['y']*h+bh/2-frame.height/2))
    return frame,pos


@contextmanager
def decoded_frames(item, size):
    from .engine import ffmpeg_bin
    w,h=size
    cmd=[ffmpeg_bin(),'-nostdin','-v','error','-ss',str(item.get('source_start',0)),'-i',str(item['path']),
         '-vf',f'fps=25,scale={w}:{h}', '-an','-f','rawvideo','-pix_fmt','rgba','pipe:1']
    log=Path(item['_log'])
    with log.open('wb') as err:
        child=subprocess.Popen(cmd,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=err,
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        def frame():
            processes.check()
            raw=child.stdout.read(w*h*4)
            if len(raw)!=w*h*4: raise ValueError('Overlay video ended before requested event; inspect '+str(log))
            return Image.frombytes('RGBA',(w,h),raw)
        try: yield frame
        finally:
            processes.stop(child)
            child.stdout.close()


def render_layer(source, output, graphics=None, callouts=None):
    from contextlib import ExitStack
    from .engine import ffprobe,ffmpeg_bin
    info=ffprobe(source); size=(info['width'],info['height'])
    total=float(info['duration'])
    graphics,callouts=graphics or [],callouts or []
    layer=output.with_suffix('.layer.mkv')
    images={e['id']:callout_image(e) for e in callouts}
    with ExitStack() as stack:
        readers={}
        def frames():
            for index in range(math.ceil(total*25-1e-8)):
                processes.check(); at=index/25
                frame=Image.new('RGBA',size)
                for item in graphics:
                    if not item['start'] <= at < item['end']: continue
                    key=item['id']
                    if key not in readers:
                        if item['kind']=='image':
                            with Image.open(item['path']) as raw: image=raw.convert('RGBA')
                            readers[key]=lambda image=image:image
                        else:
                            small=(max(1,round(item['width']*size[0])),max(1,round(item['height']*size[1])))
                            readers[key]=stack.enter_context(decoded_frames({**item,'_log':str(output.parent/(key+'-decode.log'))},small))
                    stamp,pos=transformed(readers[key](),item,animation.value(item,at),size)
                    frame.alpha_composite(stamp,pos)
                for item in callouts:
                    if not item['start'] <= at < item['end']: continue
                    x,y,bw,bh=callout_box(item,size)
                    state={'x':x/size[0],'y':y/size[1],'scale':1,'opacity':1,'rotation':0}
                    p=min(1,(at-item['start'])/.20)
                    mode=item.get('entrance','pop')
                    if mode=='pop': state['scale']=.78+.22*animation.ease(p,'ease_out')
                    elif mode=='slide_up': state['y']+=(1-animation.ease(p,'ease_out'))*.03
                    elif mode=='fade': state['opacity']=p
                    stamp,pos=transformed(images[item['id']],{'width':bw/size[0],'height':bh/size[1]},state,size)
                    frame.alpha_composite(stamp,pos)
                yield frame
        encode_frames(layer,frames(),size)
    cmd=[ffmpeg_bin(),'-nostdin','-y','-i',str(source),'-i',str(layer),'-filter_complex_threads','1',
         '-filter_complex','[0:v][1:v]overlay=0:0:eof_action=pass:repeatlast=0[v]',
         '-map','[v]','-map','0:a?','-t',str(total),'-r','25','-c:v','libx264','-c:a','copy','-movflags','+faststart',str(output)]
    processes.run(cmd,capture_output=True,check=True,timeout=600)
    return output
