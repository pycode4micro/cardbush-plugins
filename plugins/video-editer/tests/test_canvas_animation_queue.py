"""Real frames, audible streams, malformed inputs and detached worker tests."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image, ImageDraw
from video_editer import engine, jobs, visuals, animation, processes, render_queue
from video_editer import mcp_server as s


def frame(path, at):
    info=engine.ffprobe(path)
    result=subprocess.run([engine.ffmpeg_bin(),'-nostdin','-v','error','-ss',str(at),'-i',str(path),
                           '-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],capture_output=True,check=True,timeout=30)
    return np.frombuffer(result.stdout,np.uint8).reshape(info['height'],info['width'],3)


class AdvancedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(tempfile.mkdtemp(prefix='video-editer-advanced-'))
        cls.red=cls.root/'red.mp4'; cls.blue=cls.root/'blue.mp4'; cls.still=cls.root/'grid.png'
        for path,color in ((cls.red,'red'),(cls.blue,'blue')):
            subprocess.run([engine.ffmpeg_bin(),'-nostdin','-y','-f','lavfi','-i',f'color={color}:s=320x320:r=25:d=3',
                            '-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-c:a','aac',str(path)],
                           capture_output=True,check=True,timeout=30)
        image=Image.new('RGB',(480,320),'#ffffff'); d=ImageDraw.Draw(image)
        d.rectangle((130,60,230,210),fill='#00ff00'); d.rectangle((340,110,410,270),fill='#0000ff')
        for x in range(0,480,40): d.line((x,0,x,320),fill='#555555',width=2)
        image.save(cls.still)
        print('\nADVANCED_REVIEW='+str(cls.root),flush=True)

    def setUp(self):
        self.patcher=patch.object(engine,'PROJECTS',self.root/'data'/'projects'); self.patcher.start()
        self.pid=s.project_create('Advanced execution')['project_id']
        s.canvas_configure(self.pid,320,320)
        self.a=s.media_import(self.pid,str(self.red))['asset']['id']
        self.b=s.media_import(self.pid,str(self.blue))['asset']['id']
        self.image=s.media_import(self.pid,str(self.still))['asset']['id']

    def tearDown(self):
        self.patcher.stop()

    def unchanged(self,call):
        path=engine.project_dir(self.pid)/'project.json'; before=path.read_bytes()
        with self.assertRaises((ValueError,TypeError)):
            call()
        self.assertEqual(before,path.read_bytes())

    def wait_job(self,job,states=('succeeded','failed','cancelled','interrupted'),timeout=60):
        until=time.monotonic()+timeout
        while time.monotonic()<until:
            current=s.render_job_status(self.pid,job)
            if current['status'] in states: return current
            time.sleep(.1)
        self.fail('Worker timeout '+str(s.render_job_status(self.pid,job)))

    def test_image_main_landscape_and_camera_motion(self):
        s.canvas_configure(self.pid,640,360,'cover')
        clip=s.clip_add(self.pid,self.image,0,1.4,transition='none')['clip']['id']
        s.clip_camera_set(self.pid,clip,{'start_zoom':1,'end_zoom':1.6,'start_x':0,'start_y':.5,'end_x':1,'end_y':.5,'easing':'ease_in_out'})
        result=s.render_final(self.pid)
        self.assertEqual((result['quality']['width'],result['quality']['height']),(640,360))
        self.assertTrue(result['has_audio'])
        first,last=frame(Path(result['path']),.04),frame(Path(result['path']),1.32)
        self.assertGreater(np.abs(first.astype(float)-last).mean(),30)
        self.unchanged(lambda:s.clip_split(self.pid,clip,.5))
        s.clip_camera_set(self.pid,clip,None)
        s.clip_split(self.pid,clip,.5)
        self.assertTrue(s.timeline_validate(self.pid)['valid'])

    def test_canvas_invalid_data_and_restore(self):
        for width,height,fit,bg in ((321,320,'contain','#000000'),(200,320,'cover','#000000'),(3840,3840,'contain','#000000'),(320,320,'stretch','#000000'),(320,320,'contain','red;movie=x')):
            self.unchanged(lambda:s.canvas_configure(self.pid,width,height,fit,bg))
        result=s.canvas_configure(self.pid,640,360)
        s.timeline_restore(self.pid,result['undo_snapshot'])
        self.assertEqual(s.timeline_get(self.pid)['timeline']['canvas']['width'],320)

    def test_camera_invalid_data_is_atomic(self):
        clip=s.clip_add(self.pid,self.a,0,1.5)['clip']['id']
        self.unchanged(lambda:s.clip_camera_set(self.pid,clip,{'zoom':2}))
        self.unchanged(lambda:s.clip_camera_set(self.pid,clip,{'start_zoom':float('nan'),'end_zoom':1,'start_x':0,'start_y':0,'end_x':1,'end_y':1,'easing':'linear'}))

    def test_transform_curves_and_bad_keys(self):
        s.clip_add(self.pid,self.a,0,2)
        oid=s.overlay_add(self.pid,self.image,.1,1.9,.25,.25,.3,.2)['overlay']['id']
        good=[{'at':.1,'scale':.6,'rotation':-25,'opacity':.2},
              {'at':1,'x':.4,'y':.4,'scale':1.3,'rotation':25,'opacity':1,'easing':'ease_out'}]
        result=s.overlay_transform_set(self.pid,oid,good,'rounded_rect')
        value=animation.value(result['overlay'],.55)
        self.assertAlmostEqual(value['opacity'],.8)
        for keys in ([{'at':.1,'scale':float('nan')}],[{'at':.1,'scale':10}],[{'at':.1,'easing':'random'}],[{'at':.1,'x':.9}],[{'at':3}],[{'at':.2},{'at':.2}],[{'at':.5,'typo':1}]):
            self.unchanged(lambda:s.overlay_transform_set(self.pid,oid,keys))
        bad_mid=[{'at':.1,'x':0,'y':0,'rotation':0},{'at':1.8,'rotation':180}]
        self.unchanged(lambda:s.overlay_transform_set(self.pid,oid,bad_mid))

    def test_transform_real_pixels_and_alpha(self):
        s.clip_add(self.pid,self.a,0,1.6)
        oid=s.overlay_add(self.pid,self.image,.2,1.3,.2,.2,.3,.3)['overlay']['id']
        s.overlay_transform_set(self.pid,oid,[{'at':.2,'opacity':0,'scale':.5,'rotation':-20},
                                            {'at':.6,'opacity':1,'scale':1.3,'rotation':30,'easing':'ease_out'},
                                            {'at':1.2,'x':.5,'y':.5,'opacity':.5,'scale':.8,'rotation':0}],mask='circle')
        result=s.render_final(self.pid); path=Path(result['path'])
        baseline=frame(path,.08)
        first=frame(path,.2); middle=frame(path,.64); last=frame(path,1.4)
        self.assertLess(np.abs(baseline.astype(float)-first).mean(),2)
        self.assertGreater(np.abs(baseline.astype(float)-middle).mean(),4)
        self.assertLess(np.abs(baseline.astype(float)-last).mean(),2)

    def test_transform_source_binding_retimes_full_keys(self):
        clip=s.clip_add(self.pid,self.a,0,2)['clip']['id']
        oid=s.overlay_add(self.pid,self.image,.2,1.8,.2,.2,.2,.2)['overlay']['id']
        s.overlay_transform_set(self.pid,oid,[{'at':.2,'scale':.5,'opacity':0},{'at':1.2,'scale':1,'opacity':1,'easing':'ease_in'}])
        s.event_bind(self.pid,oid,clip,.2,1.8)
        s.clip_update(self.pid,clip,playback_speed=1.25)
        item=s.timeline_get(self.pid)['timeline']['tracks']['overlays'][0]
        self.assertAlmostEqual(item['keyframes'][1]['at'],.96)
        self.assertEqual(item['keyframes'][1]['easing'],'ease_in')
        self.unchanged(lambda:s.overlay_transform_set(self.pid,oid,[]))

    def test_every_procedural_mask_has_distinct_evolving_geometry(self):
        middle=[]
        for name in visuals.TRANSITIONS:
            hashes=[]
            for i in range(21):
                mask,edge=visuals.transition_frame(name,160,240,i/20)
                data=np.asarray(mask)
                hashes.append(hashlib.sha256(data.tobytes()).hexdigest())
                if i==0: self.assertEqual(int(data.max()),0)
                if i==20: self.assertEqual(int(data.min()),255)
                if i==10: middle.append(hashes[-1]); self.assertGreater(np.std(data),10)
            self.assertGreater(len(set(hashes)),15,name)
        self.assertEqual(len(set(middle)),len(visuals.TRANSITIONS))

    def test_all_procedural_transitions_reveal_actual_next_video(self):
        for name in visuals.TRANSITIONS:
            with self.subTest(name=name):
                pid=s.project_create(name)['project_id']; s.canvas_configure(pid,320,320)
                a=s.media_import(pid,str(self.red))['asset']['id']; b=s.media_import(pid,str(self.blue))['asset']['id']
                s.clip_add(pid,a,0,1.4,transition=name); s.clip_add(pid,b,0,1.4,transition='none')
                result=s.render_final(pid); path=Path(result['path'])
                overlap=engine.transition_overlap_seconds(name); total=2.8-overlap
                self.assertAlmostEqual(result['quality']['duration'],total,delta=.045)
                first,last=frame(path,.08),frame(path,total-.12)
                self.assertGreater(first[:,:,0].mean(),200); self.assertGreater(last[:,:,2].mean(),200)
                middle=frame(path,1.4-overlap/2)
                self.assertGreater(((middle[:,:,0]>150)&(middle[:,:,2]<80)).sum(),500)
                self.assertGreater(((middle[:,:,2]>150)&(middle[:,:,0]<80)).sum(),500)

    def test_distinct_callout_art_and_real_render(self):
        hashes=set()
        for index,name in enumerate(visuals.CALLOUTS):
            stamp=visuals.callout_image({'text':'轻盈自在有型','template':name,'color_theme':'sunshine'})
            stamp.save(self.root/(name+'.png')); hashes.add(hashlib.sha256(stamp.tobytes()).hexdigest())
        self.assertEqual(len(hashes),4)
        s.canvas_configure(self.pid,640,360)
        s.clip_add(self.pid,self.b,0,2)
        for i,name in enumerate(visuals.CALLOUTS):
            s.callout_add(self.pid,'轻盈自在',i*.4,(i+1)*.4,'product_left',name)
        output=s.render_final(self.pid)
        image=frame(Path(output['path']),.2)
        self.assertGreater(np.std(image[210:290,:230]),30)
        self.unchanged(lambda:s.callout_add(self.pid,'一'*11,0,1,'product_left','burst_pop'))

    def test_queue_runs_snapshot_and_fifo(self):
        s.clip_add(self.pid,self.a,0,1.4)
        with patch.object(render_queue,'start_worker',return_value={}):
            one=s.render_submit(self.pid)
            s.callout_add(self.pid,'轻盈',.2,1,'product_left','minimal_label')
            two=s.render_submit(self.pid,True)
        self.assertLess(one['revision'],two['revision'])
        self.assertEqual(s.render_job_status(self.pid,one['job_id'])['status'],'queued')
        s.render_queue_start()
        first=self.wait_job(one['job_id']); second=self.wait_job(two['job_id'])
        self.assertEqual(first['status'],'succeeded',first)
        self.assertEqual(second['status'],'succeeded',second)
        self.assertLessEqual(first['finished_at'],second['started_at'])
        self.assertEqual(first['result']['revision'],one['revision'])
        self.assertFalse(first['result']['visual_effects_applied'])
        self.assertTrue(second['result']['visual_effects_applied'])

    def test_queued_cancel_and_invalid_job_preserve_files(self):
        s.clip_add(self.pid,self.a,0,1.4)
        with patch.object(render_queue,'start_worker',return_value={}):
            job=s.render_submit(self.pid)['job_id']
        path=jobs.folder(self.pid,job); before=(path/'input.json').read_bytes()
        with self.assertRaises(ValueError): s.render_retry(self.pid,job)
        with self.assertRaises(ValueError): s.render_cancel(self.pid,'../escape')
        cancelled=s.render_cancel(self.pid,job)
        self.assertEqual(cancelled['status'],'cancelled')
        self.assertEqual(before,(path/'input.json').read_bytes())
        self.assertFalse(s.render_cancel(self.pid,job)['cancel_accepted'])
        result=s.render_retry(self.pid,job)
        self.assertEqual(result['status'],'succeeded')
        self.assertNotEqual(result['job_id'],job)

    def test_running_cancel_stops_worker_job(self):
        s.canvas_configure(self.pid,1080,1920)
        s.clip_add(self.pid,self.image,0,40)
        job=s.render_submit(self.pid)['job_id']
        state=self.wait_job(job,('running','succeeded','failed'))
        self.assertEqual(state['status'],'running',state)
        s.render_cancel(self.pid,job)
        result=self.wait_job(job,timeout=15)
        self.assertEqual(result['status'],'cancelled',result)
        self.assertTrue((jobs.folder(self.pid,job)/'input.json').is_file())

    def test_changed_media_queued_job_fails_closed(self):
        s.clip_add(self.pid,self.a,0,1.4)
        with patch.object(render_queue,'start_worker',return_value={}):
            job=s.render_submit(self.pid)['job_id']
        imported=Path(s.media_inspect(self.pid,self.a)['materials'][0]['path'])
        with imported.open('ab') as stream: stream.write(b'changed-test-media')
        s.render_queue_start()
        state=self.wait_job(job)
        self.assertEqual(state['status'],'failed')
        self.assertIn('Media changed',state['error']['message'])

    def test_video_overlay_too_short_rejected_at_preflight(self):
        s.clip_add(self.pid,self.image,0,5)
        s.overlay_add(self.pid,self.a,0,4,.1,.1,.2,.2)
        report=s.timeline_validate(self.pid)
        self.assertFalse(report['valid'])
        self.assertTrue(any(i['code']=='overlay_source_bounds' for i in report['issues']))


if __name__=='__main__': unittest.main()
