'use strict';
const get = id => document.getElementById(id);
const tabs = [...document.querySelectorAll('[data-mode]')];
const topics = [...document.querySelectorAll('[data-topic]')];
const composer = get('composer');
const avatar = get('avatar');
const speechPlayer = new SpeechPlayer(get('voice'));
const MIC_SIGNAL_TIMEOUT_MS = 8000;
const MIC_SIGNAL_MIN_RMS = 160;
const STARTUP_MIC_MAX_SAMPLES = 56000;
const MIC_SEND_MAX_BYTES = 128000;
const MIC_REPLAY_MAX_QUEUED_SAMPLES = 96000;
const MIC_REPLAY_SOCKET_WATERMARK = 24000;
get('mic-wechat').hidden = !/MicroMessenger/i.test(navigator.userAgent);
const modes = {
  text: {api: 'agent', title: '文字问答', description: '输入问题，专家将播报答案', idle: '可以输入问题，或点选推荐话题'},
  voice: {api: 'voice-agent', title: '语音问答', description: '说完自动提交，播完再问下一题', start: '开始语音问答', ready: '请说出你的问题，说完后停一下。'},
  video: {api: 'live-video', title: '实时视频', description: '看专家画面，语音连续对话', start: '开始视频', ready: '麦克风已开启。专家回答时可以直接插话。'},
  audio: {api: 'live-audio', title: '实时通话', description: '纯语音连续对话', start: '开始通话', ready: '麦克风已开启。专家回答时可以直接插话。'},
};
let mode = 'text';
let current = null;
let transition = false;
let agentReady = false;
let phase = 'idle';
let hint = '';
let question = '';
let answer = '';
let avatarPromise = null;
let avatarLoadSerial = 0;

function render() {
  const config = modes[mode];
  const textMode = mode === 'text';
  const active = Boolean(current?.connected);
  const busy = ['connecting', 'recording', 'transcribing', 'answering', 'playing', 'finishing', 'blocked'].includes(phase);
  const label = {idle: textMode || mode === 'voice' ? '待提问' : '待接通', connecting: '正在连接',
    ready: textMode ? '待提问' : '已接通', recording: '正在听', transcribing: '正在转写',
    answering: '正在生成', playing: '正在播报', finishing: '本轮已播完', blocked: '等待播放', done: '回答已播报', error: '请重试'}[phase];
  get('title').textContent = config.title;
  get('description').textContent = config.description;
  get('badge').lastChild.textContent = ' ' + label;
  get('stage-state').textContent = active ? label : phase === 'connecting' ? '正在连接' : '待连接';
  get('audio-state').textContent = active || textMode || phase === 'connecting' ? label : '等待接通';
  get('audio-stage').toggleAttribute('data-active', phase === 'recording' || phase === 'playing');
  get('stage').hidden = mode !== 'video';
  get('audio-stage').hidden = mode === 'video';
  const videoReady = Boolean(current?.avatar && !avatar.hidden);
  avatar.toggleAttribute('data-loading', !videoReady);
  get('poster').hidden = mode !== 'video' || videoReady;
  get('question').textContent = question;
  get('answer').textContent = answer;
  get('turn').hidden = !question && !answer;
  get('answer-row').hidden = !answer;
  get('topics').hidden = !textMode || Boolean(question);
  composer.hidden = !textMode;
  composer.querySelector('button').disabled = !agentReady || transition || busy;
  topics.forEach(button => { button.disabled = composer.querySelector('button').disabled; });
  get('controls').hidden = textMode && !active;
  get('action').hidden = textMode || active;
  get('action').textContent = phase === 'connecting' ? '正在连接…' : config.start || '';
  get('action').disabled = !agentReady || transition || busy;
  get('hangup').hidden = !active;
  get('hangup').disabled = transition;
  get('hangup').textContent = ({text: '结束会话', voice: '结束语音问答', video: '结束视频', audio: '结束通话'})[mode];
  get('sound').hidden = !active || !current.needsAudio;
  get('hint').textContent = hint || config.idle || '允许麦克风后开始。';
  get('hint').toggleAttribute('data-error', phase === 'error');
  get('mic-status').hidden = !active || !current.micWarning;
  tabs.forEach(tab => {
    const selected = tab.dataset.mode === mode;
    tab.setAttribute('aria-selected', String(selected));
    tab.tabIndex = selected ? 0 : -1;
    tab.disabled = transition || phase === 'connecting';
    if (selected) get('panel').setAttribute('aria-labelledby', tab.id);
  });
}

