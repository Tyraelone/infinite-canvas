"""Persistent CPU composition jobs using the existing trusted workspace API."""
import asyncio
import contextlib
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import quote

from aiohttp import web
from composition import normalize_request


class CompositionJobs:
    def __init__(self,runtime):
        self.runtime=runtime
        self.workspace=os.environ.get('API_BASE_URL','http://127.0.0.1:8081').rstrip('/')+'/api/local-workspace'
        self.tasks={};self.limit=asyncio.Semaphore(1)
        for identifier,payload in runtime.db.execute("SELECT id,payload FROM jobs WHERE kind='composition'").fetchall():
            job=json.loads(payload)
            if job.get('status') in ('queued','processing'):
                job.update(status='failed',stage='合成已中断',error='本机服务已重启，未完成的合成已中断，请重试')
                runtime.save(identifier,'composition',job)

    def headers(self):
        return {'Authorization':'Bearer '+os.environ.get('CANVAS_LOCAL_TOKEN','')}

    def get(self,identifier):
        if not re.fullmatch('[a-f0-9]{32}',identifier):raise web.HTTPNotFound()
        return self.runtime.get(identifier,'composition')

    def public(self,job):
        return {key:value for key,value in job.items() if key!='spec'}

    def update(self,identifier,**changes):
        job=self.get(identifier);job.update(changes);job['updatedAt']=time.time()
        self.runtime.save(identifier,'composition',job)
        return job

    def create(self,body):
        spec=normalize_request(body)
        identifier=body.get('requestId') or uuid.uuid4().hex
        if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('requestId无效')
        owner=self.runtime.db.execute('SELECT kind FROM jobs WHERE id=?',(identifier,)).fetchone()
        if owner and owner[0]!='composition':raise ValueError('requestId已被其他任务占用')
        try:existing=self.get(identifier)
        except web.HTTPNotFound:existing=None
        if existing:
            if existing['spec']!=spec:raise ValueError('同一requestId不能用于不同合成参数')
            return self.public(existing)
        if len(self.tasks)>=16:raise web.HTTPTooManyRequests(text='合成队列已满，请稍后重试')
        job={'id':identifier,'status':'queued','stage':'等待合成','progress':0,'createdAt':time.time(),'spec':spec,'title':spec['title']}
        self.runtime.save(identifier,'composition',job)
        task=asyncio.create_task(self.run(identifier));self.tasks[identifier]=task
        task.add_done_callback(lambda _:self.tasks.pop(identifier,None))
        return self.public(job)

    async def cancel(self,identifier):
        job=self.get(identifier)
        if job['status'] in ('queued','processing'):
            job=self.update(identifier,status='cancelled',stage='合成已取消',error='合成已取消')
            task=self.tasks.get(identifier)
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):await task
        return self.public(job)

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def run(self,identifier):
        process=None
        try:
            async with self.limit:
                job=self.update(identifier,status='processing',stage='正在读取视频')
                spec=job['spec'];folder=self.runtime.data/'compositions'/identifier;folder.mkdir(parents=True,exist_ok=True)
                clips=[];total_bytes=0
                for index,item in enumerate(spec['clips']):
                    path=folder/f'input-{index:02d}.mp4'
                    url=self.workspace+'/files/'+quote(item['storageKey'],safe='')
                    async with self.runtime.http.get(url,headers=self.headers()) as response:
                        if response.status!=200 or not response.headers.get('Content-Type','').startswith('video/'):
                            raise ValueError('无法读取视频，请确认源节点已保存到本机共享存储')
                        with path.open('wb') as output:
                            file_bytes=0
                            async for chunk in response.content.iter_chunked(1024*1024):
                                file_bytes+=len(chunk);total_bytes+=len(chunk)
                                if file_bytes>512*1024*1024 or total_bytes>1024**3:raise ValueError('单段视频最多512MB，合成来源总计最多1GB')
                                output.write(chunk)
                    clips.append({**item,'path':str(path)})
                destination=folder/'film.mp4'
                manifest={**spec,'clips':clips,'output':str(destination),'parentPid':os.getpid()}
                manifest_path=folder/'manifest.json';manifest_path.write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
                self.update(identifier,stage='正在合成视频')
                with (folder/'encoder.log').open('wb') as errors:
                    process=await asyncio.create_subprocess_exec(sys.executable,'-X','utf8','-B','-s',str(Path(__file__).with_name('composition.py')),str(manifest_path),
                        stdout=asyncio.subprocess.PIPE,stderr=errors,creationflags=0x08000000 if os.name=='nt' else 0)
                    result=None
                    while line:=await process.stdout.readline():
                        value=json.loads(line)
                        if 'result' in value:result=value['result']
                        else:self.update(identifier,stage='正在合成视频',progress=value['progress'])
                    code=await process.wait()
                if code or not result:
                    message=(folder/'encoder.log').read_text(encoding='utf-8',errors='replace').strip().splitlines()
                    raise ValueError((message[-1] if message else '视频编码失败，请检查片段设置')[:1000])
                self.update(identifier,stage='正在保存到D盘共享媒体',progress=97)
                key='video:composition:'+identifier
                with destination.open('rb') as file:
                    async with self.runtime.http.post(self.workspace+'/files/'+quote(key,safe=''),data=file,
                        headers={**self.headers(),'Content-Type':'video/mp4','X-Content-SHA256':result['sha256']}) as response:
                        saved=await response.json(content_type=None)
                        if response.status!=200 or saved.get('code')!=0:raise ValueError('成片已编码，但保存到D盘共享媒体失败，请重试')
                metadata=saved['data']
                if metadata['sha256']!=result['sha256'] or metadata['bytes']!=result['bytes']:raise ValueError('共享成片完整性校验失败')
                self.update(identifier,status='completed',stage='合成完成',progress=100,storageKey=key,
                    url='/api/local-runtime/workspace/files/'+quote(key,safe=''),**result)
        except asyncio.CancelledError:
            if self.get(identifier)['status']!='cancelled':self.update(identifier,status='failed',stage='合成已中断',error='本机服务退出，合成已中断，请重试')
            raise
        except Exception as error:
            self.update(identifier,status='failed',stage='合成失败',error=str(error)[:1500])
        finally:
            if process and process.returncode is None:
                with contextlib.suppress(ProcessLookupError):process.terminate()
                await process.wait()


def register_compositions(app,state_key):
    async def create(request):return web.json_response(request.app[state_key].compositions.create(await request.json()),status=202)
    async def get(request):
        jobs=request.app[state_key].compositions
        return web.json_response(jobs.public(jobs.get(request.match_info['id'])))
    async def cancel(request):return web.json_response(await request.app[state_key].compositions.cancel(request.match_info['id']))
    app.router.add_post('/v1/compositions',create)
    app.router.add_get('/v1/compositions/{id}',get)
    app.router.add_delete('/v1/compositions/{id}',cancel)
