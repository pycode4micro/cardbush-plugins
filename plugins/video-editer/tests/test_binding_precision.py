"""Regression from a real reference-hook edit with decimal source boundaries."""
import copy
import unittest
from video_editer import timing


class BindingPrecisionTests(unittest.TestCase):
    def test_decimal_end_keys_survive_trim_speed_and_hook_prepend(self):
        timeline={'tracks':{'main':[{'id':'body','asset_id':'source','start':19.6,'end':24.56,
                                    'playback_speed':1,'transition':'none'}],
          'overlays':[{'id':'title','start':3.16,'end':4.92,
                       'keyframes':[{'at':t,'opacity':1} for t in (3.16,3.36,4.76,4.92)]}]},
          'track_meta':{'overlays':{'kind':'overlay'}}}
        timing.bind(timeline,'title','body',22.76,24.52)
        for speed in (1,1.25):
            changed=copy.deepcopy(timeline)
            changed['tracks']['main'][0].update(start=19.8,playback_speed=speed)
            changed['tracks']['main'].insert(0,{'id':'hook','asset_id':'generated','start':0,
                                              'end':3.12,'playback_speed':1,'transition':'none'})
            event=timing.resolve(changed)['tracks']['overlays'][0]
            self.assertEqual(event['keyframes'][0]['at'],event['start'])
            self.assertEqual(event['keyframes'][-1]['at'],event['end'])
            self.assertTrue(all(event['start']<=k['at']<=event['end'] for k in event['keyframes']))
            self.assertEqual(len({k['at'] for k in event['keyframes']}),4)
            self.assertAlmostEqual(event['start'],3.12+(22.76-19.8)/speed)

    def test_real_out_of_range_key_is_still_rejected(self):
        timeline={'tracks':{'main':[{'id':'body','asset_id':'source','start':19.6,'end':24.56,
                                    'playback_speed':1,'transition':'none'}],
          'overlays':[{'id':'title','start':3.16,'end':4.92,'keyframes':[{'at':4.921}]}]}}
        with self.assertRaisesRegex(ValueError,'outside its source anchor'):
            timing.bind(timeline,'title','body',22.76,24.52)
