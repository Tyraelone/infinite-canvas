import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import create_app, video_spec, public_video, STATE
from workflows import video_graph, normalize_request

PROMPT = '<Picture 1> runs with <Picture 2> in <Picture 3>.'
BASE = {'model': 'local-h3-quality', 'reference_mode': 'ref2va', 'prompt': PROMPT, 'seconds': '5', 'size': '864x480'}


class NativeContract(unittest.TestCase):
    def test_four_through_nine_native_references_reach_the_graph_without_truncation(self):
        for count in range(4, 10):
            files = [f'ref{i}.png' for i in range(1, count + 1)]
            prompt = ' '.join(f'<Picture {i}> is a reference.' for i in range(1, count + 1))
            spec = video_spec({**BASE, 'prompt': prompt}, files)
            graph = video_graph(spec, references=spec['uploads'])
            slots = {key: value for key, value in graph['5']['inputs'].items() if key.startswith('ref_images.')}
            self.assertEqual(len(slots), count)
            for i, filename in enumerate(files):
                self.assertEqual(graph[str(50+i)]['inputs']['image'], filename)
                self.assertEqual(slots[f'ref_images.ref_image_{i}'], [str(50+i), 0])

    def test_native_maximum_does_not_expand_qwen_reference_limit(self):
        with self.assertRaisesRegex(ValueError, '3'):
            normalize_request({'mode': 'reference', 'prompt': 'scene', 'uploads': ['a.png'] * 4})

    def test_request_byte_ceiling_can_carry_nine_maximum_size_images_as_json(self):
        from gateway import MAX_IMAGE_BYTES
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(data_dir=Path(directory), start_worker=False)
            try:
                self.assertGreaterEqual(app._client_max_size, 9 * MAX_IMAGE_BYTES * 4 // 3 + 1024)
            finally:
                app[STATE].db.close()

    def test_three_reference_spec_retains_order_and_quality_native_graph(self):
        spec = video_spec(BASE, ['person.png', 'robot.png', 'scene.png'])
        self.assertEqual(spec['mode'], 'native')
        self.assertEqual(spec['profile'], 'h3-quality')
        self.assertEqual(spec['uploads'], ['person.png', 'robot.png', 'scene.png'])
        graph = video_graph(spec, references=spec['uploads'])
        self.assertEqual(graph['5']['class_type'], 'MiniMaxH3ReferenceToVideo')
        self.assertIn('ref2va', graph['1']['inputs']['unet_name'])
        self.assertEqual(graph['9']['inputs']['steps'], 20)
        for i, filename in enumerate(spec['uploads']):
            self.assertEqual(graph[str(50+i)]['inputs']['image'], filename)
            self.assertEqual(graph['5']['inputs'][f'ref_images.ref_image_{i}'], [str(50+i), 0])
        self.assertNotIn('first_frame', graph['5']['inputs'])

    def test_auto_multiple_images_and_explicit_single_native_reference(self):
        auto = {k: v for k, v in BASE.items() if k != 'reference_mode'}
        self.assertEqual(video_spec(auto, ['a.png', 'b.png', 'c.png'])['mode'], 'native')
        self.assertEqual(video_spec({**BASE, 'prompt': '<Picture 1> waves'}, ['a.png'])['mode'], 'native')

    def test_invalid_native_requests_are_not_silently_changed(self):
        for body, images in [
            ({**BASE, 'model': 'local-h3-turbo'}, ['a.png', 'b.png', 'c.png']),
            ({**BASE, 'prompt': 'missing tags'}, ['a.png']),
            ({**BASE, 'reference_mode': 'wrong'}, ['a.png']),
            ({**BASE, 'first_frame_url': 'frame'}, ['a.png', 'b.png', 'c.png']),
            (BASE, ['a.png'] * 4),
            (BASE, []),
            ({**BASE, 'prompt': '<Picture 1> and <Picture 4>'}, ['a.png']),
        ]:
            with self.subTest(body=body, images=images), self.assertRaises(ValueError):
                video_spec(body, images)

    def test_polled_tasks_expose_native_mode_for_canvas_retry(self):
        spec = {'mode': 'native', 'profile': 'h3-quality'}
        self.assertEqual(public_video({'id': 'job', 'status': 'queued', 'spec': spec})['reference_mode'], 'ref2va')


class NativeHttp(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(data_dir=Path(self.temp.name), start_worker=False)))
        await self.client.start_server()
        self.uploads, self.jobs = [], []
        async def upload(request):
            reader = await request.multipart()
            field = await reader.next()
            raw = await field.read()
            with Image.open(io.BytesIO(raw)) as image:
                self.uploads.append(image.convert('RGB').getpixel((0, 0)))
            return web.json_response({'id': f'ref{len(self.uploads)}.png'})
        async def submit(request):
            spec = await request.json()
            self.jobs.append(spec)
            return web.json_response({'id': 'a'*32, 'status': 'queued', 'spec': spec})
        app = web.Application()
        app.router.add_post('/api/uploads', upload)
        app.router.add_post('/api/jobs', submit)
        self.studio = TestServer(app)
        await self.studio.start_server()
        self.client.server.app[STATE].studio = str(self.studio.make_url('')).rstrip('/')

    async def asyncTearDown(self):
        await self.client.close()
        await self.studio.close()
        self.temp.cleanup()

    async def test_actual_http_upload_order_matches_native_graph_references(self):
        import base64
        references = []
        for color in ['red', 'green', 'blue']:
            stream = io.BytesIO()
            Image.new('RGB', (32, 32), color).save(stream, format='PNG')
            references.append('data:image/png;base64,' + base64.b64encode(stream.getvalue()).decode())
        response = await self.client.post('/v1/videos', json={**BASE, 'input_reference': references})
        payload = await response.json()
        self.assertEqual(response.status, 200, payload)
        self.assertEqual(self.uploads, [(255, 0, 0), (0, 128, 0), (0, 0, 255)])
        self.assertEqual(self.jobs[0]['uploads'], ['ref1.png', 'ref2.png', 'ref3.png'])
        self.assertEqual(self.jobs[0]['mode'], 'native')
        self.assertEqual(payload['reference_mode'], 'ref2va')

    async def test_nine_reference_json_and_multipart_requests_preserve_every_upload(self):
        import aiohttp
        import base64
        prompt = ' '.join(f'<Picture {i}> is a reference.' for i in range(1, 10))
        raw_images, colors = [], []
        for i in range(9):
            color = (20+i*20, 80+i, 160-i*10)
            stream = io.BytesIO()
            Image.new('RGB', (32, 32), color).save(stream, format='PNG')
            raw_images.append(stream.getvalue())
            colors.append(color)
        response = await self.client.post('/v1/videos', json={**BASE, 'prompt': prompt,
            'input_reference': ['data:image/png;base64,' + base64.b64encode(raw).decode() for raw in raw_images]})
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.uploads, colors)
        self.assertEqual(self.jobs[0]['uploads'], [f'ref{i}.png' for i in range(1, 10)])
        form = aiohttp.FormData()
        for name, value in {**BASE, 'prompt': prompt}.items():
            form.add_field(name, value)
        for i, raw in enumerate(raw_images):
            form.add_field('input_reference[]', raw, filename=f'image{i}.png', content_type='image/png')
        response = await self.client.post('/v1/videos', data=form)
        self.assertEqual(response.status, 200, await response.text())
        self.assertEqual(self.uploads[9:], colors)
        self.assertEqual(self.jobs[1]['uploads'], [f'ref{i}.png' for i in range(10, 19)])

    async def test_tenth_native_image_is_rejected_before_upload_or_submission(self):
        response = await self.client.post('/v1/videos', json={**BASE,
            'prompt': ' '.join(f'<Picture {i}>' for i in range(1, 11)), 'input_reference': ['invalid-image'] * 10})
        self.assertEqual(response.status, 400)
        self.assertIn('9', (await response.json())['error']['message'])
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.jobs, [])

    async def test_qwen_image_edit_still_rejects_four_references(self):
        response = await self.client.post('/v1/images/edits', json={'model': 'qwen-image-2.1',
            'prompt': 'scene', 'input_reference': ['invalid-image'] * 4})
        self.assertEqual(response.status, 400)
        self.assertIn('3', (await response.json())['error']['message'])

    async def test_invalid_requests_fail_before_any_upload_or_job(self):
        for patch in [{'model': 'local-h3-turbo'}, {'prompt': 'no tags'}, {'first_frame_url': 'extra-frame'}]:
            response = await self.client.post('/v1/videos', json={**BASE, 'input_reference': ['bad-image']*3, **patch})
            self.assertEqual(response.status, 400)
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.jobs, [])

    async def test_multipart_first_frame_binding_is_not_silently_recast_as_a_native_reference(self):
        import aiohttp
        import base64
        stream = io.BytesIO()
        Image.new('RGB', (32, 32), 'red').save(stream, format='PNG')
        raw = stream.getvalue()
        for file_frame in [True, False]:
            form = aiohttp.FormData()
            for name, value in BASE.items():
                form.add_field(name, value)
            if file_frame:
                form.add_field('first_frame_url', raw, filename='first.png', content_type='image/png')
            else:
                form.add_field('first_frame_url', 'data:image/png;base64,' + base64.b64encode(raw).decode())
            for index in range(2):
                form.add_field('input_reference[]', raw, filename=f'ref{index}.png', content_type='image/png')
            response = await self.client.post('/v1/videos', data=form)
            self.assertEqual(response.status, 400)
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.jobs, [])


if __name__ == '__main__':
    unittest.main()
