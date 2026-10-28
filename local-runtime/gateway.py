"""Loopback OpenAI-compatible gateway for this machine's existing AI engines."""
import argparse
import asyncio
import base64
import contextlib
import io
import json
import logging
import math
import os
import re
import shutil
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

import aiohttp
from aiohttp import web
from PIL import Image, ImageOps

sys.path.insert(0, str(Path(__file__).resolve().parent))
from codex_rpc import CodexSession
from composition_jobs import CompositionJobs, register_compositions

ROOT = Path(__file__).resolve().parents[1]
H3_ROOT = Path(os.environ.get('CANVAS_H3_ROOT', r'D:\AI\MiniMax-H3'))
sys.path.insert(0, str(H3_ROOT / 'studio'))
from workflows import normalize_request, qwen_graph, SIZES, upscale_frame_limit, NATIVE_REFERENCE_MAX_IMAGES

MODELS = [
    {'id': 'qwen-image-2.1', 'name': 'Qwen-Image 2.1 · 本机', 'capability': 'image'},
    {'id': 'codex-image', 'name': 'Codex 生图', 'capability': 'image'},
    {'id': 'local-h3-turbo', 'name': 'H3 日常生成 · 本机', 'capability': 'video'},
    {'id': 'local-h3-quality', 'name': 'H3 品质成片 · 本机', 'capability': 'video'},
    {'id': 'codex-local', 'name': 'Codex · 本机登录', 'capability': 'text'},
]
VIDEO_PROFILES = {'local-h3-turbo': 'h3-turbo', 'local-h3-quality': 'h3-quality'}
MAX_IMAGE_BYTES = 24 * 1024 * 1024
LOG = logging.getLogger('canvas.local')


def image_size(value):
    if not value or value == 'auto':
        return 1024, 1024
    match = re.fullmatch(r'(\d+)x(\d+)', str(value))
    if not match:
        raise ValueError('图片尺寸请使用宽x高，例如 1024x1024')
    width, height = map(int, match.groups())
    if min(width, height) < 256 or max(width, height) > 2048 or width * height > 2048 ** 2:
        raise ValueError('本机图片边长支持 256–2048，最多 419 万像素')
    if width % 16 or height % 16:
        raise ValueError('Qwen 图片宽高必须为 16 的倍数')
    return width, height


def video_spec(body, uploads):
    model = body.get('model', 'local-h3-turbo')
    if model not in VIDEO_PROFILES:
        raise ValueError('请选择本机 H3 视频模型')
    raw_size = body.get('size') or '864x480'
    selected = next(((resolution, aspect) for resolution, values in SIZES.items()
                     for aspect, size in values.items() if f'{size[0]}x{size[1]}' == raw_size), None)
    if selected is None:
        raise ValueError('本机 H3 支持 480p/720p/768p 的 16:9、9:16、1:1 画幅')
    try:
        duration = int(body.get('seconds', 5))
    except (TypeError, ValueError):
        raise ValueError('H3 时长支持 5、10 或 15 秒')
    if duration not in (5, 10, 15) or str(body.get('seconds', 5)) not in ('5', '10', '15'):
        raise ValueError('H3 时长支持 5、10 或 15 秒')
    reference_mode = body.get('reference_mode', 'auto')
    if reference_mode not in ('auto', 'ref2va'):
        raise ValueError('H3 参考模式支持 auto 或 ref2va')
    prompt = body.get('prompt', '')
    if not isinstance(prompt, str):
        raise ValueError('视频提示词必须为文本')
    if body.get('last_frame_url') or any(body.get(key) for key in ('video_reference', 'video_reference[]', 'audio_reference', 'audio_reference[]')):
        raise ValueError('本机 H3 不支持尾帧、视频或独立音频参考')
    native = reference_mode == 'ref2va' or len(uploads) > 1 or bool(re.search(r'<Picture \d+>', prompt))
    mode = 'native' if native else 'image' if uploads else 'text'
    if native:
        if model != 'local-h3-quality':
            raise ValueError('Ref2VA 需要选择 H3 品质成片模型，不支持 Turbo 档')
        if body.get('first_frame_url'):
            raise ValueError('Ref2VA 不能混用首帧绑定，请将图片作为普通参考图连接')
        if not 1 <= len(uploads) <= NATIVE_REFERENCE_MAX_IMAGES:
            raise ValueError(f'Ref2VA 需要 1–{NATIVE_REFERENCE_MAX_IMAGES} 张参考图')
        tags = {int(index) for index in re.findall(r'<Picture (\d+)>', prompt)}
        if tags != set(range(1, len(uploads) + 1)):
            raise ValueError(f'请按参考图顺序使用 <Picture 1> 至 <Picture {NATIVE_REFERENCE_MAX_IMAGES}>，数量须与图片一致')
    audio = str(body.get('video_generate_audio', 'true')).lower() == 'true'
    spec = normalize_request({'prompt': body.get('prompt', ''), 'mode': mode,
        'profile': VIDEO_PROFILES[model], 'resolution': selected[0], 'aspect': selected[1],
        'duration': duration, 'audio': audio, 'uploads': uploads, 'upscale': body.get('upscale', False),
        **({'seed': int(body['seed'])} if body.get('seed') not in (None, '') else {})})
    return spec


