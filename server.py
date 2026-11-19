"""Independent DH_live expert: browser rendering, Higgs STT/TTS, DeepSeek."""
import asyncio
import audioop
import base64
import hashlib
import hmac
import io
import json
import logging
import math
import os
import re
import time
import wave
from contextlib import aclosing, suppress
from pathlib import Path
from secrets import token_urlsafe
from urllib.parse import urlsplit
from uuid import uuid4

from aiohttp import ClientError, ClientSession, ClientTimeout, FormData, WSMsgType, web
from dotenv import load_dotenv

from barge_in import BargeInConfirmation
from expert_voice import resolve_voice

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
LOG = logging.getLogger('dhlive-expert')
COOKIE = 'dhlive_expert_session'
MODES = ('agent', 'voice-agent', 'live-video', 'live-audio')
REALTIME_MODES = ('live-video', 'live-audio')
DURATION = int(os.getenv('SESSION_SECONDS', '300'))
MAX_SESSIONS = int(os.getenv('MAX_SESSIONS', '16'))
VOICE_ASR = os.getenv('VOICE_ASR_PROVIDER', 'higgs').strip().lower()
END = re.compile(r'[。！？!?；;]+|\.(?=\s)|\n+')
PHRASE = re.compile(r'[，、：,:]|\s')
INSTRUCTIONS = ('你叫架构专家，是一位擅长软件架构和技术方案的专家。用户询问身份时明确报出姓名和专业。'
                '跟随用户使用的语言，默认普通话。自然交流，通常一两句，用户要求时可以详细回答。'
                '答案会直接播报，请用简短的纯文本，避免 Markdown、代码块和控制标签。'
                '先用一句简短且有内容的话回答，再补充说明。')


class DemoError(Exception):
    pass


def boundary(text):
    for match in END.finditer(text):
        if len(text[:match.start()].strip()) >= 5:
            return match.end()
    # A short initial phrase avoids waiting for a long, fully synthesized sentence.
    if len(text) >= 40:
        breaks = list(PHRASE.finditer(text, 12, 40))
        return breaks[-1].end() if breaks else 40
    return 0


def service_error(service, status):
    hint = {401: '请检查服务密钥。', 402: '请检查账户余额。', 429: '服务繁忙，请稍后重试。'}
    return DemoError(f'{service} 请求失败（HTTP {status}）。{hint.get(status, "请稍后重试。")}')


async def stream_answer(http, question, history):
    url = os.getenv('DEEPSEEK_CHAT_URL', 'https://api.deepseek.com/chat/completions')
    try:
        async with http.post(url, headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']}, json={
            'model': os.getenv('DEEPSEEK_MODEL', 'deepseek-flash'), 'stream': True,
            'thinking': {'type': 'disabled'}, 'max_tokens': 512,
            'messages': [{'role': 'system', 'content': INSTRUCTIONS}, *history, {'role': 'user', 'content': question}],
        }, timeout=ClientTimeout(total=90, connect=12, sock_read=30), allow_redirects=False) as response:
            if response.status != 200:
                raise service_error('DeepSeek', response.status)
            finished = False
            async for line in response.content:
                if not line.startswith(b'data:'):
                    continue
                value = line[5:].strip()
                if value == b'[DONE]':
                    break
                event = json.loads(value)
                if event.get('error'):
                    raise DemoError('DeepSeek 返回错误，请重试。')
                for choice in event.get('choices', []):
                    if choice.get('finish_reason') == 'stop':
                        finished = True
                    elif choice.get('finish_reason'):
                        raise DemoError('回答没有完整生成，请换一种问法。')
                    if text := choice.get('delta', {}).get('content'):
                        yield text
            if not finished:
                raise DemoError('DeepSeek 回答中途断开，请重试。')
    except (ClientError, TimeoutError, ValueError, KeyError, TypeError) as error:
        raise DemoError('DeepSeek 连接失败或返回异常，请重试。') from error