function setPhase(next, message = '') {
  if (phase !== next) window.reportClientStatus?.({mode, state: next, component: 'app', code: 'phase'});
  phase = next; hint = message; render();
}
function clearTurn() { question = ''; answer = ''; }

function markMicSignal(session) {
  if (!session.connected || session.micHasSignal) return;
  session.micHasSignal = true;
  clearTimeout(session.micTimer);
  if (session.micWarning) {
    session.micWarning = false;
    render();
  }
}

function watchMicSignal(session, pcm) {
  if (!session.connected || session.micHasSignal || !pcm.length) return;
  let energy = 0;
  for (const sample of pcm) energy += sample * sample;
  const hasLevel = energy >= pcm.length * MIC_SIGNAL_MIN_RMS ** 2;
  // Two consecutive 64 ms frames avoid treating a single click as speech.
  session.micSignalFrames = hasLevel ? session.micSignalFrames + 1 : 0;
  if (session.micSignalFrames >= 2) markMicSignal(session);
}
function bufferStartupMic(session, pcm) {
  session.startupMic.push(pcm);
  session.startupMicSamples += pcm.length;
  while (session.startupMicSamples > STARTUP_MIC_MAX_SAMPLES) {
    session.startupMicSamples -= session.startupMic.shift().length;
  }
}

function sendMicFrame(session, pcm) {
  if (session.ws.readyState !== WebSocket.OPEN) return false;
  if (session.ws.bufferedAmount > MIC_SEND_MAX_BYTES) {
    void stop('网络传输过慢，请重新连接。', true);
    return false;
  }
  session.ws.send(pcm.buffer);
  return true;
}

function replayStartupMic(session) {
  if (current !== session || session.ws.readyState !== WebSocket.OPEN) return;
  if (session.mode === 'voice' && session.turn !== null) {
    session.startupMic.length = 0;
    session.startupMicSamples = 0;
  }
  while (session.startupMic.length && session.ws.bufferedAmount < MIC_REPLAY_SOCKET_WATERMARK) {
    const pcm = session.startupMic.shift();
    session.startupMicSamples -= pcm.length;
    if (!sendMicFrame(session, pcm)) return;
    watchMicSignal(session, pcm);
  }
  if (session.startupMic.length) {
    session.startupMicTimer = setTimeout(() => replayStartupMic(session), 20);
  } else {
    session.startupMic = null;
    session.startupMicTimer = null;
  }
}

function send(session, body) {
  if (current === session && session.ws?.readyState === WebSocket.OPEN) {
    session.ws.send(JSON.stringify(body));
    return true;
  }
  return false;
}
async function postJson(path, body, signal) {
  for (let attempt = 0; attempt < 3; attempt++) {
    const response = await waitWithDeadline(fetch(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body), signal}),
      {signal, timeout: 10000, message: '会话创建超时，请检查网络后重试。'});
    const json = (response.headers.get('Content-Type') || '').includes('application/json');
    if (!json && [502, 503, 504].includes(response.status) && attempt < 2) {
      await waitWithDeadline(new Promise(resolve => setTimeout(resolve, 1000)), {signal, timeout: 1500});
      continue;
    }
    if (!json) {
      const error = new Error(response.ok ? '服务响应异常，请重新连接。' : '服务暂时无法连接，请稍后重试。');
      error.code = 'HTTP' + response.status;
      throw error;
    }
    let data;
    try { data = await waitWithDeadline(response.json(), {signal, timeout: 5000, message: '会话响应超时，请重试。'}); }
    catch (error) { if (signal?.aborted) throw error; throw new Error('服务响应异常，请重新连接。'); }
    if (!response.ok) {
      const error = new Error(data?.error || '请求失败，请稍后重试。');
      error.code = 'HTTP' + response.status;
      throw error;
    }
    if (!data || typeof data !== 'object') throw new Error('服务响应异常，请重新连接。');
    return data;
  }
}
function release(id) {
  if (!id) return;
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3000);
  return fetch('api/session/' + id, {method: 'DELETE', keepalive: true, signal: controller.signal})
    .catch(() => {}).finally(() => clearTimeout(timer));
}
function activateAudio() {
  speechPlayer.unlock();
  return Promise.resolve();
}

