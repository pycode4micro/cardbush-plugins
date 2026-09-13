"""Build a retained synthetic, audible capability reel and inspectable frame sheets."""
import json
from pathlib import Path
import subprocess
import tempfile
import sys

from PIL import Image, ImageDraw, ImageFont
from video_editer import engine, visuals
from video_editer import mcp_server as s


def main():
    root=Path(tempfile.mkdtemp(prefix='video-editer-showcase-'))
    engine.PROJECTS=root/'data'/'projects'
    print('SHOWCASE_ROOT='+str(root),flush=True)
    pid=s.project_create('Atomic capabilities — synthetic demo')['project_id']
    s.canvas_configure(pid,720,1280)
    palette=[('#E8F3EF','#317669'),('#FCE5DC','#BE766B'),('#E8EEFB','#687FAD'),
             ('#F5EBD7','#AB8660'),('#DEEAF0','#627F92'),('#EEDFEF','#967A99'),('#F1EFDD','#9C9658')]
    font=ImageFont.truetype(visuals.font_file(),34)
    transitions=list(visuals.TRANSITIONS)
    for i,(bg,fg) in enumerate(palette):
        image=Image.new('RGB',(720,1280),bg); d=ImageDraw.Draw(image)
        for y in range(100,1180,90): d.line((30,y,690,y),fill='#FFFFFF',width=2)
        # Code-native garment silhouette, used only as a neutral test scene.
        d.polygon([(250,290),(190,330),(110,525),(200,565),(245,465),(230,1000),
                   (490,1000),(475,465),(520,565),(610,525),(530,330),(470,290),(420,320),(300,320)],fill=fg)
        d.arc((300,250,420,340),0,180,fill=bg,width=18)
        d.line((360,365,360,975),fill=bg,width=3)
        for y in range(390,925,90): d.ellipse((351,y,369,y+18),fill=bg)
        d.text((45,55),f'执行能力演示 · 场景 {i+1}',font=font,fill='#273D43')
        d.text((45,1190),'合成测试素材 · 非商品成片',font=font,fill='#273D43')
        path=root/f'scene-{i}.png'; image.save(path)
        asset=s.media_import(pid,str(path))['asset']['id']
        clip=s.clip_add(pid,asset,0,2.2,transition=transitions[i] if i<len(transitions) else 'none')['clip']['id']
        if i==0:
            s.clip_camera_set(pid,clip,{'start_zoom':1,'end_zoom':1.16,'start_x':.5,'end_x':.5,'start_y':.5,'end_y':.4,'easing':'ease_in_out'})
    timing=s.timeline_time_map(pid)
    total=timing['duration']
    for i,(template,text) in enumerate(zip(visuals.CALLOUTS,('漫画重点','细节标签','折角丝带','冰晶标签'))):
        start=i*2.5+.25
        s.callout_add(pid,text,start,start+1.25,'product_left' if i%2==0 else 'product_right',template,color_theme='electric_blue' if i==3 else 'sunshine')
    deco=Image.new('RGBA',(180,180)); d=ImageDraw.Draw(deco)
    d.rounded_rectangle((5,5,175,175),radius=28,fill='#FFFFFF',outline='#255BD2',width=8)
    d.polygon([(65,45),(135,90),(65,135)],fill='#255BD2')
    deco_path=root/'transform-symbol.png'; deco.save(deco_path)
    aid=s.media_import(pid,str(deco_path))['asset']['id']
    oid=s.overlay_add(pid,aid,.3,2.4,.72,.15,.16,.09)['overlay']['id']
    s.overlay_transform_set(pid,oid,[{'at':.3,'scale':.5,'rotation':-25,'opacity':0},
                                    {'at':.8,'scale':1.25,'rotation':20,'opacity':1,'easing':'ease_out'},
                                    {'at':1.4,'scale':1,'rotation':0,'easing':'ease_in_out'},
                                    {'at':2.4,'opacity':0,'x':.66,'y':.18,'easing':'ease_in'}],mask='rounded_rect')
    tone=root/'tone.wav'
    subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-f','lavfi','-i',f'sine=frequency=330:duration={total}',str(tone)],capture_output=True,check=True)
    aid=s.media_import(pid,str(tone))['asset']['id']
    s.audio_track_add(pid,aid,volume=.035,fade_in=.2,fade_out=.3)
    result=s.render_final(pid)
    fontsmall=ImageFont.truetype(visuals.font_file(),18)
    sheet=Image.new('RGB',(4*216,6*424),'#152029'); draw=ImageDraw.Draw(sheet)
    for row,clip in enumerate(timing['clips'][:-1]):
        overlap=clip['outgoing_overlap']; start=clip['end']-overlap
        for col,p in enumerate((.12,.36,.64,.88)):
            at=start+overlap*p
            out=root/f'frame-{row}-{col}.png'
            subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-ss',str(at),'-i',result['path'],'-frames:v','1',str(out)],capture_output=True,check=True)
            with Image.open(out) as image: sheet.paste(image.resize((216,384)),(col*216,row*424+40))
        draw.text((8,row*424+4),transitions[row],font=fontsmall,fill='#FFFFFF')
    sheet_path=root/'transition-frames.png'; sheet.save(sheet_path)
    cards=Image.new('RGB',(1280,560),'#17232A')
    for i,name in enumerate(visuals.CALLOUTS):
        stamp=visuals.callout_image({'template':name,'text':'轻盈自在有型','color_theme':'electric_blue' if name=='frost_glass' else 'sunshine'})
        cards.paste(stamp,((i%2)*640,(i//2)*280),stamp)
    cards_path=root/'callout-styles.png'; cards.save(cards_path)
    report={'render':result,'transition_frames':str(sheet_path),'callout_styles':str(cards_path),'synthetic':True,'model_api_called':False}
    (root/'showcase.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)


if __name__=='__main__': main()
