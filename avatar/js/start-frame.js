window.avatarReady = (async () => {
  if (window.avatarStatus.status === 'error') throw new Error(window.avatarStatus.message);
  const load = src => new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = resolve;
    script.onerror = () => reject(new Error('专家画面组件下载失败，请重新连接。'));
    document.body.appendChild(script);
  });
  await load('js/MiniLive2.js?v=20261115-video2');
  await load('js/frame.js?v=20261115-video2');
  await window.avatarReady;
})();
window.avatarReady.catch(error => {
  if (window.avatarStatus.status !== 'error') window.reportAvatarStatus('error', error.message);
});