async def synthesize(http, text, voice):
    url = os.getenv('BOSON_API_BASE', 'https://api.boson.ai/v1').rstrip('/') + '/audio/speech'
    pcm = bytearray()
    async with http.post(url, headers={'Authorization': 'Bearer ' + os.environ['BOSON_API_KEY']}, json={
        'model': 'higgs-tts-3', 'input': text, 'voice': voice,
        'response_format': 'pcm', 'stream': True, 'normalize': False,
    }, timeout=ClientTimeout(total=45, connect=12, sock_read=20), allow_redirects=False) as response:
        if response.status != 200:
            raise service_error('Higgs 语音合成', response.status)
        async for chunk in response.content.iter_chunked(4800):
            pcm.extend(chunk)
            if len(pcm) > 3_200_000:
                raise DemoError('语音片段过长，请重试。')
    if not pcm or len(pcm) % 2:
        raise DemoError('语音合成返回空音频或不完整音频。')
    converted, _ = audioop.ratecv(bytes(pcm), 2, 1, 24000, 16000, None)
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(converted)
    return base64.b64encode(out.getvalue()).decode('ascii'), len(converted) / 32000


class Conversation:
    def __init__(self, app, owner, mode, request_id=None):
        self.app, self.owner, self.mode = app, owner, mode
        self.id = uuid4().hex
        self.request_id=request_id
        self.created = time.monotonic()
        self.ws = self.stt = None
        self.stop = asyncio.Event()
        self.closed = asyncio.Event()
        self.reply = None
        self.turn = 0
        self.active_turn = None
        self.pending = None
        self.history = []
        self.detected = False
        self.rate_state = None
        self.barge_in = BargeInConfirmation() if mode in REALTIME_MODES else None
        self.voice_turn = None
        self.voice_asr_task = None
        if mode == 'voice-agent' and VOICE_ASR == 'deepseek':
            from voice_turn import VoiceTurn
            self.voice_turn = VoiceTurn()

    async def transcribe_voice_turn(self, pcm):
        audio = io.BytesIO()
        with wave.open(audio, 'wb') as wav:
            wav.setparams((1, 2, self.voice_turn.RATE, 0, 'NONE', 'not compressed'))
            wav.writeframes(pcm)
        payload = FormData()
        payload.add_field('file', audio.getvalue(), filename='question.wav', content_type='audio/wav')
        try:
            async with self.app['http'].post(
                'https://api.deepseek.com/v1/tools/asr',
                headers={'Authorization': 'Bearer ' + os.environ['DEEPSEEK_API_KEY']},
                data=payload, timeout=ClientTimeout(total=20, connect=8, sock_read=14),
                allow_redirects=False,
            ) as response:
                if response.status != 200:
                    raise service_error('DeepSeek 语音识别', response.status)
                result = await response.json()
                question = result.get('text') if isinstance(result, dict) else None
                if not isinstance(question, str) or not question.strip():
                    raise DemoError('没有识别到问题，请再说一次。')
            await self.ask(question)
        except (DemoError, ClientError, TimeoutError, ValueError) as error:
            message = str(error) if isinstance(error, DemoError) else 'DeepSeek 语音识别连接失败，请再试一次。'
            await self.send('error', message=message)

    async def send(self, event_type, **data):
        if self.ws and not self.ws.closed:
            await self.ws.send_json({'type': event_type, **data})

    async def interrupt(self):
        old_turn = self.active_turn
        self.active_turn = self.pending = None
        if self.reply and not self.reply.done():
            self.reply.cancel()
            await asyncio.gather(self.reply, return_exceptions=True)
        if old_turn is not None:
            with suppress(ConnectionError, RuntimeError):
                await self.send('interrupted', turnId=old_turn)

    async def begin_speech(self):
        if self.active_turn is not None:
            await self.interrupt()
        self.detected = True
        await self.send('recording')

    async def ask(self, question):
        if not isinstance(question, str) or not question.strip() or len(question) > 1000:
            await self.send('error', message='请输入 1 至 1000 字的问题。')
            return
        if self.active_turn is not None:
            await self.send('notice', message='当前回答正在播报，请稍后再问。')
            return
        if self.barge_in:
            self.barge_in.reset()
        self.turn += 1
        self.active_turn = self.turn
        self.reply = asyncio.create_task(self.answer(question.strip(), self.turn))

    async def answer(self, question, turn):
        segments = asyncio.Queue(maxsize=8)
        answer = ''
        sequence = 0
        await self.send('answering', turnId=turn, question=question)

        async def generate():
            nonlocal answer
            offset = 0
            history = list(self.history) if self.mode in REALTIME_MODES else []
            async with aclosing(stream_answer(self.app['http'], question, history)) as stream:
                async for delta in stream:
                    answer += delta
                    if len(answer) > 1000 or '<|' in answer:
                        raise DemoError('回答过长或包含播报控制标签，请换一种问法。')
                    while end := boundary(answer[offset:]):
                        offset += end
                        text = answer[offset-end:offset].strip()
                        if text:
                            await segments.put((text, answer[:offset]))
            if not answer.strip():
                raise DemoError('模型没有返回回答，请重试。')
            if tail := answer[offset:].strip():
                await segments.put((tail, answer))
            await segments.put(None)

        async def voice():
            nonlocal sequence
            while (segment := await segments.get()) is not None:
                text, caption = segment
                audio, seconds = await synthesize(self.app['http'], text, self.app['tts_voice'])
                if self.active_turn != turn:
                    return
                await self.send('audio', turnId=turn, sequence=sequence, answer=caption,
                                audio=audio, seconds=seconds)
                sequence += 1

        tasks = [asyncio.create_task(generate()), asyncio.create_task(voice())]
        try:
            async with asyncio.timeout(120):
                await asyncio.gather(*tasks)
            if self.active_turn == turn:
                self.pending = (question, answer)
                await self.send('complete', turnId=turn, answer=answer, segments=sequence)
        except asyncio.CancelledError:
            raise
        except (DemoError, ClientError, TimeoutError, ValueError) as error:
            message = str(error) if isinstance(error, DemoError) else '回答或语音连接失败，请重试。'
            if self.active_turn == turn:
                self.active_turn = self.pending = None
                await self.send('error', turnId=turn, message=message, answer=answer)
        except Exception:
            LOG.exception('Reply failed')
            self.active_turn = self.pending = None
            await self.send('error', turnId=turn, message='回答暂时不可用，请重试。')
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def transcripts(self):
        async for msg in self.stt:
            if msg.type != WSMsgType.TEXT:
                continue
            event = json.loads(msg.data)
            kind = event.get('type')
            if kind == 'error':
                raise DemoError('Higgs 语音识别返回错误，请重新接通。')
            if kind == 'input_audio_buffer.speech_started':
                if self.active_turn is not None:
                    if self.mode not in REALTIME_MODES:
                        continue
                    if self.barge_in and not self.barge_in.speech_started():
                        continue
                await self.begin_speech()
            elif kind == 'input_audio_buffer.speech_stopped':
                if self.barge_in:
                    self.barge_in.reset()
                if self.active_turn is None and self.detected:
                    await self.send('transcribing')
            elif self.active_turn is not None:
                continue
            elif self.detected and kind in ('conversation.item.input_audio_transcription.completed',
                                           'conversation.item.input_audio_transcription.failed'):
                self.detected = False
                await self.stt.send_json({'type': 'input_audio_buffer.clear'})
                question = (event.get('transcript') or '').strip()
                if kind.endswith('.failed') or not question:
                    await self.send('error', message='没有识别到问题，请再说一次。')
                else:
                    await self.ask(question)
        raise DemoError('语音识别连接已断开，请重新接通。')

    async def receive(self):
        async for msg in self.ws:
            if msg.type == WSMsgType.BINARY:
                if not (self.stt or self.voice_turn) or (self.mode == 'voice-agent' and self.active_turn is not None):
                    continue
                if not msg.data or len(msg.data) > 8192 or len(msg.data) % 2:
                    await self.ws.close(code=1008, message=b'Invalid PCM frame')
                    break
                if self.voice_turn is not None:
                    if self.voice_asr_task and not self.voice_asr_task.done():
                        continue
                    started, pcm = self.voice_turn.feed(msg.data)
                    if started:
                        await self.send('recording')
                    if pcm is not None:
                        self.voice_turn = type(self.voice_turn)()
                        await self.send('transcribing', audioSeconds=len(pcm) / 32000)
                        self.voice_asr_task = asyncio.create_task(self.transcribe_voice_turn(pcm))
                    continue
                if self.barge_in and self.barge_in.feed(msg.data):
                    await self.begin_speech()
                pcm, self.rate_state = audioop.ratecv(msg.data, 2, 1, 16000, 24000, self.rate_state)
                await self.stt.send_json({'type': 'input_audio_buffer.append',
                                          'audio': base64.b64encode(pcm).decode('ascii')})
            elif msg.type == WSMsgType.TEXT:
                try:
                    event = json.loads(msg.data)
                except ValueError:
                    await self.send('notice', message='请求格式异常。')
                    continue
                if not isinstance(event, dict):
                    continue
                kind = event.get('type')
                if kind == 'question' and self.mode == 'agent':
                    await self.ask(event.get('question'))
                elif kind == 'played' and event.get('turnId') == self.active_turn and self.pending:
                    if self.mode in REALTIME_MODES:
                        question, answer = self.pending
                        self.history.extend([{'role': 'user', 'content': question}, {'role': 'assistant', 'content': answer}])
                        del self.history[:-12]
                    turn = self.active_turn
                    self.pending = self.active_turn = None
                    await self.send('done', turnId=turn)
                elif kind == 'interrupt' and self.mode in REALTIME_MODES:
                    if self.barge_in:
                        self.barge_in.reset()
                    await self.interrupt()
                    await self.send('recording')
                elif kind == 'end':
                    break
        self.stop.set()

    async def configure_stt(self):
        url = os.getenv('BOSON_REALTIME_URL', 'wss://api.boson.ai/v1/realtime')
        self.stt = await self.app['http'].ws_connect(url, headers={
            'Authorization': 'Bearer ' + os.environ['BOSON_API_KEY']}, heartbeat=20)
        await self.stt.send_json({'type': 'session.update', 'session': {
            'model': 'higgs-realtime', 'output_modalities': ['text'], 'audio': {'input': {
                'turn_detection': {'type': 'server_vad', 'create_response': False, 'silence_duration_ms': 800},
                'transcription': {'model': 'higgs-stt-3.1'},
            }},
        }})
        async with asyncio.timeout(15):
            while True:
                msg = await self.stt.receive()
                if msg.type != WSMsgType.TEXT:
                    raise DemoError('语音识别连接失败，请重新接通。')
                event = json.loads(msg.data)
                if event.get('type') == 'error':
                    raise DemoError('语音识别配置失败，请检查服务配置。')
                # Higgs acknowledges the initial configuration with session.created.
                if event.get('type') in ('session.created', 'session.updated'):
                    return

    async def run(self):
        workers = [asyncio.create_task(self.receive()), asyncio.create_task(self.stop.wait())]
        try:
            if self.mode != 'agent' and self.voice_turn is None:
                configuration = asyncio.create_task(self.configure_stt())
                workers.append(configuration)
                completed, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
                if configuration not in completed or self.stop.is_set():
                    return
                configuration.result()
                workers.remove(configuration)
                workers.append(asyncio.create_task(self.transcripts()))
            ready = {'id': self.id, 'mode': self.mode, 'seconds': DURATION}
            if self.mode == 'voice-agent':
                ready['asrProvider'] = 'deepseek' if self.voice_turn is not None else 'higgs'
            await self.send('ready', **ready)
            workers.append(asyncio.create_task(asyncio.sleep(DURATION)))
            done, _ = await asyncio.wait(workers, return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                task.result()
            await self.send('ended', message='会话已结束。')
        except (DemoError, ClientError, TimeoutError, ValueError) as error:
            await self.send('fatal', message=str(error) if isinstance(error, DemoError) else '连接失败，请重新接通。')
        except Exception:
            LOG.exception('Session failed')
            with suppress(Exception):
                await self.send('fatal', message='连接异常，请重新接通。')
        finally:
            self.stop.set()
            self.app['sessions'].pop(self.id, None)
            if self.voice_asr_task:
                self.voice_asr_task.cancel()
                await asyncio.gather(self.voice_asr_task, return_exceptions=True)
            await self.interrupt()
            for task in workers:
                task.cancel()
            await asyncio.gather(*workers, return_exceptions=True)
            if self.stt:
                await self.stt.close()
            if self.ws and not self.ws.closed:
                await self.ws.close()
            self.closed.set()


def owner(request):
    token = request.cookies.get(COOKIE)
    if not token or len(token) > 160:
        return None
    parts=token.split('.')
    if len(parts)!=3 or not re.fullmatch('[0-9a-f]{32}',parts[0]) or not parts[1].isdigit() or not re.fullmatch('[0-9a-f]{64}',parts[2]):
        return None
    identifier,expires,signature=parts
    expiry=int(expires)
    if expiry<=time.time():
        return None
    expected=hmac.new(request.app['cookie_secret'],f'{identifier}.{expires}'.encode(),hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature,expected):
        return None
    request.app['owners'][identifier]=time.monotonic()+max(0,expiry-time.time())
    return identifier


def visitor_cookie(request,response,identifier):
    expires=int(time.time())+43200
    payload=f'{identifier}.{expires}'
    signature=hmac.new(request.app['cookie_secret'],payload.encode(),hashlib.sha256).hexdigest()
    request.app['owners'][identifier]=time.monotonic()+43200
    response.set_cookie(COOKIE,f'{payload}.{signature}',httponly=True,samesite='Strict',max_age=43200,secure=request.secure)


async def index(request):
    response = web.FileResponse(public_root() / 'web/index.html', headers={'Cache-Control': 'no-store'})
    if not owner(request):
        visitor_cookie(request,response,uuid4().hex)
    return response


async def status(request):
    missing = [key for key in ('BOSON_API_KEY', 'DEEPSEEK_API_KEY') if not os.getenv(key, '').strip()]
    return web.json_response({'missing': missing, 'error': None, 'agentReady': not missing,
        'user': '访客' if owner(request) else None, 'renderer': 'DH_live mini 2.0',
        'experts': [{'id': 'architecture', 'name': '架构专家', 'specialty': '软件架构与技术方案'}],
        'activeSessions': len(request.app['sessions']), 'webRelease':public_root().name}, headers={'Cache-Control': 'no-store'})


async def start(request):
    token = owner(request)
    origin=request.headers.get('Origin')
    if origin and urlsplit(origin).netloc!=request.host:
        raise web.HTTPForbidden()
    if request.content_type != 'application/json':
        raise web.HTTPUnsupportedMediaType()
    try:
        body = await request.json()
    except ValueError:
        body = None
    if not isinstance(body, dict) or body.get('expertId') != 'architecture' or body.get('mode') not in MODES:
        return web.json_response({'error': '请选择有效的专家和互动方式。'}, status=400)
    request_id=body.get('requestId')
    if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,64}',request_id)):
        return web.json_response({'error':'连接请求无效，请重新开始。'},status=400)
    if not all(os.getenv(key, '').strip() for key in ('DEEPSEEK_API_KEY', 'BOSON_API_KEY')):
        return web.json_response({'error': '服务端配置未完成。'}, status=503)
    new_visitor=not token
    if new_visitor:
        token=uuid4().hex
    sessions = request.app['sessions']
    for existing in list(sessions.values()):
        if existing.owner!=token:
            continue
        if request_id and existing.request_id==request_id:
            return web.json_response({'id':existing.id,'wsPath':f'api/session/{existing.id}/ws'})
        if request_id and existing.ws is None:
            existing.stop.set()
            sessions.pop(existing.id,None)
        else:
            return web.json_response({'error': '当前浏览器已有会话，请先结束。'}, status=409)
    if len(sessions) >= MAX_SESSIONS:
        return web.json_response({'error': '当前会话较多，请稍后再试。'}, status=429)
    call = Conversation(request.app, token, body['mode'],request_id)
    sessions[call.id] = call
    response=web.json_response({'id': call.id, 'wsPath': f'api/session/{call.id}/ws'})
    if new_visitor:
        visitor_cookie(request,response,token)
    return response


