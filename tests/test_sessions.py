"""Local fake clouds verify playback acknowledgement, cancellation and isolation."""
import asyncio
import base64
import io
import json
import math
import os
import struct
import sys
import unittest
import wave
from pathlib import Path
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from server import make_app


class SessionsTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stt = []
        self.handshake_gate = asyncio.Event()
        self.handshake_gate.set()
        self.questions = []
        self.synthesis_cancelled = asyncio.Event()

        async def llm(request):
            body = await request.json()
            self.questions.append(body['messages'])
            question = body['messages'][-1]['content']
            text = '我是架构专家。可以帮助你设计软件架构。' if question != '慢回答' else '这是一个需要取消的旧回答。'
            response = web.StreamResponse(headers={'Content-Type': 'text/event-stream'})
            await response.prepare(request)
            await response.write(('data: ' + json.dumps({'choices': [{'delta': {'content': text}, 'finish_reason': None}]}) + '\n\n').encode())
            await response.write(b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n')
            return response

        async def tts(request):
            body = await request.json()
            if '取消' in body['input']:
                await asyncio.sleep(.5)
            pcm = b''.join(struct.pack('<h', int(2000 * math.sin(i / 24000 * math.tau * 220))) for i in range(2400))
            return web.Response(body=pcm)

        async def stt(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            self.stt.append(ws)
            async for message in ws:
                event = json.loads(message.data)
                if event['type'] == 'session.update':
                    await self.handshake_gate.wait()
                    await ws.send_json({'type': 'session.created'})
            return ws

        fake = web.Application()
        fake.add_routes([web.post('/chat', llm), web.post('/audio/speech', tts), web.get('/realtime', stt)])
        self.cloud = TestServer(fake)
        await self.cloud.start_server()
        self.env = patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'test-deepseek', 'BOSON_API_KEY': 'test-boson',
            'BOSON_VOICE': 'default',
            'SESSION_SIGNING_KEY': 'test-session-signing-key',
            'DEEPSEEK_CHAT_URL': str(self.cloud.make_url('/chat')),
            'BOSON_API_BASE': str(self.cloud.make_url('/')), 'BOSON_REALTIME_URL': str(self.cloud.make_url('/realtime')).replace('http:', 'ws:')})
        self.env.start()
        self.asr = patch('server.VOICE_ASR', 'higgs')
        self.asr.start()
        self.client = TestClient(TestServer(make_app()))
        await self.client.start_server()
        await self.client.get('/')

    async def asyncTearDown(self):
        await self.client.close()
        await self.cloud.close()
        self.env.stop()
        self.asr.stop()

    async def connect(self, mode='agent', client=None):
        client = client or self.client
        response = await client.post('/api/session', json={'expertId': 'architecture', 'mode': mode})
        self.assertEqual(response.status, 200)
        data = await response.json()
        ws = await client.ws_connect('/' + data['wsPath'])
        self.assertEqual((await ws.receive_json())['type'], 'ready')
        return ws, data['id']

    async def reply(self, ws):
        events = []
        while True:
            event = await asyncio.wait_for(ws.receive_json(), 3)
            events.append(event)
            if event['type'] in ('complete', 'error'):
                return events

    async def test_audio_format_and_acknowledgement(self):
        ws, ident = await self.connect()
        await ws.send_json({'type': 'question', 'question': '你好'})
        events = await self.reply(ws)
        audio = [event for event in events if event['type'] == 'audio']
        self.assertGreater(len(audio), 0)
        for event in audio:
            with wave.open(io.BytesIO(base64.b64decode(event['audio']))) as wav:
                self.assertEqual((wav.getframerate(), wav.getnchannels(), wav.getsampwidth()), (16000, 1, 2))
                self.assertGreater(wav.getnframes(), 0)
        call = self.client.server.app['sessions'][ident]
        self.assertIsNotNone(call.active_turn)
        await ws.send_json({'type': 'played', 'turnId': events[-1]['turnId']})
        self.assertEqual((await ws.receive_json())['type'], 'done')
        self.assertIsNone(call.active_turn)
        await ws.close()

    async def test_ownership_and_one_session_per_browser(self):
        ws, ident = await self.connect()
        response = await self.client.post('/api/session', json={'expertId': 'architecture', 'mode': 'agent'})
        self.assertEqual(response.status, 409)
        other = TestClient(TestServer(make_app()))
        await other.start_server()
        try:
            self.assertEqual((await other.delete('/api/session/' + ident)).status, 404)
        finally:
            await other.close()
        # A second cookie on the same application cannot access the first session.
        from aiohttp import ClientSession, CookieJar
        async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as guest:
            await guest.get(self.client.make_url('/'))
            self.assertEqual((await guest.delete(self.client.make_url('/api/session/' + ident))).status, 404)
            response = await guest.post(self.client.make_url('/api/session'), json={'expertId': 'architecture', 'mode': 'agent'})
            self.assertEqual(response.status, 200)
        await ws.close()

    async def test_spoken_interruption_discards_old_reply_and_history(self):
        await self.verify_interruption_confirmation('live-video')

    async def test_call_ignores_short_noise_and_accepts_sustained_speech(self):
        await self.verify_interruption_confirmation('live-audio')

    async def verify_interruption_confirmation(self, mode):
        ws, ident = await self.connect(mode)
        stt = self.stt[-1]
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        self.assertEqual((await ws.receive_json())['type'], 'recording')
        await stt.send_json({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': '慢回答'})
        first = await ws.receive_json()
        self.assertEqual(first['type'], 'answering')
        call = self.client.server.app['sessions'][ident]
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        await ws.send_bytes(struct.pack('<h', 2400) * 1920)
        await asyncio.sleep(.04)
        self.assertEqual(call.active_turn, first['turnId'])
        self.assertFalse(call.detected)
        await stt.send_json({'type': 'input_audio_buffer.speech_stopped'})
        await stt.send_json({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': '杂音'})
        await asyncio.sleep(.02)
        self.assertEqual(len(self.questions), 1)
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        await ws.send_bytes(struct.pack('<h', 2400) * 2560)
        await asyncio.sleep(.04)
        self.assertEqual(call.active_turn, first['turnId'])
        await ws.send_bytes(struct.pack('<h', 2400) * 2560)
        self.assertEqual((await ws.receive_json())['type'], 'interrupted')
        self.assertEqual((await ws.receive_json())['type'], 'recording')
        await stt.send_json({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': '你好'})
        events = await self.reply(ws)
        self.assertTrue(all(event.get('turnId') != first['turnId'] for event in events))
        self.assertEqual(self.client.server.app['sessions'][ident].history, [])
        await ws.send_json({'type': 'played', 'turnId': events[-1]['turnId']})
        self.assertEqual((await ws.receive_json())['type'], 'done')
        self.assertEqual(len(self.client.server.app['sessions'][ident].history), 2)
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        await ws.receive_json()
        await stt.send_json({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': '继续'})
        events = await self.reply(ws)
        self.assertEqual(len(self.questions[-1]), 4)
        await ws.close()

    async def test_voice_qa_keeps_reply_until_played(self):
        ws, ident = await self.connect('voice-agent')
        stt = self.stt[-1]
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        await ws.receive_json()
        await stt.send_json({'type': 'conversation.item.input_audio_transcription.completed', 'transcript': '你好'})
        events = await self.reply(ws)
        await stt.send_json({'type': 'input_audio_buffer.speech_started'})
        await asyncio.sleep(.05)
        self.assertEqual(self.client.server.app['sessions'][ident].active_turn, events[-1]['turnId'])
        await ws.send_json({'type': 'played', 'turnId': events[-1]['turnId']})
        self.assertEqual((await ws.receive_json())['type'], 'done')
        await ws.close()

    async def test_cancel_during_stt_handshake_allows_immediate_retry(self):
        self.handshake_gate.clear()
        response = await self.client.post('/api/session', json={'expertId': 'architecture', 'mode': 'live-video'})
        data = await response.json()
        ws = await self.client.ws_connect('/' + data['wsPath'])
        async with asyncio.timeout(2):
            while not self.stt:
                await asyncio.sleep(.01)
        await ws.close()
        async with asyncio.timeout(2):
            while data['id'] in self.client.server.app['sessions']:
                await asyncio.sleep(.01)
        response = await self.client.post('/api/session', json={'expertId': 'architecture', 'mode': 'agent'})
        self.assertEqual(response.status, 200)
        self.handshake_gate.set()

    async def test_diagnostics_excludes_dialogue_and_credentials(self):
        with self.assertLogs('dhlive-expert', level='INFO') as records:
            response = await self.client.post('/api/diagnostics', json={
                'client':'test-client','mode':'video','state':'error','code':'NotAllowedError',
                'frames':0,'peak':0,'permission':'denied',
                'question':'DO_NOT_LOG_QUESTION','audio':'DO_NOT_LOG_AUDIO','cookie':'DO_NOT_LOG_COOKIE'})
        self.assertEqual(response.status,202)
        output=' '.join(records.output)
        self.assertIn('NotAllowedError',output)
        self.assertNotIn('DO_NOT_LOG',output)

    async def test_visitor_survives_process_restart_and_legacy_cookie_recovers(self):
        from aiohttp import ClientSession, CookieJar
        restarted=TestServer(make_app())
        await restarted.start_server()
        try:
            async with ClientSession(cookie_jar=CookieJar(unsafe=True)) as client:
                # Same cookie in a new application process, without opening its index.
                for cookie in self.client.session.cookie_jar:
                    client.cookie_jar.update_cookies({cookie.key:cookie.value})
                state=await (await client.get(restarted.make_url('/api/status'))).json()
                self.assertEqual(state['user'],'访客')
                response=await client.post(restarted.make_url('/api/session'),json={'expertId':'architecture','mode':'agent'})
                self.assertEqual(response.status,200)
                call=await response.json()
                await client.delete(restarted.make_url('/api/session/'+call['id']))
                # Legacy/expired cookies are renewed in the create-session response.
                client.cookie_jar.clear()
                client.cookie_jar.update_cookies({'dhlive_expert_session':'legacy-opaque-cookie'})
                response=await client.post(restarted.make_url('/api/session'),json={'expertId':'architecture','mode':'agent'})
                self.assertEqual(response.status,200)
                call=await response.json()
                ws=await client.ws_connect(restarted.make_url('/'+call['wsPath']))
                self.assertEqual((await ws.receive_json())['type'],'ready')
                await ws.close()
        finally:
            await restarted.close()

    async def test_repeated_connection_request_reuses_pending_session(self):
        body={'expertId':'architecture','mode':'agent','requestId':'connection-first-123'}
        first=await (await self.client.post('/api/session',json=body)).json()
        repeated=await (await self.client.post('/api/session',json=body)).json()
        self.assertEqual(first['id'],repeated['id'])
        body['requestId']='connection-second-123'
        response=await self.client.post('/api/session',json=body)
        self.assertEqual(response.status,200)
        second=await response.json()
        self.assertNotIn(first['id'],self.client.server.app['sessions'])
        ws=await self.client.ws_connect('/'+second['wsPath'])
        await ws.receive_json()
        body['requestId']='connection-third-123'
        self.assertEqual((await self.client.post('/api/session',json=body)).status,409)
        await ws.close()

    async def test_failed_websocket_upgrade_releases_session(self):
        body={'expertId':'architecture','mode':'agent'}
        first=await (await self.client.post('/api/session',json=body)).json()
        with patch('server.web.WebSocketResponse.prepare',AsyncMock(side_effect=ConnectionResetError('client disconnected'))):
            with self.assertLogs('aiohttp.server',level='ERROR'):
                response=await self.client.get('/'+first['wsPath'])
        self.assertEqual(response.status,500)
        self.assertNotIn(first['id'],self.client.server.app['sessions'])
        self.assertEqual((await self.client.post('/api/session',json=body)).status,200)


if __name__ == '__main__':
    unittest.main()
