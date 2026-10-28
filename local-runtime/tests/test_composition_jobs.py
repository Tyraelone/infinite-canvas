import asyncio
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from gateway import create_app,STATE
from test_composition import source


class CompositionHttpTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        source(self.root/'source.mp4');self.files={'video:source':(self.root/'source.mp4').read_bytes()};self.reject_publish=False
        async def media(request):
            key=request.match_info['key']
            if request.method=='POST':
                if self.reject_publish:return web.json_response({'msg':'disk unavailable'},status=503)
                self.files[key]=await request.read()
                return web.json_response({'code':0,'data':{'key':key,'bytes':len(self.files[key]),'sha256':hashlib.sha256(self.files[key]).hexdigest()}})
            if key not in self.files:raise web.HTTPNotFound()
            return web.Response(body=self.files[key],content_type='video/mp4')
        workspace=web.Application();workspace.router.add_route('*','/api/local-workspace/files/{key}',media)
        self.workspace=TestServer(workspace);await self.workspace.start_server()
        self.client=TestClient(TestServer(create_app(data_dir=self.root/'runtime')));await self.client.start_server()
        self.runtime=self.client.server.app[STATE]
        if hasattr(self.runtime,'compositions'):self.runtime.compositions.workspace=str(self.workspace.make_url('/api/local-workspace'))

    async def asyncTearDown(self):
        await self.client.close();await self.workspace.close();self.temp.cleanup()

    def payload(self,identifier='a'*32):
        return {'requestId':identifier,'clips':[{'storageKey':'video:source','startSeconds':.25,'durationSeconds':.5}]}

    async def settled(self,identifier):
        for _ in range(400):
            response=await self.client.get('/v1/compositions/'+identifier);value=await response.json()
            if value.get('status') in ('completed','failed','cancelled'):return value
            await asyncio.sleep(.025)
        self.fail('Composition did not settle')

    async def test_real_http_composes_publishes_and_is_idempotent(self):
        before=self.files['video:source']
        response=await self.client.post('/v1/compositions',json=self.payload())
        self.assertEqual(response.status,202)
        again=await self.client.post('/v1/compositions',json=self.payload())
        self.assertEqual((await again.json())['id'],'a'*32)
        result=await self.settled('a'*32)
        self.assertEqual(result['status'],'completed',result)
        self.assertEqual((result['frames'],result['durationSeconds']),(12,.5))
        self.assertEqual(result['sha256'],hashlib.sha256(self.files[result['storageKey']]).hexdigest())
        self.assertEqual(self.files['video:source'],before)
        self.assertIn('/workspace/files/',result['url'])
        altered=self.payload();altered['clips'][0]['durationSeconds']=.25
        self.assertEqual((await self.client.post('/v1/compositions',json=altered)).status,400)

    async def test_publish_failure_is_not_a_completed_movie(self):
        self.reject_publish=True
        response=await self.client.post('/v1/compositions',json=self.payload('b'*32));self.assertEqual(response.status,202)
        result=await self.settled('b'*32)
        self.assertEqual(result['status'],'failed');self.assertNotIn('url',result)

    async def test_illegal_paths_rejected_before_job_creation(self):
        request=self.payload();request['clips']=[{'path':'C:/secret.mp4','durationSeconds':1}]
        self.assertEqual((await self.client.post('/v1/compositions',json=request)).status,400)

    async def test_request_id_cannot_replace_an_existing_video_registration(self):
        self.runtime.save('f'*32,'video',{'model':'local-h3-turbo','marker':'original'})
        response=await self.client.post('/v1/compositions',json=self.payload('f'*32))
        self.assertEqual(response.status,400)
        self.assertEqual(self.runtime.get('f'*32,'video')['marker'],'original')

    async def test_restart_marks_interrupted_tasks_failed(self):
        self.runtime.save('d'*32,'composition',{'id':'d'*32,'status':'processing','spec':self.payload()})
        await self.client.close()
        self.client=TestClient(TestServer(create_app(data_dir=self.root/'runtime')));await self.client.start_server()
        response=await self.client.get('/v1/compositions/'+'d'*32);result=await response.json()
        self.assertEqual(result.get('status'),'failed');self.assertIn('重启',result['error'])

    async def test_cancel_queued_job_never_publishes(self):
        if not hasattr(self.runtime,'compositions'):self.fail('Composition queue is missing')
        await self.runtime.compositions.limit.acquire()
        try:
            await self.client.post('/v1/compositions',json=self.payload('e'*32))
            response=await self.client.delete('/v1/compositions/'+'e'*32)
            self.assertEqual((await response.json())['status'],'cancelled')
        finally:self.runtime.compositions.limit.release()
        self.assertNotIn('video:composition:'+'e'*32,self.files)