async def diagnostics(request):
    visitor = owner(request)
    token = visitor or '_anonymous'
    origin=request.headers.get('Origin')
    if origin and urlsplit(origin).netloc!=request.host:
        raise web.HTTPForbidden()
    if request.content_type != 'application/json':
        raise web.HTTPUnsupportedMediaType()
    try:
        body = await request.json()
    except ValueError:
        raise web.HTTPBadRequest()
    if not isinstance(body, dict):
        raise web.HTTPBadRequest()
    now = time.monotonic()
    started, count = request.app['diagnostic_rates'].get(token, (now, 0))
    if now - started > 60:
        started, count = now, 0
    if count >= 60:
        return web.Response(status=204)
    request.app['diagnostic_rates'][token] = (started, count + 1)
    data = {'browser': request.headers.get('User-Agent', '')[:220], 'visitor':bool(visitor)}
    for field in ('client', 'build', 'mode', 'state', 'component', 'code', 'permission'):
        value = body.get(field)
        if isinstance(value, str) and re.fullmatch(r'[A-Za-z0-9_-]{1,48}', value):
            data[field] = value
    for field in ('secure', 'top', 'mic', 'worklet', 'wasm'):
        if isinstance(body.get(field), bool):
            data[field] = body[field]
    for field in ('frames', 'peak', 'mediaState', 'mediaTime', 'nativeError'):
        value = body.get(field)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 <= value <= 1000000:
            data[field] = round(value, 3)
    # Never log cookies, questions, transcripts, audio, or arbitrary payload fields.
    LOG.info('ClientDiagnostic %s', json.dumps(data, ensure_ascii=True))
    return web.Response(status=202)