async function ensureAvatar() {
  avatar.hidden = false;
  if (!avatarPromise) {
    const loaderId = String(++avatarLoadSerial);
    avatarPromise = new Promise((resolve, reject) => {
      const clean = () => { clearTimeout(timer); window.removeEventListener('message', onStatus); };
      const onStatus = event => {
        if (event.origin !== location.origin || event.source !== avatar.contentWindow || event.data?.type !== 'dhlive-avatar' || event.data.loaderId !== loaderId) return;
        const {status, message} = event.data;
        if (status === 'ready') { clean(); resolve(avatar.contentWindow); }
        else if (status === 'error') { clean(); reject(Object.assign(new Error(message || '专家画面加载失败，请重试。'), {code:'AvatarInitError'})); }
        else if (current?.mode === 'video' && !current.connected) {
          setPhase('connecting', current.recorder && !current.recorder.stream ? '请允许访问麦克风，画面正在加载。' : message || '正在准备专家画面。');
        }
      };
      const timer = setTimeout(() => { clean(); reject(Object.assign(new Error('专家画面加载超时，请检查网络后重试。'), {code:'AvatarTimeout'})); }, 25000);
      window.addEventListener('message', onStatus);
      avatar.src = `avatar/frame.html?v=20261115-video2&loaderId=${loaderId}`;
    }).then(frame => {
      if (current?.mode !== 'video') frame.setAvatarPaused(true);
      return frame;
    }).catch(error => {
      avatarPromise = null;
      avatar.hidden = true;
      avatar.removeAttribute('src');
      throw error;
    });
  }
  return avatarPromise;
}

function clearPlayback(session) {
  clearTimeout(session.finishTimer);
  session.playbackGeneration++;
  if (session.source) {
    speechPlayer.stop();
    session.source = null;
  }
  session.currentItem = null;
  session.needsAudio = false;
  session.avatar?.stopAvatarAudio(false);
  session.queue = [];
  session.playing = false;
  session.complete = false;
  session.turn = null;
}

async function stop(message = '会话已结束。', error = false) {
  const session = current;
  if (!session) return;
  if (error) {
    const code = message.includes('连接已断开') ? 'WebSocketClosed' : message.includes('传输过慢') ? 'UploadBackpressure'
      : message.includes('播放失败') ? 'PlaybackError' : 'SessionError';
    window.reportClientStatus?.({mode:session.mode,state:'error',component:'session',code,
      frames:session.micFrames,peak:session.micPeak});
  }
  current = null;
  transition = true;
  session.controller?.abort();
  clearTimeout(session.micTimer);
  clearTimeout(session.micReportTimer);
  clearTimeout(session.startupMicTimer);
  clearPlayback(session);
  session.avatar?.setAvatarPaused(true);
  avatar.hidden = true;
  session.ws?.close();
  session.rejectReady?.(new Error('会话已结束。'));
  await Promise.allSettled([session.recorder?.stop(), release(session.id)]);
  transition = false;
  setPhase(error ? 'error' : 'idle', message);
}

function acknowledge(session) {
  if (current !== session || !session.complete || session.playing || session.currentItem || session.needsAudio || session.queue.length || session.acknowledged) return;
  session.acknowledged = true;
  setPhase('finishing', '语音已播完，正在准备下一轮。');
  if (!send(session, {type: 'played', turnId: session.turn})) { void stop('连接已断开，请重新开始。', true); return; }
  session.finishTimer = setTimeout(() => {
    if (current === session && session.turn !== null) void stop('语音已播完，但连接未能恢复，请重新开始。', true);
  }, 10000);
}

