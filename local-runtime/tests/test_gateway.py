import io
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web, ClientSession
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import create_app, image_size, video_spec, public_video, qwen_workflow, Runtime, STATE


class RequestContracts(unittest.TestCase):
    def test_h3_preserves_supported_duration_and_dimensions(self):
        spec = video_spec({'model': 'local-h3-turbo', 'prompt': 'a blue bird',
                           'size': '864x480', 'seconds': '5'}, [])
        self.assertEqual((spec['duration'], spec['resolution'], spec['aspect']), (5, '480p', '16:9'))
        self.assertEqual(spec['profile'], 'h3-turbo')

    def test_h3_rejects_unsupported_duration_instead_of_rounding(self):
        with self.assertRaisesRegex(ValueError, '5.*10.*15'):
            video_spec({'model': 'local-h3-turbo', 'prompt': 'bird', 'seconds': '6'}, [])

    def test_h3_rejects_unknown_model_and_unsupported_dimensions(self):
        for body in [{'model': 'cloud-model', 'prompt': 'bird'},
                     {'model': 'local-h3-turbo', 'prompt': 'bird', 'size': '4096x2160'}]:
            with self.assertRaises(ValueError):
                video_spec(body, [])

    def test_h3_uploaded_image_is_bound_to_first_frame(self):
        spec = video_spec({'model': 'local-h3-turbo', 'prompt': 'bird'}, ['frame.png'])
        self.assertEqual((spec['mode'], spec['uploads']), ('image', ['frame.png']))

    def test_failed_video_never_exposes_success_url(self):
        result = public_video({'id': 'abc', 'status': 'failed', 'error': 'out of memory',
                               'progress': 20, 'spec': {}, 'created': 1})
        self.assertEqual(result['status'], 'failed')
        self.assertEqual(result['error']['message'], 'out of memory')
        self.assertNotIn('video_url', result)

    def test_completed_video_uses_same_origin_media_path(self):
        result = public_video({'id': 'abc', 'status': 'completed', 'progress': 100,
                               'spec': {'width': 864, 'height': 480, 'duration': 5}, 'created': 1})
        self.assertEqual(result['video_url'], '/api/local-runtime/media/video/abc')
        self.assertEqual(result['size'], '864x480')

    def test_image_size_rejects_excessive_gpu_allocation(self):
        self.assertEqual(image_size('1024x1024'), (1024, 1024))
        with self.assertRaises(ValueError):
            image_size('8192x8192')

    def test_qwen_references_and_aspect_are_in_actual_graph(self):
        graph = qwen_workflow('blue bird', (1024, 576), 123, ['ref.png'])
        self.assertEqual(graph['4']['inputs']['prompt'], 'blue bird')
        self.assertEqual(graph['4']['inputs']['images.image_1'], ['41', 0])
        self.assertEqual(graph['14']['inputs']['width'], 512)
        self.assertEqual(graph['14']['inputs']['height'], 288)
        self.assertEqual(graph['11']['inputs']['latent_image'], ['14', 0])
        self.assertEqual(graph['8']['inputs']['noise_seed'], 123)


