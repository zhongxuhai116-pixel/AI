import hashlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'apps'/'api'))
from productdirector_api import scene_generation as scenes

class PipelineTests(unittest.TestCase):
    def exercise(self, fail_encode=False):
        events=[]
        buffer=io.BytesIO();Image.new('RGB',(64,64),'purple').save(buffer,format='PNG')
        data=buffer.getvalue()
        snapshot={'scene_generation':{'reference':{'asset_id':'image','sha256':hashlib.sha256(data).hexdigest()}},
            'output':{'fps':24,'width':1080,'height':1920},
            'shots':scenes.parse_brief('0–3 seconds:\nFirst view\n3–6 seconds:\nSecond view')}
        def submit(*args,**kwargs):
            name='p'+str(sum(e.startswith('submit:') for e in events)+1);events.append('submit:'+name);return name
        def encode(args):
            events.append('encode:'+Path(args[-1]).name)
            if fail_encode:raise RuntimeError('encoding failed')
            Path(args[-1]).write_bytes(b'encoded')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            with patch.object(scenes.comfyui,'upload_image',return_value='image.png'), \
                 patch.object(scenes.comfyui,'submit',side_effect=submit) as submitted, \
                 patch.object(scenes.comfyui,'history',return_value={}), \
                 patch.object(scenes.comfyui,'status_text',return_value='SUCCEEDED'), \
                 patch.object(scenes.comfyui,'outputs',return_value=[{'filename':'output.mp4'}]), \
                 patch.object(scenes.comfyui,'download',return_value=b'real-provider-fixture'), \
                 patch.object(scenes.comfyui,'delete_pending',return_value=True) as deleted:
                def generate():return scenes.generate_scenes(snapshot,data,root,ffmpeg='ffmpeg',run_command=encode,check_active=lambda:None,progress=lambda *_:None)
                if fail_encode:
                    with self.assertRaisesRegex(RuntimeError,'encoding failed'):generate()
                    deleted.assert_called_once_with('p2')
                    self.assertNotIn('external_id',json.loads((root/'scenes/scene-02.json').read_text()))
                else:
                    report=generate()
                    self.assertEqual(report['pipeline'],'one_scene_lookahead_cpu_encode')
                    self.assertLess(events.index('submit:p2'),events.index('encode:scene-01.mp4'))
                    self.assertEqual(submitted.call_count,2)
                    generate()  # Resume completed work without a second provider submission.
                    self.assertEqual(submitted.call_count,2)
                    deleted.assert_not_called()

    def test_next_scene_is_queued_before_cpu_encoding_and_resume_is_idempotent(self):self.exercise()
    def test_encoding_failure_removes_only_our_queued_lookahead(self):self.exercise(True)