async function playAudio(session) {
  if (current !== session || session.playing || session.needsAudio) return;
  if (!session.queue.length) { acknowledge(session); return; }
  const item = session.queue.shift();
  const generation = session.playbackGeneration;
  const valid = () => current === session && generation === session.playbackGeneration;
  session.playing = true;
  try {
    const bytes = Uint8Array.from(atob(item.audio), char => char.charCodeAt(0));
    session.currentItem = {...item, bytes};
    session.avatar?.prepareAvatarAudio(bytes);
    session.source = speechPlayer;
    answer = item.answer;
    setPhase('answering', '语音已准备好，正在启动声音。');
    speechPlayer.play(bytes, item.seconds, {
      onStarted: (clock, duration) => {
        if (!valid()) return;
        session.playing = true;
        session.needsAudio = false;
        session.avatar?.startAvatarAudio(clock, 0, duration);
        setPhase('playing', ['video', 'audio'].includes(session.mode) ? '专家正在回答，你可以直接开口打断。' : '专家正在播报答案。');
      },
      onBlocked: () => {
        if (!valid()) return;
        session.playing = false;
        session.needsAudio = true;
        session.avatar?.stopAvatarAudio(false);
        setPhase('blocked', '声音尚未启动，请点击“启用声音”。');
      },
      onEnded: () => {
        if (!valid()) return;
        session.source = session.currentItem = null;
        session.playing = false;
        session.needsAudio = false;
        session.avatar?.stopAvatarAudio();
        if (!session.queue.length && !session.complete) setPhase('answering', '正在准备后续语音。');
        void playAudio(session);
      },
    });
  } catch (error) {
    if (current !== session || generation !== session.playbackGeneration) return;
    void stop('语音播放失败，请重新接通。', true);
  }
}

function onEvent(event, session) {
  if (current !== session) return;
  switch (event.type) {
    case 'ready':
      session.voiceReady = true;
      session.resolveReady();
      if (session.mode === 'video' && !session.avatar) setPhase('connecting', '语音已接通，正在准备专家画面。');
      break;
    case 'recording':
      markMicSignal(session);
      clearTurn();
      setPhase('recording', '正在听你说。说完后会自动提交。');
      break;
    case 'transcribing': setPhase('transcribing', '正在转写你的问题。'); break;
    case 'answering':
      clearPlayback(session);
      session.turn = event.turnId;
      session.acknowledged = false;
      question = event.question;
      answer = '';
      setPhase('answering', '正在准备回答。');
      break;
    case 'audio':
      if (event.turnId !== session.turn) return;
      session.queue.push(event);
      void playAudio(session);
      break;
    case 'complete':
      if (event.turnId !== session.turn) return;
      session.complete = true;
      session.fullAnswer = event.answer;
      acknowledge(session);
      break;
    case 'done':
      if (event.turnId !== session.turn) return;
      answer = session.fullAnswer || answer;
      clearTimeout(session.finishTimer);
      session.turn = null;
      setPhase('done', '可以继续提问。');
      break;
    case 'interrupted':
      if (event.turnId !== session.turn) return;
      clearPlayback(session);
      clearTurn();
      setPhase('recording', '已打断上一轮，正在听你的新问题。');
      break;
    case 'error':
      window.reportClientStatus?.({mode, state: 'error', component: 'answer', code: 'ProviderError'});
      if (event.turnId !== undefined && event.turnId !== session.turn) return;
      clearPlayback(session);
      answer = event.answer || answer;
      setPhase('error', event.message);
      break;
    case 'notice': hint = event.message; render(); break;
    case 'ended': void stop(event.message); break;
    case 'fatal': void stop(event.message, true); break;
  }
}