def public_video(job):
    status = {'running': 'processing', 'cancelling': 'processing', 'cancelled': 'failed'}.get(job['status'], job['status'])
    spec = job.get('spec', {})
    result = {'id': job['id'], 'object': 'video', 'status': status, 'progress': job.get('progress', 0),
              'created_at': job.get('created'), 'stage': job.get('stage', ''),
              'model': next((key for key, value in VIDEO_PROFILES.items() if value == spec.get('profile')), 'local-h3-turbo'),
              'size': f"{spec.get('output_width', spec.get('width', 864))}x{spec.get('output_height', spec.get('height', 480))}",
              'seconds': str(spec.get('duration', 5)), 'upscale': spec.get('upscale', False),
              'reference_mode': 'ref2va' if spec.get('mode') == 'native' else 'auto',
              'source_video_id': spec.get('source_job_id'), 'can_upscale': can_upscale_video(job)}
    if status == 'completed':
        result['video_url'] = result['url'] = '/api/local-runtime/media/video/' + job['id']
    if status == 'failed':
        result['error'] = {'message': job.get('error') or '生成已取消'}
    return result


def can_upscale_video(job):
    spec = job.get('spec', {})
    return bool(job.get('status') == 'completed' and job.get('result') and
                spec.get('profile') in VIDEO_PROFILES.values() and spec.get('kind') == 'video' and
                not spec.get('upscale') and 0 < spec.get('frames', 0) <=
                upscale_frame_limit(spec.get('width', 0), spec.get('height', 0)))


