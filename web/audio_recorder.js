function waitWithDeadline(promise, {signal, timeout = 20000, message = '连接超时，请重试。'} = {}) {
  return new Promise((resolve, reject) => {
    let timer;
    let settled = false;
    const abort = () => finish(reject, new DOMException('连接已取消', 'AbortError'));
    const finish = (settle, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener('abort', abort);
      settle(value);
    };
    Promise.resolve(promise).then(value => finish(resolve, value), error => finish(reject, error));
    if (signal?.aborted) { abort(); return; }
    signal?.addEventListener('abort', abort, {once: true});
    timer = setTimeout(() => finish(reject, new Error(message)), timeout);
  });
}

// 将AudioWorklet处理逻辑转为字符串嵌入主文件
const workletCode = `
class PCMProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this.port.onmessage = (event) => {
      if (event.data === 'stop') {
        this.port.postMessage('prepare to stop');
        this.isStopped = true;
        if (this.buffer.length > 0) {
          this.port.postMessage(new Int16Array(this.buffer));
          this.port.postMessage({'event':'stopped'});
          this.buffer = [];
        }
      }
    };
    this.buffer = [];
    this.sourceBuffer = [];
    this.sourcePosition = 0;
    this.sampleRatio = sampleRate / 16000;
    this.targetSampleCount = 1024;
  }

  process(inputs) {
    if (this.isStopped) return false;
    const input = inputs[0];
    if (input.length > 0) {
      const inputData = input[0];
      // Microphones commonly use 44.1/48 kHz. Keep VAD and ASR at 16 kHz
      // without requiring the device to support a 16 kHz AudioContext.
      this.sourceBuffer.push(...inputData);
      while (this.sourcePosition + 1 < this.sourceBuffer.length) {
        const index = Math.floor(this.sourcePosition);
        const fraction = this.sourcePosition - index;
        const sample = this.sourceBuffer[index] * (1 - fraction) + this.sourceBuffer[index + 1] * fraction;
        this.buffer.push(Math.max(-32768, Math.min(32767, Math.round(sample * 32767))));
        this.sourcePosition += this.sampleRatio;
      }
      const consumed = Math.min(Math.floor(this.sourcePosition), this.sourceBuffer.length - 1);
      this.sourceBuffer.splice(0, consumed);
      this.sourcePosition -= consumed;

      while (this.buffer.length >= this.targetSampleCount) {
        const pcmData = this.buffer.splice(0, this.targetSampleCount);
        this.port.postMessage(new Int16Array(pcmData));
        this.port.postMessage({'event':'sending'});
      }
    }
    return true;
  }
}

registerProcessor('pcm-processor', PCMProcessor);
`;
class PCMAudioRecorder {
  constructor() {
    this.audioContext = null;
    this.stream = null;
    this.currentSource = null;
    this.audioCallback = null;
    this.connecting = null;
    this.generation = 0;
    this.workletContext = null;
    this.resumeReady = null;
  }

  // Call synchronously from the microphone click, before network/permission awaits.
  prepareContext() {
    if (!this.audioContext || this.audioContext.state === 'closed') {
      const Context = window.AudioContext || window.webkitAudioContext;
      if (!Context) throw new DOMException('当前浏览器不支持语音录制', 'NotSupportedError');
      this.audioContext = new Context();
    }
    const context = this.audioContext;
    if (context.state === 'running') return Promise.resolve();
    let timer;
    this.resumeReady = Promise.race([
      context.resume(),
      new Promise((_, reject) => {
        timer = setTimeout(() => reject(new DOMException('AudioContext did not resume', 'AudioActivationError')), 4000);
      }),
    ]).finally(() => clearTimeout(timer));
    this.resumeReady.catch(() => {});
    return this.resumeReady;
  }

  async connect(audioCallback, options = {}) {
    if (this.connecting) return this.connecting;
    if (this.processorNode) return;
    const generation = ++this.generation;
    this.connecting = this.open(audioCallback, generation, options);
    try { await this.connecting; }
    finally { this.connecting = null; }
  }

  async open(audioCallback, generation, {signal} = {}) {
    this.audioCallback = audioCallback;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      throw new DOMException('Microphone access requires a secure browser context', 'SecurityError');
    }
    const ready = this.prepareContext();
    const context = this.audioContext;
    if (!context.audioWorklet) throw new DOMException('AudioWorklet unavailable', 'NotSupportedError');
    // Request permission in the start click, before any avatar or worklet await.
    const media = navigator.mediaDevices.getUserMedia({audio: {echoCancellation: true, noiseSuppression: true, autoGainControl: true}, video: false});
    media.then(stream => {
      if (signal?.aborted || generation !== this.generation || context.state === 'closed') stream.getTracks().forEach(track => track.stop());
    }).catch(() => {});
    console.log('Current sample rate:', this.audioContext.sampleRate, 'Hz');

    const worklet = (async () => {
      if (this.workletContext !== context) {
        const blob = new Blob([workletCode], { type: 'application/javascript' });
        const url = URL.createObjectURL(blob);
        try {
          await waitWithDeadline(context.audioWorklet.addModule(url), {signal, timeout: 8000, message: '麦克风初始化超时，请重新连接。'});
          this.workletContext = context;
        } finally { URL.revokeObjectURL(url); }
      }
    })();
    worklet.catch(() => {});
    const stream = await waitWithDeadline(media, {signal, timeout: 20000, message: '未获得麦克风权限，请允许访问后重新连接。'});
    if (signal?.aborted || generation !== this.generation || context.state === 'closed') {
      stream.getTracks().forEach(track => track.stop());
      throw new DOMException('Recording cancelled', 'AbortError');
    }
    this.stream = stream;
    await Promise.all([worklet, waitWithDeadline(ready, {signal, timeout: 5000, message: '手机未能启动麦克风，请重新点击开始。'})]);
    if (signal?.aborted || generation !== this.generation || context.state === 'closed') {
      stream.getTracks().forEach(track => track.stop());
      throw new DOMException('Recording cancelled', 'AbortError');
    }
    if (context.state !== 'running') throw new DOMException('Recording context is not running', 'AudioActivationError');
    this.currentSource = context.createMediaStreamSource(stream);

    this.processorNode = new AudioWorkletNode(context, 'pcm-processor');

    this.processorNode.port.onmessage = (event) => {
      if (event.data instanceof Int16Array) {
        if (generation === this.generation) {
          Promise.resolve().then(() => this.audioCallback?.(event.data)).catch(error => this.onError?.(error));
        }
      } else if (event.data?.event === 'stopped') {
        console.log('Recorder stopped.');
      }
    };

    this.currentSource.connect(this.processorNode);
    this.processorNode.connect(this.audioContext.destination);
  }

  async stop({keepContext = false} = {}) {
    this.generation += 1;
    this.audioCallback = null;
    if (this.processorNode) {
      this.processorNode.port.postMessage('stop');
      this.processorNode.disconnect();
      this.processorNode = null;
    }

    this.stream?.getTracks().forEach(track => track.stop());
    this.stream = null;
    this.currentSource?.disconnect();
    this.currentSource = null;

    if (this.audioContext && !keepContext) {
      const context = this.audioContext;
      this.audioContext = null;
      if (context.state !== 'closed') {
        await waitWithDeadline(context.close(), {timeout: 1500, message: '麦克风停止超时'}).catch(() => {});
      }
      this.workletContext = null;
      this.resumeReady = null;
    }
  }
}

// 暴露到全局环境
window.PCMAudioRecorder = PCMAudioRecorder;