async function start() {
  if (current || transition || !agentReady) return;
  const activation = activateAudio();
  const session = {mode, connected: false, queue: [], playing: false, source: null, turn: null,
    playbackGeneration: 0, complete: false, needsAudio: false, controller: new AbortController(),
    requestId: Date.now().toString(36) + '-' + Math.random().toString(36).slice(2)};
  session.micFrames = session.micPeak = session.micSignalFrames = 0;
  current = session;
  if (session.mode !== 'text') {
    session.recorder = new PCMAudioRecorder();
    session.startupMic = [];
    session.startupMicSamples = 0;
    session.recorder.onError = error => { if (current === session) void stop(error.message || '麦克风连接失败。', true); };
  }
  setPhase('connecting', '正在连接专家。');
  try {
    const signal = session.controller.signal;
    const image = session.mode === 'video' ? waitWithDeadline(ensureAvatar(), {signal, timeout: 26000,
      message: '专家画面加载超时，请检查网络后重试。'}).then(frame => {
        if (current !== session) return;
        session.avatar = frame;
        frame.setAvatarPaused(false);
        if (!session.voiceReady) setPhase('connecting', session.recorder?.stream ? '正在接通语音服务。' : '请允许浏览器访问麦克风。');
      }) : Promise.resolve();
    const voice = (async () => {
      if (session.recorder) {
        setPhase('connecting', '请允许浏览器访问麦克风。');
        await session.recorder.connect(pcm => {
          if (current !== session) return;
          session.micFrames++;
          for (let index = 0; index < pcm.length; index += 32) session.micPeak = Math.max(session.micPeak, Math.abs(pcm[index]));
          if (!session.connected) {
            bufferStartupMic(session, pcm);
            return;
          }
          watchMicSignal(session, pcm);
          if (session.mode === 'voice' && session.turn !== null) return;
          if (session.startupMic) {
            if (session.startupMicSamples + pcm.length > MIC_REPLAY_MAX_QUEUED_SAMPLES) {
              void stop('网络传输过慢，请重新连接。', true);
              return;
            }
            session.startupMic.push(pcm);
            session.startupMicSamples += pcm.length;
            return;
          }
          sendMicFrame(session, pcm);
        }, {signal});
      }
      if (current !== session) return;
      setPhase('connecting', '正在接通语音服务。');
      Object.assign(session, await postJson('api/session', {expertId: 'architecture', mode: modes[session.mode].api, requestId:session.requestId}, signal));
      if (current !== session) { await release(session.id); return; }
      const url = new URL(session.wsPath, location.href);
      url.protocol = location.protocol === 'https:' ? 'wss:' : 'ws:';
      session.ws = new WebSocket(url);
      session.ws.onmessage = message => {
        try { onEvent(JSON.parse(message.data), session); } catch { void stop('收到异常数据，请重新接通。', true); }
      };
      session.ws.onclose = () => { if (current === session) void stop('连接已断开，请重新接通。', true); };
      await waitWithDeadline(new Promise((resolve, reject) => {
        session.resolveReady = resolve;
        session.rejectReady = reject;
        session.ws.onerror = () => reject(Object.assign(new Error('无法连接语音服务，请重试。'), {code:'WebSocketConnectError'}));
      }), {signal, timeout: 20000, message: '语音服务连接超时，请重试。'});
    })();
    await waitWithDeadline(Promise.all([activation, image, voice]), {signal, timeout: 30000,
      message: '连接超时，请检查网络和麦克风权限后重试。'});
    if (current !== session) return;
    session.connected = true;
    setPhase('ready', modes[session.mode].ready);
    if (session.recorder) {
      replayStartupMic(session);
      if (current !== session) return;
      session.micReportTimer = setTimeout(() => {
        if (current === session) window.reportClientStatus?.({mode, state: 'sampled', component: 'microphone', code: 'InputSummary',
          frames: session.micFrames, peak: session.micPeak});
      }, 5000);
      session.micTimer = setTimeout(() => {
        if (current !== session || session.micHasSignal) return;
        session.micWarning = true;
        render();
        window.reportClientStatus?.({mode, state: 'warning', component: 'microphone',
          code: session.micFrames ? 'LowMicSignal' : 'NoInputFrames', frames: session.micFrames, peak: session.micPeak});
      }, MIC_SIGNAL_TIMEOUT_MS);
    }
    return current === session ? session : undefined;
  } catch (error) {
    if (current === session) window.reportClientStatus?.({mode, state: 'error', component: 'startup', code: error.code || error.name || 'Error',
      frames: session.micFrames, peak: session.micPeak});
    const messages = {NotAllowedError: '麦克风权限未开启，请允许后重试。', NotFoundError: '未找到麦克风，请检查设备。',
      NotReadableError: '无法读取麦克风，请检查设备是否被占用。', SecurityError: '请通过 HTTPS 使用麦克风。',
      NotSupportedError: '当前浏览器无法开启麦克风，请使用新版浏览器。'};
    if (current === session) await stop(messages[error.name] || error.message || '无法开始会话。', true);
  }
}

