"""Verify a fresh checkout serves the resources needed by the browser."""

import re
import unittest
from unittest.mock import patch

from aiohttp.test_utils import TestClient, TestServer
from server import make_app


class ResourcesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.keys = patch.dict('os.environ', {'BOSON_API_KEY': '', 'DEEPSEEK_API_KEY': ''})
        self.keys.start()
        self.client = TestClient(TestServer(make_app()))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.keys.stop()

    async def test_missing_keys_are_reported_without_calling_clouds(self):
        response = await self.client.get('/api/status')
        state = await response.json()
        self.assertFalse(state['agentReady'])
        self.assertEqual(set(state['missing']), {'BOSON_API_KEY', 'DEEPSEEK_API_KEY'})
        response = await self.client.post('/api/session', json={'expertId': 'architecture', 'mode': 'agent'})
        self.assertEqual(response.status, 503)

    async def test_homepage_assets_and_avatar_are_available_without_keys(self):
        response = await self.client.get('/')
        self.assertEqual(response.status, 200)
        html = await response.text()
        paths = set(re.findall(r'(?:src|href)="(assets/[^"]+)"', html))
        self.assertTrue(paths)
        for path in paths:
            with self.subTest(path=path):
                response = await self.client.get('/' + path)
                self.assertEqual(response.status, 200)
                self.assertTrue(await response.read())
        response = await self.client.get('/avatar/frame.html')
        self.assertEqual(response.status, 200)
        for path in ('js/DHLiveMini.js', 'js/MiniLive2.js', 'js/frame.js',
                     'assets-higgs/01.mp4', 'assets-higgs/combined_data.json.gz',
                     'common/bs_texture_halfFace.png'):
            with self.subTest(path=path):
                response = await self.client.get('/avatar/' + path)
                self.assertEqual(response.status, 200)
                self.assertTrue(await response.read())
        response = await self.client.get('/avatar/DHLiveMini.wasm')
        self.assertEqual(response.status, 200)
        self.assertEqual(response.headers['Content-Type'], 'application/wasm')
        self.assertEqual((await response.read())[:4], b'\0asm')
