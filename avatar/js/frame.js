window.avatarReady = (async () => {
  if (!gl) throw new Error('当前设备无法启用数字人画面，请使用新版浏览器。');
  window.reportAvatarStatus('loading', '正在下载专家形象。');
  await Promise.all([
    qtLoad({qt: {entryFunction: window.createQtAppInstance, containerElements: [document.getElementById('screen')]}}),
    videoProcessor.init(CONFIG.videoSrc, CONFIG.dataSrc),
  ]);
  window.reportAvatarStatus('loading', '正在准备专家画面。');
  await newVideoTask(true);
  document.getElementById('loadingSpinner').hidden = true;
  window.reportAvatarStatus('ready');
})();
window.avatarReady.catch(error => { window.reportAvatarStatus('error', error.message); });

window.prepareAvatarAudio = bytes => {
  window.stopAvatarAudio(false);
  const ptr = Module._malloc(bytes.byteLength);
  try {
    Module.HEAPU8.set(bytes, ptr);
    Module._setAudioBuffer(ptr, bytes.byteLength);
  } finally { Module._free(ptr); }
};
window.setAvatarPaused = paused => {
  isPaused = paused;
  if (paused) {
    window.stopAvatarAudio(false);
    cancelAnimationFrame(renderLoopId);
    videoProcessor.pause();
    document.getElementById('background_video').pause();
  } else {
    videoProcessor.play();
    if (CONFIG.backgroundVideoSrc) document.getElementById('background_video').play().catch(() => {});
    processVideoFrames();
  }
};
