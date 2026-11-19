'use strict';

// Use the browser's media output path and its actual playback clock on phones.
class SpeechPlayer {
  constructor(element) {
    this.element = element;
    this.current = null;
    this.revision = 0;
    this.timer = null;
    this.listeners = [];
    this.silentUrl = null;
    this.diagnostics = [];
    const player = this;
    this.clock = {
      get currentTime() { return player.element.currentTime; },
      get state() { return !player.current || player.element.ended ? 'closed' : player.element.paused ? 'suspended' : 'running'; },
    };
  }

  record(event) {
    this.diagnostics.push({event, time: performance.now(), position: this.element.currentTime,
      paused: this.element.paused, readyState: this.element.readyState});
    if (this.diagnostics.length > 30) this.diagnostics.shift();
  }

  unlock() {
    if (this.current) return;
    if (!this.silentUrl) {
      const bytes = new Uint8Array(44 + 1600);
      const data = new DataView(bytes.buffer);
      const write = (offset, text) => [...text].forEach((char, i) => { bytes[offset + i] = char.charCodeAt(0); });
      write(0, 'RIFF'); data.setUint32(4, bytes.length - 8, true); write(8, 'WAVE'); write(12, 'fmt ');
      data.setUint32(16, 16, true); data.setUint16(20, 1, true); data.setUint16(22, 1, true);
      data.setUint32(24, 16000, true); data.setUint32(28, 32000, true); data.setUint16(32, 2, true);
      data.setUint16(34, 16, true); write(36, 'data'); data.setUint32(40, bytes.length - 44, true);
      this.silentUrl = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
    }
    const url = this.silentUrl;
    this.element.muted = false;
    this.element.volume = 1;
    this.element.src = url;
    // Called synchronously in the send/start click; a denied prime never blocks a question.
    try {
      Promise.resolve(this.element.play()).then(() => {
        if (!this.current && this.element.src === url) this.element.pause();
      }).catch(() => {});
    } catch {}
  }

  play(bytes, seconds, callbacks) {
    this.stop();
    const revision = this.revision;
    const url = URL.createObjectURL(new Blob([bytes], {type: 'audio/wav'}));
    const item = {url, seconds, callbacks, started: false, blocked: false};
    this.current = item;
    this.element.muted = false;
    this.element.volume = 1;
    this.element.src = url;
    const valid = () => this.revision === revision && this.current === item;
    const listen = (name, handler) => {
      this.element.addEventListener(name, handler);
      this.listeners.push([name, handler]);
    };
    listen('timeupdate', () => { if (valid()) this.checkProgress(); });
    listen('playing', () => { if (valid()) this.checkProgress(); });
    listen('error', () => { if (valid()) this.block('media-error'); });
    listen('ended', () => { if (valid()) this.finish(); });
    this.retry(false);
  }

  retry(restart = false) {
    const item = this.current;
    if (!item) { this.unlock(); return; }
    clearInterval(this.timer);
    const attemptId = (item.attemptId || 0) + 1;
    item.attemptId = attemptId;
    if (this.element.error) this.element.load();
    if (restart) {
      try { this.element.currentTime = 0; } catch {}
    }
    item.started = false;
    item.blocked = false;
    item.lastPosition = this.element.currentTime;
    item.startPosition = item.lastPosition;
    item.lastProgress = performance.now();
    this.record(restart ? 'replay-requested' : 'play-requested');
    this.timer = setInterval(() => this.checkProgress(), 250);
    try {
      const attempt = this.element.play();
      Promise.resolve(attempt).catch(error => {
        if (this.current === item && item.attemptId === attemptId && !item.blocked) this.block(error.name || 'play-rejected');
      });
    } catch (error) { this.block(error.name || 'play-rejected'); }
  }

  checkProgress() {
    const item = this.current;
    if (!item || item.blocked || document.hidden) return;
    if (this.element.ended) { this.finish(); return; }
    const position = this.element.currentTime;
    if (!this.element.paused && position > item.lastPosition + 0.005) {
      item.lastPosition = position;
      item.lastProgress = performance.now();
      if (!item.started) {
        item.started = true;
        this.record('playing');
        const duration = Number.isFinite(this.element.duration) && this.element.duration > 0 ? this.element.duration : item.seconds;
        item.callbacks.onStarted(this.clock, duration);
      }
    } else if (performance.now() - item.lastProgress > 3000 && !this.element.ended) {
      this.block('playback-stalled');
    }
  }

  finish() {
    const item = this.current;
    if (!item || !this.element.ended) return;
    if (!item.started && this.element.currentTime <= item.startPosition + 0.005) {
      this.block('no-playback-progress');
      return;
    }
    this.record('ended');
    this.stop();
    item.callbacks.onEnded();
  }

  block(reason) {
    const item = this.current;
    if (!item || item.blocked) return;
    item.blocked = true;
    clearInterval(this.timer);
    this.timer = null;
    this.element.pause();
    this.record(reason);
    window.reportClientStatus?.({state: 'blocked', component: 'speech', code: reason,
      mediaState: this.element.readyState, mediaTime: this.element.currentTime, nativeError: this.element.error?.code || 0});
    item.callbacks.onBlocked(reason);
  }

  stop() {
    this.revision++;
    clearInterval(this.timer);
    this.timer = null;
    for (const [name, handler] of this.listeners) this.element.removeEventListener(name, handler);
    this.listeners = [];
    this.element.pause();
    this.element.removeAttribute('src');
    this.element.load();
    if (this.current) URL.revokeObjectURL(this.current.url);
    this.current = null;
  }
}
