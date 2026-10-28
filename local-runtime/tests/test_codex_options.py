import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from codex_rpc import CodexSession
from gateway import create_app

CATALOG = [
    {'model': 'model-a', 'displayName': 'Model A', 'isDefault': True, 'defaultReasoningEffort': 'low',
     'supportedReasoningEfforts': [{'reasoningEffort': value, 'description': value} for value in ['low', 'high', 'xhigh']]},
    {'model': 'model-b', 'displayName': 'Model B', 'isDefault': False, 'defaultReasoningEffort': 'medium',
     'supportedReasoningEfforts': [{'reasoningEffort': value, 'description': value} for value in ['low', 'medium']]},
]


class FakeRpc(CodexSession):
    def __init__(self):
        super().__init__('.')
        self.calls = []
        self.events.put_nowait({'method': 'turn/completed', 'params': {'turn': {'status': 'completed'}}})

    async def request(self, method, params):
        self.calls.append((method, params))
        return {'account/read': {'account': {}}, 'model/list': {'data': CATALOG, 'nextCursor': None},
                'thread/start': {'thread': {'id': 'thread-test'}}, 'turn/start': {}}[method]


class RpcSelection(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_does_not_expose_process_stderr_in_public_errors(self):
        rpc = CodexSession('.')
        reader = asyncio.StreamReader()
        reader.feed_eof()
        rpc.process = SimpleNamespace(stdout=reader)
        rpc.stderr.extend(b'private-login-details')
        future = asyncio.get_running_loop().create_future()
        rpc.pending[1] = future
        await rpc._read()
        with self.assertRaises(RuntimeError) as error:
            await future
        self.assertNotIn('private-login-details', str(error.exception))
        event = await rpc.events.get()
        self.assertEqual(event['method'], 'local/disconnected')
        self.assertNotIn('private-login-details', event['error'])

    async def test_selected_model_and_effort_reach_turn_start(self):
        rpc = FakeRpc()
        await rpc.run('hello', model='model-b', effort='medium')
        self.assertEqual(dict(rpc.calls)['thread/start']['model'], 'model-b')
        self.assertEqual(dict(rpc.calls)['turn/start']['effort'], 'medium')

    async def test_default_alias_uses_catalog_default_not_a_hardcoded_effort(self):
        rpc = FakeRpc()
        await rpc.run('hello', model='codex-local')
        self.assertEqual(dict(rpc.calls)['thread/start']['model'], 'model-a')
        self.assertEqual(dict(rpc.calls)['turn/start']['effort'], 'low')

    async def test_unavailable_model_and_unsupported_effort_fail_before_a_turn(self):
        for options in [{'model': 'missing'}, {'model': 'model-b', 'effort': 'xhigh'}, {'model': 'model-a', 'effort': 1}]:
            rpc = FakeRpc()
            with self.assertRaises(ValueError):
                await rpc.run('hello', **options)
            self.assertNotIn('turn/start', dict(rpc.calls))


class GatewaySelection(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.client = TestClient(TestServer(create_app(data_dir=Path(self.temp.name), start_worker=False)))
        await self.client.start_server()
        self.received = []
        received = self.received

        class FakeCodex:
            def __init__(self, *_): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *_): pass
            async def models(self): return CATALOG
            async def run(self, prompt, **options):
                received.append((prompt, options))
                return {'text': 'ok', 'tool_calls': [], 'model': options.get('model') or 'model-a'}
        self.mock = patch('gateway.CodexSession', FakeCodex)
        self.mock.start()

    async def asyncTearDown(self):
        self.mock.stop()
        await self.client.close()
        self.temp.cleanup()

    async def test_catalog_returns_actual_account_options(self):
        response = await self.client.get('/v1/codex/models')
        self.assertEqual(response.status, 200)
        self.assertEqual((await response.json())['data'], CATALOG)

    async def test_chat_preserves_selected_effort_in_plain_and_streaming_modes(self):
        for stream in [False, True]:
            response = await self.client.post('/v1/chat/completions', json={
                'model': 'model-b', 'reasoning_effort': 'medium', 'stream': stream,
                'messages': [{'role': 'user', 'content': 'hello'}]})
            await response.read()
            self.assertEqual(response.status, 200)
            self.assertEqual(self.received[-1][1].get('effort'), 'medium')
            self.assertEqual(self.received[-1][1]['model'], 'model-b')

    async def test_tool_result_keeps_name_and_call_id_in_replayed_history(self):
        response = await self.client.post('/v1/chat/completions', json={'model': 'codex-local', 'messages': [
            {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'call-one', 'function': {'name': 'get_node', 'arguments': '{"nodeId":"script"}'}}]},
            {'role': 'tool', 'tool_call_id': 'call-one', 'name': 'get_node', 'content': '{"ok":true,"node":{"text":"full script"}}'},
        ]})
        await response.read()
        prompt = self.received[-1][0]
        self.assertIn('tool_call_id', prompt)
        self.assertIn('call-one', prompt)
        self.assertIn('full script', prompt)


if __name__ == '__main__':
    unittest.main()