def find_call(request):
    call = request.app['sessions'].get(request.match_info['id'])
    if not owner(request) or not call or call.owner != owner(request):
        raise web.HTTPNotFound()
    return call


async def socket(request):
    call = find_call(request)
    origin = request.headers.get('Origin')
    if origin and urlsplit(origin).netloc != request.host:
        raise web.HTTPForbidden()
    if call.ws is not None or call.stop.is_set():
        raise web.HTTPConflict()
    call.ws = web.WebSocketResponse(heartbeat=20, max_msg_size=65536, compress=False)
    try:
        await call.ws.prepare(request)
    except BaseException:
        call.stop.set()
        request.app['sessions'].pop(call.id,None)
        call.closed.set()
        raise
    await call.run()
    return call.ws


async def end(request):
    if not owner(request):
        raise web.HTTPNotFound()
    if request.match_info['id'] not in request.app['sessions']:
        return web.Response(status=204)
    call = find_call(request)
    call.stop.set()
    if call.ws:
        with suppress(TimeoutError):
            await asyncio.wait_for(call.closed.wait(), 5)
    else:
        request.app['sessions'].pop(call.id, None)
    return web.Response(status=204)


async def wasm(request):
    return await wasm_file(request, request.app['public_root'] / 'avatar')


async def release_wasm(request):
    directory = ROOT / 'releases' / request.match_info['version'] / 'avatar'
    if not (directory / 'DHLiveMini.wasm').is_file():
        raise web.HTTPNotFound()
    return await wasm_file(request, directory, immutable=True)


