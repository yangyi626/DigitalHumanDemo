(function () {
  var client = Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 10);
  var permission = 'unknown';
  try {
    if (navigator.permissions && navigator.permissions.query) {
      navigator.permissions.query({name: 'microphone'}).then(function (result) {
        permission = result.state;
      }).catch(function () {});
    }
  } catch (_) {}
  window.reportClientStatus = function (details) {
    try {
      var data = {
        client: client,
        build: document.documentElement.getAttribute('data-release') || 'development',
        secure: window.isSecureContext === true,
        top: window.top === window,
        mic: !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia),
        worklet: typeof window.AudioWorkletNode === 'function',
        wasm: typeof window.WebAssembly === 'object',
        permission: permission
      };
      var keys = ['mode', 'state', 'component', 'code', 'frames', 'peak', 'mediaState', 'mediaTime', 'nativeError'];
      keys.forEach(function (key) { if (details[key] !== undefined) data[key] = details[key]; });
      var request = new XMLHttpRequest();
      request.open('POST', 'api/diagnostics', true);
      request.setRequestHeader('Content-Type', 'application/json');
      request.send(JSON.stringify(data));
    } catch (_) {}
  };
  window.addEventListener('error', function (event) {
    window.reportClientStatus({state: 'error', component: 'page', code: event.error && event.error.name || 'ScriptError'});
    if (event.error && event.error.name === 'SyntaxError') {
      var status = document.getElementById('startup-status');
      if (status) status.textContent = '当前浏览器无法加载页面，请使用新版浏览器。';
    }
  });
  window.addEventListener('DOMContentLoaded', function () {
    window.reportClientStatus({state: 'boot', component: 'page', code: 'loaded'});
  });
})();