class HttpBoundary(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(data_dir=Path(self.temp.name), start_worker=False)))
        await self.client.start_server()
        self.client.server.app[STATE].studio = 'http://127.0.0.1:9'
        self.client.server.app[STATE].comfy = 'http://127.0.0.1:9'

    async def asyncTearDown(self):
        await self.client.close()
        self.temp.cleanup()

    async def test_models_expose_explicit_image_video_text_capabilities(self):
        response = await self.client.get('/v1/models')
        payload = await response.json()
        models = {item['id']: item['capability'] for item in payload['data']}
        self.assertEqual(models['qwen-image-2.1'], 'image')
        self.assertEqual(models['codex-image'], 'image')
        self.assertEqual(models['local-h3-turbo'], 'video')
        self.assertEqual(models['codex-local'], 'text')

    async def test_cross_site_requests_are_rejected_before_generation(self):
        response = await self.client.post('/v1/images/generations',
            headers={'Origin': 'https://untrusted.example'}, json={'prompt': 'bird'})
        self.assertEqual(response.status, 403)

    async def test_external_host_is_rejected(self):
        response = await self.client.get('/v1/models', headers={'Host': 'untrusted.example'})
        self.assertEqual(response.status, 403)

    async def test_path_escape_and_unknown_media_are_not_served(self):
        response = await self.client.get('/media/image/../../AGENTS.md')
        self.assertNotEqual(response.status, 200)
        response = await self.client.get('/media/image/missing.png')
        self.assertEqual(response.status, 404)

    async def test_missing_prompt_and_unknown_image_model_are_client_errors(self):
        for body in [{'model': 'qwen-image-2.1', 'prompt': ''}, {'model': 'unknown', 'prompt': 'bird'}]:
            response = await self.client.post('/v1/images/generations', json=body)
            self.assertEqual(response.status, 400)
            self.assertIn('message', (await response.json())['error'])

    async def test_json_tail_frame_is_rejected_instead_of_discarded(self):
        response = await self.client.post('/v1/videos', json={
            'model': 'local-h3-turbo', 'prompt': 'bird', 'last_frame_url': 'data:image/png;base64,AAAA'})
        self.assertEqual(response.status, 400)
        self.assertIn('尾帧', (await response.json())['error']['message'])

    async def test_cannot_cancel_unowned_studio_task(self):
        response = await self.client.delete('/v1/videos/' + 'a' * 32)
        self.assertEqual(response.status, 404)

    async def test_concurrent_first_playback_downloads_one_complete_cache(self):
        import asyncio
        calls = 0
        async def upstream(request):
            nonlocal calls
            calls += 1
            response = web.StreamResponse(headers={'Content-Type': 'video/mp4'})
            await response.prepare(request)
            await response.write(b'video-first-half')
            await asyncio.sleep(0.05)
            await response.write(b'-second-half')
            return response
        app = web.Application()
        app.router.add_get('/api/media/{id}/result', upstream)
        async with TestServer(app) as server:
            state = self.client.server.app[STATE]
            state.studio = str(server.make_url('')).rstrip('/')
            state.save('a' * 32, 'video', {})
            responses = await asyncio.gather(*[self.client.get('/media/video/' + 'a' * 32) for _ in range(2)])
            self.assertEqual([response.status for response in responses], [200, 200])
            for response in responses:
                self.assertEqual(await response.read(), b'video-first-half-second-half')
            self.assertEqual(calls, 1)


class QwenOutputContract(unittest.IsolatedAsyncioTestCase):
    async def test_rich_comfy_descriptor_is_filtered_for_view_query(self):
        picture = io.BytesIO()
        Image.new('RGB', (1024, 576), 'red').save(picture, format='PNG')
        prompt_id = None
        async def submit(request):
            nonlocal prompt_id
            prompt_id = (await request.json())['prompt_id']
            return web.json_response({'prompt_id': prompt_id})
        async def history(request):
            return web.json_response({prompt_id: {'status': {'completed': True}, 'outputs': {'13': {'images': [{
                'filename': 'qwen.png', 'type': 'output', 'subfolder': '', 'metadata': {'kind': 'image', 'width': 1024}}]}}}})
        async def view(request):
            self.assertEqual(dict(request.query), {'filename': 'qwen.png', 'type': 'output', 'subfolder': ''})
            return web.Response(body=picture.getvalue(), content_type='image/png')
        app = web.Application()
        app.router.add_post('/prompt', submit)
        app.router.add_get('/history/{id}', history)
        app.router.add_get('/view', view)
        async with TestServer(app) as server, ClientSession() as http:
            with tempfile.TemporaryDirectory() as directory:
                state = Runtime(Path(directory))
                state.http, state.comfy = http, str(server.make_url('')).rstrip('/')
                try:
                    path = await state.qwen('bird', (1024, 576), 1, [])
                    with Image.open(path) as result:
                        self.assertEqual(result.size, (1024, 576))
                finally:
                    state.db.close()