async def wasm_file(request, directory, *, immutable=False):
    compressed = 'gzip' in request.headers.get('Accept-Encoding', '')
    headers = {'Cache-Control': 'public, max-age=31536000, immutable' if immutable else 'public, max-age=86400', 'Vary': 'Accept-Encoding', 'Content-Type': 'application/wasm'}
    if compressed:
        headers['Content-Encoding'] = 'gzip'
    return web.FileResponse(directory / ('DHLiveMini.wasm.gz' if compressed else 'DHLiveMini.wasm'), headers=headers)


def public_root():
    marker = ROOT / 'active-release'
    if not marker.exists():
        return ROOT
    version = marker.read_text().strip()
    if not re.fullmatch(r'[0-9a-f]{16}', version):
        raise RuntimeError('Invalid web release marker')
    directory = ROOT / 'releases' / version
    if not (directory / 'web/index.html').is_file():
        raise RuntimeError('Web release is incomplete')
    return directory


async def resources(app):
    app['sessions'], app['owners'] = {}, {}
    app['diagnostic_rates'] = {}
    async def expire():
        while True:
            await asyncio.sleep(10)
            now = time.monotonic()
            for token, expires in list(app['owners'].items()):
                if expires < now:
                    app['owners'].pop(token, None)
                    app['diagnostic_rates'].pop(token, None)
            for call in list(app['sessions'].values()):
                if call.ws is None and now - call.created > 30:
                    app['sessions'].pop(call.id, None)
    async with ClientSession(timeout=ClientTimeout(total=None, connect=12)) as http:
        app['http'] = http
        app['tts_voice'] = None
        if all(os.getenv(key, '').strip() for key in ('BOSON_API_KEY', 'DEEPSEEK_API_KEY')):
            app['tts_voice'] = await resolve_voice(http, os.getenv('BOSON_API_BASE', 'https://api.boson.ai/v1'),
                                                   os.environ['BOSON_API_KEY'], os.getenv('BOSON_VOICE', 'expert'))
            LOG.info('已配置播报音色：%s', app['tts_voice'])
        expiry = asyncio.create_task(expire())
        yield
        expiry.cancel()
        await asyncio.gather(expiry, return_exceptions=True)
        calls = list(app['sessions'].values())
        for call in calls:
            call.stop.set()
        with suppress(TimeoutError):
            await asyncio.wait_for(asyncio.gather(*(call.closed.wait() for call in calls if call.ws)), 10)


def make_app():
    app = web.Application(client_max_size=32768)
    app['cookie_secret']=(os.getenv('SESSION_SIGNING_KEY','').strip() or token_urlsafe(48)).encode()
    app['public_root'] = public_root()
    app.cleanup_ctx.append(resources)
    app.add_routes([web.get('/', index), web.get('/api/status', status), web.post('/api/session', start),
        web.post('/api/diagnostics', diagnostics),
        web.get('/api/session/{id}/ws', socket), web.delete('/api/session/{id}', end),
        web.get('/avatar/DHLiveMini.wasm', wasm),
        web.get('/releases/{version:[0-9a-f]{16}}/avatar/DHLiveMini.wasm', release_wasm)])
    app.router.add_static('/assets', app['public_root'] / 'web')
    app.router.add_static('/avatar', app['public_root'] / 'avatar')
    if (ROOT / 'releases').exists():
        app.router.add_static('/releases', ROOT / 'releases')
    return app


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    web.run_app(make_app(), host=os.getenv('HOST', '127.0.0.1'), port=int(os.getenv('PORT', '8789')), access_log=None)