async function ask(text) {
  if (!text || transition || composer.querySelector('button').disabled) return;
  activateAudio();
  const session = current || await start();
  if (!session || current !== session) return false;
  question = text;
  answer = '';
  setPhase('answering', '正在准备回答。');
  return send(session, {type: 'question', question: text});
}

async function selectMode(next) {
  if (mode === next || transition) return;
  mode = next;
  clearTurn();
  if (current) await stop('');
  else setPhase('idle');
  tabs.find(tab => tab.dataset.mode === next)?.focus();
}
tabs.forEach((tab, index) => {
  tab.onclick = () => void selectMode(tab.dataset.mode);
  tab.onkeydown = event => {
    const next = {ArrowRight: (index + 1) % tabs.length, ArrowLeft: (index + tabs.length - 1) % tabs.length, Home: 0, End: tabs.length - 1}[event.key];
    if (next !== undefined) { event.preventDefault(); tabs[next].click(); }
  };
});
composer.onsubmit = async event => {
  event.preventDefault();
  const input = get('question-input');
  const value = input.value.trim();
  if (!value || composer.querySelector('button').disabled) return;
  const submitted = await ask(value);
  if (submitted && input.value.trim() === value) input.value = '';
};
topics.forEach(button => { button.onclick = () => void ask(button.dataset.topic); });
get('action').onclick = () => { clearTurn(); void start(); };
get('hangup').onclick = () => void stop();
get('sound').onclick = async () => {
  const session = current;
  if (!session) return;
  try {
    if (session.currentItem) {
      const restart = !session.needsAudio;
      session.avatar?.prepareAvatarAudio(session.currentItem.bytes);
      session.playing = true;
      session.needsAudio = false;
      setPhase('answering', '正在启动声音。');
      speechPlayer.retry(restart);
    } else {
      activateAudio();
      session.needsAudio = false;
      render();
      void playAudio(session);
    }
  } catch { setPhase('error', '浏览器未能播放声音，请检查权限。'); }
};
window.addEventListener('pagehide', () => {
  const session = current;
  current = null;
  transition = false;
  if (session) {
    session.controller?.abort();
    clearTimeout(session.micTimer);
    clearTimeout(session.micReportTimer);
    clearTimeout(session.startupMicTimer);
    clearPlayback(session);
    session.avatar?.setAvatarPaused(true);
    avatar.hidden = true;
    session.ws?.close();
    void session.recorder?.stop();
    void release(session.id);
  }
  phase = 'idle'; hint = '会话已结束，可以重新开始。'; render();
});
window.addEventListener('pageshow', event => { if (event.persisted) setPhase('idle', '会话已结束，可以重新开始。'); });
(async () => {
  try {
    const response = await fetch('api/status', {cache: 'no-store'});
    if (!response.ok) throw new Error('无法读取服务状态，请刷新重试。');
    const data = await response.json();
    if (data.missing.length) throw new Error('缺少服务端配置：' + data.missing.join('、'));
    if (!data.user) {
      const error = new Error('当前浏览器未能建立会话，请使用系统浏览器打开。');
      error.code = 'NoVisitor';
      throw error;
    }
    agentReady = data.agentReady;
    const expert = data.experts[0];
    ['expert-name', 'stage-expert', 'audio-expert'].forEach(id => { get(id).textContent = expert.name; });
    get('startup-status').hidden = true;
    get('workspace').hidden = false;
    setPhase('idle');
  } catch (error) {
    window.reportClientStatus?.({state: 'error', component: 'page', code: error.code || 'BootstrapError'});
    get('startup-status').textContent = error.message;
  }
})();
