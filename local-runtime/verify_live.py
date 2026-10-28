"""Real localhost acceptance; completed checks are reusable on a later invocation."""
import argparse
import asyncio
import base64
import io
import json
import time
from pathlib import Path

import aiohttp
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data/local-runtime'
BASE = 'http://127.0.0.1:3000/api/local-runtime'
REPORT = DATA / 'verification.json'


async def main(only, force=False):
    report = json.loads(REPORT.read_text(encoding='utf-8')) if REPORT.exists() else {}
    if force:
        for name in only:
            report.pop(name, None)
    def save(name, value):
        report[name] = value
        REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(name, json.dumps(value, ensure_ascii=False), flush=True)
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=1800),
                                     headers={'Origin': 'http://127.0.0.1:3000'}) as http:
        async def call(method, path, **kwargs):
            async with http.request(method, BASE + path, **kwargs) as response:
                payload = await response.json(content_type=None)
                if response.status >= 400:
                    raise RuntimeError(f'{response.status}: {str(payload)[:1500]}')
                return payload
        async def check(name, work):
            if name not in only or report.get(name, {}).get('ok'):
                return
            started = time.monotonic()
            try:
                result = await work()
                save(name, {'ok': True, 'seconds': round(time.monotonic() - started, 2), **result})
            except Exception as error:
                save(name, {'ok': False, 'seconds': round(time.monotonic() - started, 2), 'error': str(error)})
        async def text():
            value = await call('POST', '/v1/chat/completions', json={
                'model': 'codex-local', 'messages': [{'role': 'user', 'content': 'Reply with exactly CANVAS_LOCAL_OK. Do not use tools.'}]})
            content = value['choices'][0]['message']['content']
            assert 'CANVAS_LOCAL_OK' in content, content
            return {'model': value['model'], 'reply': content}
        async def http_contract():
            profile = await call('GET', '/profile')
            assert profile['baseUrl'] == BASE + '/v1', profile
            async with http.post(BASE + '/v1/chat/completions', json={'messages': []}) as response:
                assert response.status == 400, response.status
            async with http.get(BASE + '/v1/models', headers={'Origin': 'https://untrusted.example'}) as response:
                assert response.status == 403, response.status
            async with http.get(BASE + '/profile', headers={'Host': 'untrusted.example'}) as response:
                assert response.status == 403, response.status
            async with http.delete(BASE + '/v1/videos/' + 'a' * 32) as response:
                assert response.status == 404, response.status
            return {'same_origin_profile': True, 'browser_origin_accepted': True,
                    'untrusted_origin_rejected': True, 'unowned_cancel_rejected': True}
        async def tools():
            value = await call('POST', '/v1/chat/completions', json={
                'model': 'codex-local', 'messages': [{'role': 'user', 'content': 'Call canvas_echo with text equal to canvas-ok. Use that tool, do not respond with text.'}],
                'tools': [{'type': 'function', 'function': {'name': 'canvas_echo', 'description': 'Echo a canvas test string.',
                    'parameters': {'type': 'object', 'properties': {'text': {'type': 'string'}}, 'required': ['text'], 'additionalProperties': False}}}]})
            choice = value['choices'][0]
            calls = choice['message']['tool_calls']
            assert choice['finish_reason'] == 'tool_calls' and calls[0]['function']['name'] == 'canvas_echo', choice
            assert json.loads(calls[0]['function']['arguments'])['text'] == 'canvas-ok'
            return {'tool': calls[0]['function']}
        async def image(model):
            prompt = ('A single red origami crane on a warm ivory tabletop, soft studio light, '
                      'elegant product photography, subtle paper texture, square composition, no text, no watermark.')
            value = await call('POST', '/v1/images/generations', json={
                'model': model, 'prompt': prompt, 'size': '1024x1024', 'n': 1})
            raw = base64.b64decode(value['data'][0]['b64_json'])
            path = DATA / (model + '-acceptance.png')
            path.write_bytes(raw)
            with Image.open(io.BytesIO(raw)) as picture:
                picture.load()
                width, height = picture.size
                assert min(width, height) >= 512
            return {'width': width, 'height': height, 'bytes': len(raw), 'file': str(path), 'url': value['data'][0]['url']}
        async def video():
            prior = report.get('h3', {})
            job_id = prior.get('id')
            if not job_id:
                job = await call('POST', '/v1/videos', json={'model': 'local-h3-turbo', 'size': '864x480', 'seconds': '5',
                    'prompt': 'Cinematic close-up of a red origami crane on an ivory tabletop. A gentle breeze moves the paper wings. Slow camera dolly forward, warm studio sunlight, detailed paper fibers. Soft airy room ambience. No text or watermark.'})
                job_id = job['id']
                save('h3', {'ok': False, 'id': job_id, 'status': job['status']})
            while True:
                job = await call('GET', '/v1/videos/' + job_id)
                if job['status'] == 'failed':
                    raise RuntimeError(json.dumps(job, ensure_ascii=False))
                if job['status'] == 'completed':
                    break
                await asyncio.sleep(5)
            async with http.get('http://127.0.0.1:3000' + job['video_url']) as response:
                response.raise_for_status()
                path = DATA / 'h3-acceptance.mp4'
                path.write_bytes(await response.read())
            import av
            with av.open(str(path)) as container:
                stream = container.streams.video[0]
                frames = sum(1 for _ in container.decode(video=0))
                width, height, rate = stream.width, stream.height, float(stream.average_rate)
            with av.open(str(path)) as container:
                audio_frames = sum(1 for _ in container.decode(audio=0)) if container.streams.audio else 0
            assert (width, height) == (864, 480) and frames >= 120 and audio_frames > 0
            async with http.get('http://127.0.0.1:3000' + job['video_url'], headers={'Range': 'bytes=0-1023'}) as response:
                assert response.status == 206
            return {'id': job_id, 'file': str(path), 'url': job['video_url'], 'width': width, 'height': height,
                    'frames': frames, 'fps': rate, 'audio_frames': audio_frames, 'bytes': path.stat().st_size}
        await asyncio.gather(check('http-contract', http_contract), check('codex-text', text), check('codex-tools', tools),
            check('qwen', lambda: image('qwen-image-2.1')), check('codex-image', lambda: image('codex-image')), check('h3', video))
    if any(name not in report or not report[name].get('ok') for name in only):
        raise SystemExit(1)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--only', nargs='+', default=['http-contract', 'codex-text', 'codex-tools', 'qwen', 'codex-image', 'h3'])
    parser.add_argument('--force', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(args.only, args.force))
