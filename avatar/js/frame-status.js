const avatarLoaderId = new URLSearchParams(location.search).get('loaderId');
window.avatarStatus = {status: 'loading', message: '正在下载专家形象。'};
window.reportAvatarStatus = (status, message) => {
  window.avatarStatus = {status, message};
  parent.postMessage({type: 'dhlive-avatar', loaderId: avatarLoaderId, status, message}, location.origin);
  const spinner = document.getElementById('loadingSpinner');
  if (spinner && message) spinner.textContent = message;
};
window.addEventListener('error', () => {
  if (!['ready', 'error'].includes(window.avatarStatus.status)) window.reportAvatarStatus('error', '专家画面初始化失败，请重新连接。');
});
window.reportAvatarStatus('loading', '正在下载专家形象。');
let avatarSupported = false;
try { avatarSupported = typeof WebAssembly === 'object' && !!document.getElementById('canvas_gl').getContext('webgl2', {antialias: false}); } catch {}
if (!avatarSupported) {
  window.reportAvatarStatus('error', '当前浏览器无法打开专家画面，请使用新版浏览器或选择实时通话。');
}
