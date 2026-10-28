"""Bounded local Codex app-server sessions; authentication stays inside Codex."""
import asyncio
import base64
import contextlib
import json
import os
import shutil
from pathlib import Path


class CodexSession:
    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.process = None
        self.pending = {}
        self.events = asyncio.Queue()
        self.next_id = 0
        self.reader = None
        self.errors = None
        self.stderr = bytearray()

    async def __aenter__(self):
        self.workspace.mkdir(parents=True, exist_ok=True)
        binary = os.environ.get('CANVAS_CODEX_BINARY') or shutil.which('codex')
        if not binary:
            raise RuntimeError('未找到本机 Codex，请先安装并登录 Codex')
        args = [binary, 'app-server', '--listen', 'stdio://',
                '-c', 'features.shell_tool=false', '-c', 'features.apps=false',
                '-c', 'features.plugins=false', '-c', 'features.browser_use=false',
                '-c', 'features.computer_use=false', '-c', 'features.multi_agent=false']
        self.process = await asyncio.create_subprocess_exec(
            *args, cwd=self.workspace, stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            limit=32 * 1024 * 1024, **({'creationflags': 0x08000000} if os.name == 'nt' else {}))
        self.reader = asyncio.create_task(self._read())
        self.errors = asyncio.create_task(self._stderr())
        await self.request('initialize', {
            'clientInfo': {'name': 'infinite-canvas-local', 'title': '无限画布本机接口', 'version': '0.1.0'},
            'capabilities': {'experimentalApi': True}})
        self.write({'method': 'initialized'})
        return self

    def write(self, message):
        self.process.stdin.write((json.dumps(message, ensure_ascii=False) + '\n').encode())

    async def request(self, method, params):
        self.next_id += 1
        future = asyncio.get_running_loop().create_future()
        self.pending[self.next_id] = future
        self.write({'id': self.next_id, 'method': method, 'params': params})
        try:
            return await asyncio.wait_for(future, 90)
        finally:
            self.pending.pop(self.next_id, None)

    async def _stderr(self):
        while chunk := await self.process.stderr.read(4096):
            self.stderr.extend(chunk)
            del self.stderr[:-16000]

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                message = json.loads(line)
                if message.get('method'):
                    await self.events.put(message)
                elif message.get('id') in self.pending:
                    future = self.pending[message['id']]
                    if not future.done():
                        if message.get('error'):
                            future.set_exception(RuntimeError(message['error']['message']))
                        else:
                            future.set_result(message.get('result'))
        finally:
            error = RuntimeError('本机 Codex 已断开，请确认登录状态后重试')
            for future in self.pending.values():
                if not future.done():
                    future.set_exception(error)
            await self.events.put({'method': 'local/disconnected', 'error': str(error)})

    async def models(self):
        items, cursor = [], None
        while True:
            page = await self.request('model/list', {'limit': 100, 'cursor': cursor, 'includeHidden': False})
            items.extend(page['data'])
            cursor = page.get('nextCursor')
            if not cursor:
                return items

    async def run(self, prompt, *, model=None, effort=None, images=(), tools=(), image=False, on_delta=None):
        account = await self.request('account/read', {'refreshToken': False})
        if account.get('requiresOpenaiAuth') and not account.get('account'):
            raise RuntimeError('本机 Codex 尚未登录，请运行 codex login')
        catalog = await self.models()
        if not catalog:
            raise ValueError('当前 Codex 账号没有可用模型')
        selected = (next((m for m in catalog if m.get('isDefault')), catalog[0])
                    if not model or model == 'codex-local' else next((m for m in catalog if m['model'] == model), None))
        if not selected:
            raise ValueError('所选 Codex 模型当前不可用，请重新选择')
        model = selected['model']
        effort = selected.get('defaultReasoningEffort') if effort is None else effort
        if effort not in [item['reasoningEffort'] for item in selected.get('supportedReasoningEfforts', [])]:
            raise ValueError('所选模型不支持该推理强度，请重新选择')
        dynamic = [{'type': 'function', 'name': tool['function']['name'],
                    'description': tool['function'].get('description', ''),
                    'inputSchema': tool['function'].get('parameters', {'type': 'object', 'properties': {}})}
                   for tool in tools if tool.get('type') == 'function']
        thread = await self.request('thread/start', {
            'cwd': str(self.workspace), 'model': model, 'ephemeral': True,
            'approvalPolicy': 'never', 'sandbox': 'workspace-write' if image else 'read-only',
            'baseInstructions': ('You are the local language and image engine for Infinite Canvas. '
                'Follow the supplied conversation. Use only the supplied dynamic tools or image generation. '
                'Do not run shell commands, browse, modify source files, or contact other agents.'),
            'dynamicTools': dynamic,
            'config': {'features.image_generation': image, 'mcp_servers': {}}})
        inputs = [{'type': 'text', 'text': prompt}]
        inputs.extend({'type': 'localImage', 'path': str(Path(path).resolve())} for path in images)
        await self.request('turn/start', {'threadId': thread['thread']['id'], 'input': inputs, 'effort': effort})
        text, generated = '', []
        async with asyncio.timeout(900):
            while True:
                message = await self.events.get()
                method, params = message.get('method'), message.get('params', {})
                if method == 'item/agentMessage/delta':
                    delta = params.get('delta', '')
                    text += delta
                    if on_delta:
                        await on_delta(delta)
                elif method == 'item/tool/call' and 'id' in message:
                    # The browser owns these tools. Return the call without executing it here.
                    return {'text': text, 'images': [], 'model': model, 'tool_calls': [{
                        'id': params['callId'], 'type': 'function', 'function': {
                            'name': params['tool'], 'arguments': json.dumps(params['arguments'], ensure_ascii=False)}}]}
                elif method == 'item/completed':
                    item = params.get('item', {})
                    if item.get('type') == 'imageGeneration':
                        if item.get('failure') or item.get('status') in ('failed', 'error'):
                            raise RuntimeError('Codex 生图失败：' + json.dumps(item.get('failure'), ensure_ascii=False))
                        if item.get('savedPath'):
                            path = Path(item['savedPath'])
                            if path.is_file():
                                generated.append(path)
                        elif item.get('result'):
                            path = self.workspace / (item['id'] + '.png')
                            path.write_bytes(base64.b64decode(item['result']))
                            generated.append(path)
                    elif item.get('type') == 'agentMessage' and not text:
                        text = item.get('text', '')
                elif method == 'turn/completed':
                    turn = params['turn']
                    if turn['status'] != 'completed':
                        raise RuntimeError((turn.get('error') or {}).get('message') or 'Codex 任务未完成')
                    if image and not generated:
                        raise RuntimeError('Codex 未返回实际图像文件：' + text[:400])
                    return {'text': text, 'images': generated, 'tool_calls': [], 'model': model}
                elif method == 'local/disconnected':
                    raise RuntimeError(message['error'])
                elif 'id' in message:
                    self.write({'id': message['id'], 'error': {'code': -32601,
                               'message': '此接口不允许执行额外操作，请仅返回文本或生成图像'}})

    async def __aexit__(self, *_):
        if self.process and self.process.returncode is None:
            self.process.terminate()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self.process.wait(), 8)
            if self.process.returncode is None:
                self.process.kill()
                await self.process.wait()
        for task in (self.reader, self.errors):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
