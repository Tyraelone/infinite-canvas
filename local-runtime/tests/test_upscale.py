import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gateway import STATE, create_app, public_video, video_spec


class UpscaleRequestContract(unittest.TestCase):
    def test_generation_passes_boolean_upscale_through_studio_normalizer(self):
        spec = video_spec({'prompt': 'bird', 'upscale': True, 'size': '864x480', 'seconds': '15'}, [])
        self.assertTrue(spec['upscale'])
        self.assertEqual((spec['output_width'], spec['output_height'], spec['frames']), (1728, 960, 362))
        self.assertFalse(video_spec({'prompt': 'bird', 'upscale': False}, [])['upscale'])

    def test_generation_rejects_over_limit_without_shortening(self):
        for size in ('1280x720', '1344x768'):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, '480p.*15.*720p/768p.*5'):
                video_spec({'prompt': 'bird', 'upscale': True, 'size': size, 'seconds': '10'}, [])
        with self.assertRaises(ValueError):
            video_spec({'prompt': 'bird', 'upscale': 'false'}, [])

    def test_public_upscale_uses_output_dimensions_and_preserves_source_metadata(self):
        result = public_video({'id': 'child', 'status': 'completed', 'progress': 100, 'created': 1,
            'spec': {'kind': 'upscale', 'profile': 'h3-turbo', 'width': 864, 'height': 480,
                     'output_width': 1728, 'output_height': 960, 'frames': 124, 'duration': 5,
                     'upscale': True, 'source_job_id': 'original'}})
        self.assertEqual(result['size'], '1728x960')
        self.assertEqual(result['source_video_id'], 'original')
        self.assertTrue(result['upscale'])
        self.assertFalse(result['can_upscale'])


class OwnedUpscaleRoutes(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(data_dir=Path(self.temp.name), start_worker=False)))
        await self.client.start_server()
        self.state = self.client.server.app[STATE]
        self.original_id, self.child_id = 'a' * 32, 'b' * 32
        self.original = {'id': self.original_id, 'status': 'completed', 'progress': 100,
            'spec': video_spec({'prompt': 'bird'}, []), 'result': {'filename': 'original.mp4'}}
        self.child = {'id': self.child_id, 'status': 'queued', 'progress': 0,
            'spec': {**self.original['spec'], 'kind': 'upscale', 'upscale': True,
                     'source_job_id': self.original_id, 'output_width': 1728, 'output_height': 960}}
        self.posts = []
        async def get_job(request):
            return web.json_response(self.original if request.match_info['id'] == self.original_id else self.child)
        async def upscale(request):
            self.posts.append(request.match_info['id'])
            return web.json_response(self.child, status=201)
        async def generate(request):
            spec = await request.json()
            self.posts.append(spec)
            return web.json_response({**self.original, 'status': 'queued', 'spec': spec}, status=201)
        async def cancel(request):
            self.child.update(status='cancelled', stage='已取消')
            return web.json_response(self.child)
        app = web.Application()
        app.router.add_get('/api/jobs/{id}', get_job)
        app.router.add_post('/api/jobs', generate)
        app.router.add_post('/api/jobs/{id}/upscale', upscale)
        app.router.add_post('/api/jobs/{id}/cancel', cancel)
        self.studio = TestServer(app)
        await self.studio.start_server()
        self.state.studio = str(self.studio.make_url('')).rstrip('/')

    async def asyncTearDown(self):
        await self.client.close()
        await self.studio.close()
        self.temp.cleanup()

    async def test_unowned_original_cannot_be_upscaled(self):
        response = await self.client.post('/v1/videos/' + self.original_id + '/upscale', json={})
        self.assertEqual(response.status, 404)
        self.assertEqual(self.posts, [])

    async def test_generation_submits_upscale_and_rejects_limits_before_submission(self):
        response = await self.client.post('/v1/videos', json={'prompt': 'bird', 'upscale': True,
            'size': '864x480', 'seconds': '15', 'video_generate_audio': False})
        self.assertEqual(response.status, 200)
        self.assertEqual((self.posts[0]['frames'], self.posts[0]['upscale'], self.posts[0]['audio']), (362, True, False))
        response = await self.client.post('/v1/videos', json={'prompt': 'bird', 'upscale': True,
            'size': '1280x720', 'seconds': '10'})
        self.assertEqual(response.status, 400)
        self.assertEqual(len(self.posts), 1)

    async def test_owned_original_creates_owned_child_and_keeps_parent(self):
        self.state.save(self.original_id, 'video', {'model': 'local-h3-turbo', 'created': 1})
        response = await self.client.post('/v1/videos/' + self.original_id + '/upscale', json={})
        self.assertEqual(response.status, 200)
        task = await response.json()
        self.assertEqual((task['id'], task['status']), (self.child_id, 'queued'))
        self.assertEqual(self.posts, [self.original_id])
        self.assertEqual(self.state.get(self.child_id, 'video')['source_video_id'], self.original_id)
        self.assertEqual(self.state.get(self.original_id, 'video')['model'], 'local-h3-turbo')
        self.child.update(status='running', progress=47)
        response = await self.client.get('/v1/videos/' + self.child_id)
        self.assertEqual((await response.json())['progress'], 47)
        self.child.update(status='completed', progress=100)
        response = await self.client.get('/v1/videos/' + self.child_id)
        task = await response.json()
        self.assertEqual(task['size'], '1728x960')
        self.assertTrue(task['video_url'].endswith(self.child_id))
        response = await self.client.delete('/v1/videos/' + self.child_id)
        self.assertEqual((await response.json())['status'], 'failed')

    async def test_only_completed_supported_h3_original_is_accepted(self):
        self.state.save(self.original_id, 'video', {})
        cases = [{'status': 'running'}, {'spec': {**self.original['spec'], 'upscale': True}},
                 {'spec': {**self.original['spec'], 'profile': 'ltx-preview'}},
                 {'spec': {**self.original['spec'], 'kind': 'upscale'}},
                 {'spec': {**self.original['spec'], 'width': 1280, 'height': 720, 'frames': 243}}]
        for patch in cases:
            with self.subTest(patch=patch):
                original = self.original
                self.original = {**original, **patch}
                response = await self.client.post('/v1/videos/' + self.original_id + '/upscale', json={})
                self.assertEqual(response.status, 400)
                self.original = original
        self.assertEqual(self.posts, [])
