"""Verify expert voice reuse, account setup, and explicit voice overrides."""

import base64
import io
import unittest
import wave

from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from expert_voice import EXISTING_VOICE, resolve_voice


class ExpertVoiceTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.lookup_status = 200
        self.create_status = 200
        self.lookups = 0
        self.uploads = []

        async def lookup(request):
            self.lookups += 1
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
            return web.json_response({'voice_id': EXISTING_VOICE}, status=self.lookup_status)

        async def create(request):
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
            self.uploads.append(await request.json())
            return web.json_response({'voice_id': 'voice_registered_in_this_account'}, status=self.create_status)

        app = web.Application()
        app.add_routes([web.get('/v1/audio/voices/{voice}', lookup), web.post('/v1/audio/voices', create)])
        self.api = TestServer(app)
        await self.api.start_server()
        self.http = ClientSession()
        self.base = str(self.api.make_url('/v1'))

    async def asyncTearDown(self):
        await self.http.close()
        await self.api.close()

    async def test_original_account_reuses_exact_voice(self):
        self.assertEqual(await resolve_voice(self.http, self.base, 'test-key'), EXISTING_VOICE)
        self.assertEqual(self.lookups, 1)
        self.assertEqual(self.uploads, [])

    async def test_other_account_registers_bundled_reference(self):
        self.lookup_status = 404
        voice = await resolve_voice(self.http, self.base, 'test-key')
        self.assertEqual(voice, 'voice_registered_in_this_account')
        self.assertEqual(len(self.uploads), 1)
        uploaded = self.uploads[0]
        self.assertEqual(uploaded['ref_text'], '你好，云娟。今天想聊点什么呢？')
        with wave.open(io.BytesIO(base64.b64decode(uploaded['ref_audio']))) as audio:
            self.assertGreaterEqual(audio.getnframes() / audio.getframerate(), 3)
            self.assertEqual(audio.getnchannels(), 1)

    async def test_explicit_voice_skips_expert_setup(self):
        for choice in ('default', 'voice_user_selected'):
            self.assertEqual(await resolve_voice(self.http, self.base, 'test-key', choice), choice)
        self.assertEqual(self.lookups, 0)
        self.assertEqual(self.uploads, [])

    async def test_invalid_key_does_not_upload_or_fall_back(self):
        self.lookup_status = 401
        with self.assertRaisesRegex(RuntimeError, 'BOSON_API_KEY'):
            await resolve_voice(self.http, self.base, 'test-key')
        self.assertEqual(self.uploads, [])

    async def test_voice_quota_failure_is_reported(self):
        self.lookup_status = 404
        self.create_status = 403
        with self.assertRaisesRegex(RuntimeError, '音色数量上限'):
            await resolve_voice(self.http, self.base, 'test-key')
