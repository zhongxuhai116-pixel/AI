import io
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_job_control as fixtures
from PIL import Image
from productdirector_api import scene_generation as scenes
main = fixtures.main

BRIEF = '''PRODUCTO: Purple appliance. Keep the exact colors.
ESCENA 1 — 0 a 3 segundos:
A person places the product on a bedside table.
Texto en pantalla:
“Bienvenido”
ESCENA 2 — 3 a 7 segundos:
Blue stars illuminate the ceiling. Pan upward.
ESCENA 3 — 7 a 10 segundos:
A hand changes the disc.
ESCENA 4 — 10 a 13 segundos:
A phone connects via Bluetooth.
ESCENA 5 — 13 a 15 segundos:
Close-up of the product on the table.
Texto final:
“Tu universo”
ESTILO VISUAL:
Cinematic night lighting.
AUDIO:
Music.
RESTRICCIONES:
Do not deform the product.
'''


class BriefParserTests(unittest.TestCase):
    def test_preserves_five_scene_timeline_and_distinct_prompts(self):
        shots = scenes.parse_brief(BRIEF)
        self.assertEqual([s['duration_frames'] for s in shots], [72,96,72,72,48])
        self.assertEqual(shots[0]['caption_text'], 'Bienvenido')
        self.assertIn('changes the disc', shots[2]['scene_description'])
        self.assertNotIn('Music.', shots[4]['scene_prompt'])
        self.assertNotIn('phone connects', shots[0]['scene_prompt'])
        self.assertEqual(shots[-1]['end_frame'], 360)

    def test_rejects_gaps_overlap_missing_scenes(self):
        for brief in [BRIEF.replace('3 a 7', '4 a 7'), BRIEF.replace('7 a 10','6 a 10'), 'Make an ad']:
            with self.assertRaises(ValueError): scenes.parse_brief(brief)

    def test_chinese_and_fractional_frames(self):
        shots = scenes.parse_brief('场景 1 — 0 到 1.5 秒：\n产品近景\n场景 2 — 1.5 到 3 秒：\n桌面全景')
        self.assertEqual([s['duration_frames'] for s in shots], [36,36])

    def test_graph_uses_reference_image_no_gray_mesh_or_reference_video(self):
        graph = scenes.reference_graph('product.png','scene action','job/scene',72,42)
        inputs=graph['7']['inputs']
        self.assertEqual(inputs['prompt'],'scene action')
        self.assertEqual(inputs['ref_images.ref_image_0'],['12',0])
        self.assertNotIn('ref_videos.ref_video_0',inputs)
        self.assertNotIn('LoadVideo',[n['class_type'] for n in graph.values()])
        self.assertNotIn('ProductBlenderRender',[n['class_type'] for n in graph.values()])


class ScenePlanTests(unittest.TestCase):
    setUp = fixtures.JobControlAcceptanceTests.setUp
    tearDown = fixtures.JobControlAcceptanceTests.tearDown
    _create_asset = fixtures.JobControlAcceptanceTests._create_asset

    def image(self):
        b=io.BytesIO();Image.new('RGB',(256,256),'purple').save(b,format='PNG')
        return self.client.post('/api/v1/assets',files={'file':('product.png',b.getvalue(),'image/png')}).json()

    def body(self, asset):
        return {'product_asset_id':asset['id'],'profile_id':'tiktok-mx-9x16-esmx','intent':BRIEF,
                'render_mode':'h3_scenes','shots':[{'duration_frames':72},{'duration_frames':72}]}

    def test_freezes_actual_scene_text_and_reference_not_placeholder_shots(self):
        image=self.image()
        response=self.client.post('/api/v1/plans/production',json=self.body(image))
        self.assertEqual(response.status_code,201,response.text)
        body=response.json()
        self.assertEqual(body['total_frames'],360)
        self.assertEqual(body['scene_generation']['reference']['asset_id'],image['id'])
        self.assertEqual(body['shots'][1]['duration_frames'],96)
        self.assertIn('Blue stars',body['shots'][1]['scene_prompt'])
        with main.connect() as db:
            stored=json.loads(db.execute('SELECT payload FROM plans WHERE id=?',(body['id'],)).fetchone()['payload'])
        self.assertEqual(stored['shots'],body['shots'])
        self.client.post(f"/api/v1/plans/{body['id']}/approve",json={'approved':True})
        with patch.object(main,'execute_job'):
            run=self.client.post('/api/v1/runs',json={'plan_id':body['id']})
        self.assertEqual(run.status_code,202,run.text)
        frozen=json.loads((main.RUNS/run.json()['job_id']/'director_plan.json').read_text())
        self.assertEqual(frozen['scene_generation']['reference']['sha256'],image['sha256'])

    def test_missing_model_provenance_fails_before_provider_submission(self):
        with patch.object(scenes.comfyui,'submit') as submit:
            response=self.client.post('/api/v1/plans/production',json=self.body(self._create_asset()))
        self.assertEqual(response.status_code,422,response.text)
        submit.assert_not_called()

    def test_reconstruction_crop_bounds_are_checked_before_gpu(self):
        with patch.object(scenes.comfyui,'submit') as submit:
            response=self.client.post('/api/v1/providers/h3/reconstruct',json={
                'product_asset_id':self.image()['id'],'crop':[200,0,256,256]})
        self.assertEqual(response.status_code,422,response.text)
        submit.assert_not_called()

    def test_model_provenance_resolves_color_image_and_crop(self):
        image,model=self.image(),self._create_asset()
        with main.connect() as db:
            db.execute('INSERT INTO provider_jobs (id,provider,operation,status,stage,request_payload,artifact_asset_id,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?)',
                ('reference-test','h3-comfyui','RECONSTRUCT_3D','SUCCEEDED','ARTIFACT',json.dumps({
                    'asset_id':image['id'],'asset_sha256':image['sha256'],'crop':[1,2,200,200]}),model['id'],main.utc_now(),main.utc_now()))
        response=self.client.post('/api/v1/plans/production',json=self.body(model))
        self.assertEqual(response.status_code,201,response.text)
        self.assertEqual(response.json()['scene_generation']['reference']['crop'],[1,2,200,200])


if __name__ == '__main__': unittest.main()
