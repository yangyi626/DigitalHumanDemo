"""Resolve the bundled expert voice in the caller's Boson account."""

import base64
from pathlib import Path

from aiohttp import ClientError, ClientTimeout

ROOT = Path(__file__).resolve().parent
EXISTING_VOICE = 'voice_6c8f074e79e938502509de626d3df93b4911df58391551cd85ab3b27197cdae7'


def voice_error(status):
    hint = {401: '请检查 BOSON_API_KEY。', 403: '请检查音色数量上限和账户权限。',
            429: '服务繁忙，请稍后重试。'}
    return RuntimeError(f'专家音色初始化失败（HTTP {status}）。' + hint.get(status, '请检查 Boson 服务后重试。'))


async def resolve_voice(http, api_base, api_key, choice='expert'):
    choice = choice.strip() or 'expert'
    if choice != 'expert':
        return choice
    endpoint = api_base.rstrip('/') + '/audio/voices'
    headers = {'Authorization': 'Bearer ' + api_key}
    timeout = ClientTimeout(total=45, connect=12)
    try:
        # The original account can use its exact existing expert voice. Other
        # accounts register the bundled reference under their own credentials.
        async with http.get(endpoint + '/' + EXISTING_VOICE, headers=headers,
                            timeout=timeout, allow_redirects=False) as response:
            if response.status == 200:
                data = await response.json()
                if data.get('voice_id') != EXISTING_VOICE:
                    raise RuntimeError('专家音色接口未返回预期的 Voice ID。')
                return EXISTING_VOICE
            if response.status != 404:
                raise voice_error(response.status)
        folder = ROOT / 'voice/expert'
        payload = {'ref_audio': base64.b64encode((folder / 'reference.wav').read_bytes()).decode('ascii'),
                   'ref_text': (folder / 'reference.txt').read_text(encoding='utf-8').strip(),
                   'title': 'DigitalHumanDemo 架构专家'}
        async with http.post(endpoint, headers=headers, json=payload,
                             timeout=timeout, allow_redirects=False) as response:
            if response.status != 200:
                raise voice_error(response.status)
            data = await response.json()
            voice = data.get('voice_id')
            if not isinstance(voice, str) or not voice.startswith('voice_'):
                raise RuntimeError('专家音色注册接口未返回有效的 Voice ID。')
            return voice
    except (ClientError, TimeoutError, ValueError) as error:
        raise RuntimeError('专家音色服务连接失败，请检查网络与 Boson 配置后重试。') from error