def qwen_workflow(prompt, size, seed, references=()):
    graph = qwen_graph({'frame_prompt': prompt, 'seed': seed}, references)
    # This installed Qwen VAE has a 16x latent stride; LatentUpscale uses 8x.
    graph['14'] = {'class_type': 'LatentUpscale', 'inputs': {
        'samples': ['4', 2], 'upscale_method': 'nearest-exact',
        'width': size[0] // 2, 'height': size[1] // 2, 'crop': 'disabled'}}
    graph['11']['inputs']['latent_image'] = ['14', 0]
    return graph


def normalized_png(raw):
    if len(raw) > MAX_IMAGE_BYTES:
        raise ValueError('参考图不能超过 24MB')
    with Image.open(io.BytesIO(raw)) as original:
        if original.format not in ('PNG', 'JPEG', 'WEBP') or original.width * original.height > 24_000_000:
            raise ValueError('请使用 2400 万像素以内的 PNG/JPG/WebP 图片')
        original.load()
        image = ImageOps.exif_transpose(original).convert('RGBA')
        output = io.BytesIO()
        image.save(output, format='PNG')
        return output.getvalue()


class Runtime:
    def __init__(self, data_dir):
        self.data = Path(data_dir)
        self.media = self.data / 'media'
        self.media.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.data / 'gateway.sqlite')
        self.db.execute('CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, kind TEXT, payload TEXT)')
        self.db.commit()
        self.http = None
        self.studio = os.environ.get('CANVAS_STUDIO_URL', 'http://127.0.0.1:18290')
        self.comfy = os.environ.get('CANVAS_COMFY_URL', 'http://127.0.0.1:8189')
        self.image_lock = asyncio.Lock()
        self.video_locks = {}
        self.compositions = CompositionJobs(self)

    def save(self, job_id, kind, payload):
        self.db.execute('INSERT OR REPLACE INTO jobs VALUES (?, ?, ?)',
                        (job_id, kind, json.dumps(payload, ensure_ascii=False)))
        self.db.commit()

    def get(self, job_id, kind):
        row = self.db.execute('SELECT payload FROM jobs WHERE id=? AND kind=?', (job_id, kind)).fetchone()
        if not row:
            raise web.HTTPNotFound()
        return json.loads(row[0])

    async def json(self, method, url, **kwargs):
        async with self.http.request(method, url, **kwargs) as response:
            payload = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(json.dumps(payload.get('error') or payload, ensure_ascii=False)[:1500])
            return payload

    async def reference(self, value):
        if isinstance(value, bytes):
            raw = value
        elif isinstance(value, str) and value.startswith('data:image/'):
            try:
                raw = base64.b64decode(value.split(',', 1)[1], validate=True)
            except (ValueError, IndexError):
                raise ValueError('参考图片 data URL 无效')
        elif isinstance(value, str) and value.startswith('/api/local-runtime/media/image/'):
            name = value.rsplit('/', 1)[-1]
            raw = self.image_file(name).read_bytes()
        else:
            raise ValueError('参考图请上传文件或使用图片 data URL')
        raw = await asyncio.to_thread(normalized_png, raw)
        path = self.media / (uuid.uuid4().hex + '.png')
        path.write_bytes(raw)
        return path

    def image_file(self, name):
        if not re.fullmatch(r'[a-f0-9]{32}\.png', name):
            raise web.HTTPNotFound()
        path = self.media / name
        if not path.is_file():
            raise web.HTTPNotFound()
        return path

    async def studio_upload(self, path):
        data = aiohttp.FormData()
        data.add_field('image', path.read_bytes(), filename=path.name, content_type='image/png')
        value = await self.json('POST', self.studio + '/api/uploads', data=data)
        return value['id']

    async def qwen(self, prompt, size, seed, references):
        names = []
        for path in references:
            data = aiohttp.FormData()
            data.add_field('image', path.read_bytes(), filename=path.name, content_type='image/png')
            result = await self.json('POST', self.comfy + '/upload/image', data=data)
            names.append((result.get('subfolder', '') + '/' + result['name']).lstrip('/'))
        graph = qwen_workflow(prompt, size, seed, names)
        prompt_id = str(uuid.uuid4())
        graph['13']['inputs']['filename_prefix'] = 'InfiniteCanvas/' + prompt_id
        self.save(prompt_id, 'image', {'status': 'queued', 'prompt': prompt, 'size': size})
        (self.data / (prompt_id + '.json')).write_text(json.dumps(graph, indent=2), encoding='utf-8')
        await self.json('POST', self.comfy + '/prompt', json={
            'prompt_id': prompt_id, 'client_id': 'infinite-canvas-' + prompt_id, 'prompt': graph})
        async with asyncio.timeout(1800):
            while True:
                history = await self.json('GET', self.comfy + '/history/' + prompt_id)
                if prompt_id in history:
                    result = history[prompt_id]
                    if not result['status'].get('completed'):
                        errors = [event.get('exception_message') for kind, event in result['status'].get('messages', []) if kind == 'execution_error']
                        raise RuntimeError('Qwen 推理失败：' + str(next(iter(errors), '任务未完成')))
                    outputs = result.get('outputs', {}).get('13', {}).get('images', [])
                    if not outputs:
                        raise RuntimeError('Qwen 未返回生成图片')
                    image = outputs[0]
                    descriptor = {key: image[key] for key in ('filename', 'subfolder', 'type') if key in image}
                    async with self.http.get(self.comfy + '/view', params=descriptor) as response:
                        response.raise_for_status()
                        raw = await response.read()
                    path = self.media / (uuid.uuid4().hex + '.png')
                    path.write_bytes(await asyncio.to_thread(normalized_png, raw))
                    self.save(prompt_id, 'image', {'status': 'completed', 'file': path.name})
                    return path
                await asyncio.sleep(2)


STATE = web.AppKey('runtime', Runtime)


@web.middleware
async def boundary(request, handler):
    host = urlsplit('http://' + request.host).hostname
    origin = request.headers.get('Origin')
    if host not in ('localhost', '127.0.0.1', '::1') or request.remote not in ('127.0.0.1', '::1', None):
        return web.json_response({'error': {'message': '本机接口仅接受回环地址'}}, status=403)
    if origin and origin not in ('http://127.0.0.1:3000', 'http://localhost:3000'):
        return web.json_response({'error': {'message': '请求来源无效'}}, status=403)
    expected = os.environ.get('CANVAS_LOCAL_TOKEN')
    if expected and request.headers.get('Authorization') != 'Bearer ' + expected:
        return web.json_response({'error': {'message': '本机接口凭据无效'}}, status=401)
    try:
        response = await handler(request)
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        response = web.json_response({'error': {'message': str(error)}}, status=400)
    except (RuntimeError, aiohttp.ClientError, asyncio.TimeoutError) as error:
        LOG.exception('Local engine request failed')
        response = web.json_response({'error': {'message': str(error) or '本机引擎请求超时'}}, status=502)
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    return response


async def parse_body(request, max_references=3):
    references = []
    if request.content_type == 'multipart/form-data':
        body = {}
        reader = await request.multipart()
        while field := await reader.next():
            if field.filename:
                if field.name not in ('image', 'image[]', 'input_reference[]', 'first_frame_url'):
                    raise ValueError('当前接口不支持此参考文件：' + str(field.name))
                raw = bytearray()
                while chunk := await field.read_chunk():
                    raw.extend(chunk)
                    if len(raw) > MAX_IMAGE_BYTES:
                        raise ValueError('参考图不能超过 24MB')
                references.append(bytes(raw))
                if field.name == 'first_frame_url':
                    body['first_frame_url'] = True
            else:
                value = await field.text()
                if field.name in ('image', 'image[]', 'input_reference[]', 'first_frame_url') and value:
                    references.append(value)
                    if field.name == 'first_frame_url':
                        body['first_frame_url'] = True
                else:
                    body[field.name] = value
    else:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError('请求必须为 JSON 对象')
        for key in ('image', 'images', 'input_reference', 'input_reference[]', 'first_frame_url'):
            value = body.get(key)
            if value:
                references.extend(value if isinstance(value, list) else [value])
    if body.get('last_frame_url'):
        raise ValueError('当前画布接口暂不支持尾帧，请使用视频工作室的首尾帧入口')
    if len(references) > max_references:
        raise ValueError(f'最多支持 {max_references} 张参考图')
    return body, references


async def models(request):
    return web.json_response({'object': 'list', 'data': [{**m, 'object': 'model', 'owned_by': 'local'} for m in MODELS]})


async def status(request):
    state = request.app[STATE]
    try:
        engine = await state.json('GET', state.studio + '/api/status')
    except Exception as error:
        engine = {'connected': False, 'error': str(error)}
    return web.json_response({'service': 'infinite-canvas-local', 'models': MODELS, 'engine': engine})


async def images(request):
    state = request.app[STATE]
    body, raw_references = await parse_body(request)
    model, prompt = body.get('model', 'qwen-image-2.1'), body.get('prompt', '')
    if model not in ('qwen-image-2.1', 'codex-image'):
        raise ValueError('请选择 Qwen-Image-2.1 或 Codex 生图')
    if not isinstance(prompt, str) or not 1 <= len(prompt.strip()) <= 16000:
        raise ValueError('请输入有效的图片描述')
    size = image_size(body.get('size'))
    count = int(body.get('n', 1))
    if not 1 <= count <= 4:
        raise ValueError('本机每批支持 1–4 张图片')
    references = [await state.reference(value) for value in raw_references]
    paths = []
    async with state.image_lock:
        for index in range(count):
            if model == 'qwen-image-2.1':
                paths.append(await state.qwen(prompt, size, int(body.get('seed', uuid.uuid4().int % 2**32)) + index, references))
            else:
                async with CodexSession(state.data / 'codex-work') as codex:
                    result = await codex.run(
                        f'Use the image generation tool to {"edit the attached images" if references else "generate an image"}. '
                        f'Requested image dimensions: {size[0]}x{size[1]}. Return the generated image artifact. '
                        f'Do not use code to draw. Image prompt:\n{prompt}', images=references, image=True)
                for source in result['images']:
                    path = state.media / (uuid.uuid4().hex + '.png')
                    path.write_bytes(await asyncio.to_thread(normalized_png, source.read_bytes()))
                    paths.append(path)
    result = []
    for path in paths:
        with Image.open(path) as image:
            width, height = image.size
        result.append({'b64_json': base64.b64encode(path.read_bytes()).decode(),
                       'url': '/api/local-runtime/media/image/' + path.name, 'width': width, 'height': height})
    return web.json_response({'created': int(time.time()), 'data': result})


async def create_video(request):
    state = request.app[STATE]
    body, raw = await parse_body(request, max_references=NATIVE_REFERENCE_MAX_IMAGES)
    # Validate before doing any upload or submission.
    video_spec(body, ['placeholder.png'] * len(raw))
    paths = [await state.reference(value) for value in raw]
    uploads = [await state.studio_upload(path) for path in paths]
    spec = video_spec(body, uploads)
    job = await state.json('POST', state.studio + '/api/jobs', json=spec)
    state.save(job['id'], 'video', {'created': time.time(), 'model': body.get('model')})
    return web.json_response(public_video(job))


async def get_video(request):
    state, job_id = request.app[STATE], request.match_info['id']
    state.get(job_id, 'video')
    job = await state.json('GET', state.studio + '/api/jobs/' + job_id)
    return web.json_response(public_video(job))


async def upscale_video(request):
    state, job_id = request.app[STATE], request.match_info['id']
    original = state.get(job_id, 'video')
    source = await state.json('GET', state.studio + '/api/jobs/' + job_id)
    if not can_upscale_video(source):
        raise ValueError('请选择已完成的H3原片；不能重复超分。480p最长15秒，720p/768p最长5秒')
    job = await state.json('POST', state.studio + '/api/jobs/' + job_id + '/upscale', json={})
    state.save(job['id'], 'video', {'created': time.time(), 'model': original.get('model'),
                                  'source_video_id': job_id, 'upscale': True})
    return web.json_response(public_video(job))


async def cancel_video(request):
    state, job_id = request.app[STATE], request.match_info['id']
    state.get(job_id, 'video')
    job = await state.json('POST', state.studio + '/api/jobs/' + job_id + '/cancel', json={})
    return web.json_response(public_video(job))


async def media(request):
    state = request.app[STATE]
    if request.match_info['kind'] == 'image':
        return web.FileResponse(state.image_file(request.match_info['id']))
    job_id = request.match_info['id']
    if not re.fullmatch('[a-f0-9]{32}', job_id):
        raise web.HTTPNotFound()
    state.get(job_id, 'video')
    destination = state.media / (job_id + '.mp4')
    async with state.video_locks.setdefault(job_id, asyncio.Lock()):
        if not destination.is_file():
            async with state.http.get(state.studio + '/api/media/' + job_id + '/result') as response:
                if response.status != 200:
                    raise web.HTTPNotFound()
                temporary = destination.with_suffix('.tmp')
                try:
                    with temporary.open('wb') as output:
                        async for chunk in response.content.iter_chunked(1024 * 1024):
                            output.write(chunk)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    return web.FileResponse(destination)


async def codex_models(request):
    state = request.app[STATE]
    async with CodexSession(state.data / 'codex-work') as codex:
        return web.json_response({'data': await codex.models()})


async def chat(request):
    state = request.app[STATE]
    body = await request.json()
    messages = body.get('messages', [])
    if not isinstance(messages, list) or not messages:
        raise ValueError('请提供对话消息')
    prompt, references = [], []
    for message in messages:
        content = message.get('content', '')
        if isinstance(content, list):
            texts = []
            for part in content:
                if part.get('type') == 'text':
                    texts.append(part.get('text', ''))
                elif part.get('type') == 'image_url':
                    references.append(await state.reference(part['image_url']['url']))
            content = '\n'.join(texts)
        prompt.append({key: value for key, value in {**message, 'content': content or ''}.items()
                       if key in ('role', 'content', 'tool_calls', 'tool_call_id', 'name')})
    text = ('以下 JSON 是按时间排列的对话历史，工具调用和结果已经执行。'
            '只继续处理最后一条用户请求；依据末尾最新工具结果决定下一步，不要重放历史调用。'
            '同一节点已经返回完整正文时直接据此分析或修改，不重复读取相同正文。'
            '正在生成的任务保持原任务，不重复提交；状态没有变化时结束本轮并说明等待状态。\n'
            + json.dumps(prompt, ensure_ascii=False))
    if len(text) > 500_000:
        raise ValueError('对话过长，请新建对话')
    stream = body.get('stream') is True
    response = None
    request_id = 'chatcmpl-' + uuid.uuid4().hex
    async def emit(delta, finish=None):
        chunk = {'id': request_id, 'object': 'chat.completion.chunk', 'created': int(time.time()),
                 'model': body.get('model', 'codex-local'),
                 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
        await response.write(('data: ' + json.dumps(chunk, ensure_ascii=False) + '\n\n').encode())
    async with CodexSession(state.data / 'codex-work') as codex:
        if stream:
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream', 'Cache-Control': 'no-store'})
            await response.prepare(request)
        try:
            result = await codex.run(text, model=None if body.get('model') == 'codex-local' else body.get('model'),
                effort=body.get('reasoning_effort'), images=references, tools=body.get('tools', []),
                on_delta=(lambda value: emit({'content': value})) if stream else None)
        except Exception as error:
            if not stream:
                raise
            await response.write(('data: ' + json.dumps({'error': {'message': str(error)}}, ensure_ascii=False) + '\n\n').encode())
            await response.write(b'data: [DONE]\n\n')
            return response
    finish = 'tool_calls' if result['tool_calls'] else 'stop'
    if stream:
        if result['tool_calls']:
            await emit({'tool_calls': [{**call, 'index': i} for i, call in enumerate(result['tool_calls'])]})
        await emit({}, finish)
        await response.write(b'data: [DONE]\n\n')
        return response
    return web.json_response({'id': request_id, 'object': 'chat.completion', 'created': int(time.time()),
        'model': result['model'], 'choices': [{'index': 0, 'message': {'role': 'assistant',
            'content': result['text'], **({'tool_calls': result['tool_calls']} if result['tool_calls'] else {})},
            'finish_reason': finish}]})


def create_app(data_dir=None, start_worker=True):
    app = web.Application(middlewares=[boundary], client_max_size=NATIVE_REFERENCE_MAX_IMAGES * MAX_IMAGE_BYTES * 4 // 3 + 1024 * 1024)
    app[STATE] = Runtime(data_dir or ROOT / 'data/local-runtime')
    async def lifetime(app):
        state = app[STATE]
        state.http = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1800))
        yield
        await state.compositions.close()
        await state.http.close()
        state.db.close()
    app.cleanup_ctx.append(lifetime)
    app.router.add_get('/status', status)
    app.router.add_get('/v1/models', models)
    app.router.add_get('/v1/codex/models', codex_models)
    app.router.add_post('/v1/images/generations', images)
    app.router.add_post('/v1/images/edits', images)
    app.router.add_post('/v1/videos', create_video)
    app.router.add_get('/v1/videos/{id}', get_video)
    app.router.add_post('/v1/videos/{id}/upscale', upscale_video)
    app.router.add_delete('/v1/videos/{id}', cancel_video)
    app.router.add_post('/v1/chat/completions', chat)
    app.router.add_get('/media/{kind:image|video}/{id}', media)
    register_compositions(app, STATE)
    return app


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=18291)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    web.run_app(create_app(), host='127.0.0.1', port=args.port, handler_cancellation=True)
