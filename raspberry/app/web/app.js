const connection = document.querySelector('#connection');
const mode = document.querySelector('#mode');
const lastMessage = document.querySelector('#last-message');
const telemetry = document.querySelector('#telemetry');
const heroMode = document.querySelector('#hero-mode');
const heroMessage = document.querySelector('#hero-message');
const messages = document.querySelector('#messages');
const form = document.querySelector('#chat-form');
const input = document.querySelector('#chat-input');
const terminalForm = document.querySelector('#terminal-form');
const terminalInput = document.querySelector('#terminal-input');
const terminalOutput = document.querySelector('#terminal-output');
const servoStick = document.querySelector('#servo-stick');
const servoKnob = document.querySelector('#servo-knob');
const faceCanvas = document.querySelector('#face-canvas');
const voiceButton = document.querySelector('#voice-button');
const voiceTranscript = document.querySelector('#voice-transcript');
const voiceStatus = document.querySelector('#voice-status');
const voiceResponse = document.querySelector('#voice-response');
const settingsForm = document.querySelector('#settings-form');
const settingsFeedback = document.querySelector('#settings-feedback');
const monitorMode = new URLSearchParams(location.search).has('monitor');
if (monitorMode) document.body.classList.add('monitor-mode');

function drawRobotFace(now) {
  const context = faceCanvas.getContext('2d');
  const elapsed = (now / 1000) % 24;
  const centerX = 120;
  const eyeY = 125;
  let offsetX = 0;
  let blink = false;
  let sleeping = false;
  let yawning = false;
  let smiling = false;
  let laughing = false;
  if (elapsed >= 2.2 && elapsed < 4.6) offsetX = 8;
  else if (elapsed >= 5.6 && elapsed < 8) offsetX = -8;
  else if (elapsed >= 4.6 && elapsed < 5.2) blink = true;
  else if (elapsed >= 8.6 && elapsed < 10.1) yawning = true;
  else if (elapsed >= 10.1 && elapsed < 12) smiling = true;
  else if (elapsed >= 12 && elapsed < 21) sleeping = true;
  else if (elapsed >= 21) laughing = true;
  context.fillStyle = '#000';
  context.fillRect(0, 0, 240, 280);
  context.fillStyle = '#00d9ff';
  if (sleeping) {
    context.fillRect(56, eyeY - 4, 32, 8);
    context.fillRect(152, eyeY - 4, 32, 8);
    context.font = '28px sans-serif';
    context.fillText('Z', 62 + ((now / 180) % 18), 55);
    context.fillText('Z', 86 + ((now / 180 + 6) % 18), 35);
  } else if (blink) {
    context.fillRect(56, eyeY - 2, 16, 4);
    context.fillRect(168, eyeY - 2, 16, 4);
  } else {
    context.beginPath(); context.arc(64 + offsetX, eyeY, 8, 0, Math.PI * 2); context.fill();
    context.beginPath(); context.arc(176 + offsetX, eyeY, 8, 0, Math.PI * 2); context.fill();
  }
  const mouthY = eyeY + 36;
  if (yawning) {
    context.beginPath(); context.ellipse(centerX, mouthY + 3, 13, 16, 0, 0, Math.PI * 2); context.fill();
  } else if (smiling || laughing) {
    context.beginPath(); context.arc(centerX, mouthY - 2, 24, 0.15, Math.PI - 0.15); context.lineWidth = laughing ? 7 : 4; context.strokeStyle = '#00d9ff'; context.stroke();
    if (laughing) { context.fillRect(92, mouthY + 6, 56, 5); }
  } else {
    const mouthHeight = elapsed < 2.2 ? 4 + Math.abs(Math.sin(now / 130)) * 4 : 4;
    context.fillRect(88, mouthY, 64, mouthHeight);
  }
  requestAnimationFrame(drawRobotFace);
}
requestAnimationFrame(drawRobotFace);

function addMessage(text, kind) {
  const item = document.createElement('p');
  item.className = `message ${kind}`;
  item.textContent = text;
  messages.append(item);
  messages.scrollTop = messages.scrollHeight;
}

function setMeter(id, value) {
  const meter = document.querySelector(id);
  if (meter) meter.style.width = `${Math.max(0, Math.min(100, value || 0))}%`;
}

function renderMetrics(metrics) {
  if (!metrics) return;
  const cpu = `${metrics.cpu_load.toFixed(1)}%`;
  const memory = `${metrics.memory_used.toFixed(1)}%`;
  const temperature = metrics.temperature === null ? '--' : `${metrics.temperature.toFixed(1)}°C`;
  ['#quick-cpu', '#perf-cpu'].forEach((id) => { const node = document.querySelector(id); if (node) node.textContent = cpu; });
  ['#quick-memory', '#perf-memory'].forEach((id) => { const node = document.querySelector(id); if (node) node.textContent = memory; });
  ['#quick-temp', '#perf-temp'].forEach((id) => { const node = document.querySelector(id); if (node) node.textContent = temperature; });
  setMeter('#meter-cpu', metrics.cpu_load);
  setMeter('#meter-memory', metrics.memory_used);
  setMeter('#meter-temp', metrics.temperature === null ? 0 : Math.min(100, metrics.temperature / 90 * 100));
}

function render(state) {
  mode.textContent = state.mode;
  heroMode.textContent = state.mode;
  lastMessage.textContent = state.last_message;
  heroMessage.textContent = state.chat_busy ? 'Estou pensando...' : state.last_message;
  telemetry.textContent = `Gesto: ${state.gesture} · Dedos: ${state.finger_count}`;
  if (window.zetaMicListening !== state.mic_listening) {
    window.zetaMicListening = state.mic_listening;
    window.dispatchEvent(new CustomEvent('zeta-mic', {detail: {listening: state.mic_listening}}));
  }
  if (inputFeedback && state.input_ready === false) {
    inputFeedback.textContent = 'Mouse e teclado indisponiveis: verifique /dev/uinput no Raspberry.';
    inputFeedback.classList.add('error');
  }
  if (state.chat_messages) {
    messages.replaceChildren(...state.chat_messages.map((message) => {
      const item = document.createElement('p');
      item.className = `message ${message.kind}`;
      item.textContent = message.text;
      return item;
    }));
    messages.scrollTop = messages.scrollHeight;
    const lastResponse = [...state.chat_messages].reverse().find((message) => message.kind === 'incoming');
    if (lastResponse && voiceResponse) voiceResponse.textContent = lastResponse.text;
  }
  renderMetrics(state.metrics);
}

function renderSettings(settings) {
  if (!settingsForm) return;
  document.querySelector('#setting-tts').checked = settings.tts_enabled;
  document.querySelector('#setting-ai').value = settings.ai_provider;
  document.querySelector('#setting-transcription').value = settings.transcription_provider;
  document.querySelector('#setting-navigation').value = settings.menu_navigation;
  document.querySelector('#setting-dwell').value = settings.menu_dwell_time_s;
}

async function post(path, body) {
  const response = await fetch(path, { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(body) });
  return response.json();
}

document.querySelectorAll('[data-view-target]').forEach((button) => {
  button.addEventListener('click', () => {
    const target = button.dataset.viewTarget;
    document.querySelectorAll('.view').forEach((view) => view.classList.toggle('active', view.dataset.view === target));
    document.querySelectorAll('.nav-tab').forEach((tab) => tab.classList.toggle('active', tab === button));
    window.scrollTo({top: 0, behavior: 'smooth'});
  });
});

document.querySelectorAll('[data-fullscreen]').forEach((button) => {
  button.addEventListener('click', async () => {
    const target = document.querySelector(`#${button.dataset.fullscreen}`);
    if (document.fullscreenElement) await document.exitFullscreen();
    else await target.requestFullscreen();
  });
});

document.querySelectorAll('[data-mode]').forEach((button) => button.addEventListener('click', () => post('/api/mode', {mode: button.dataset.mode})));
document.querySelector('#stop').addEventListener('click', () => post('/api/stop', {}));

async function sendChat(text) {
  addMessage(text, 'outgoing');
  await post('/api/chat', {text});
}

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  await sendChat(text);
});

if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
  voiceButton.disabled = true;
  voiceStatus.textContent = 'gravacao de voz nao suportada neste navegador';
} else {
  let recorder = null;
  let microphoneStream = null;
  let audioChunks = [];
  let microphoneReady = false;

  async function authorizeMicrophone() {
    const stream = await navigator.mediaDevices.getUserMedia({audio: true});
    stream.getTracks().forEach((track) => track.stop());
    microphoneReady = true;
    voiceStatus.textContent = 'microfone autorizado; faca o gesto shaka';
  }

  async function createRecorder() {
    microphoneStream = await navigator.mediaDevices.getUserMedia({audio: {
      channelCount: 1,
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    }});
    const mimeType = ['audio/webm;codecs=opus', 'audio/ogg;codecs=opus', 'audio/webm']
      .find((type) => MediaRecorder.isTypeSupported(type));
    recorder = new MediaRecorder(microphoneStream, {
      ...(mimeType ? {mimeType} : {}),
      audioBitsPerSecond: 128000,
    });
    recorder.addEventListener('dataavailable', (event) => {
      if (event.data.size) audioChunks.push(event.data);
    });
    recorder.addEventListener('start', () => {
      voiceButton.classList.add('listening');
      voiceButton.setAttribute('aria-pressed', 'true');
      voiceStatus.textContent = 'ouvindo pelo gesto shaka...';
      voiceTranscript.textContent = 'Fale agora...';
    });
    recorder.addEventListener('stop', async () => {
      voiceButton.classList.remove('listening');
      voiceButton.setAttribute('aria-pressed', 'false');
      voiceStatus.textContent = 'enviando ao Groq...';
      microphoneStream?.getTracks().forEach((track) => track.stop());
      const blob = new Blob(audioChunks, {type: recorder.mimeType || 'audio/webm'});
      recorder = null;
      microphoneReady = false;
      audioChunks = [];
      if (!blob.size) {
        voiceStatus.textContent = 'nenhum audio capturado';
        return;
      }
      const body = new FormData();
      body.append('audio', blob, 'voice.webm');
      const controller = new AbortController();
      const timeout = window.setTimeout(() => controller.abort(), 180000);
      try {
        const response = await fetch('/api/transcribe', {method: 'POST', body, signal: controller.signal});
        const result = await response.json();
        if (!response.ok || result.accepted === false) throw new Error(result.error || 'falha na transcricao');
        voiceTranscript.textContent = result.text;
        voiceStatus.textContent = 'transcricao enviada ao Groq';
      } catch (error) {
        voiceStatus.textContent = error.name === 'AbortError'
          ? 'a transcricao demorou mais de 3 minutos'
          : (error.message || 'falha na transcricao');
      } finally {
        window.clearTimeout(timeout);
      }
    });
    microphoneReady = true;
  }

  async function startRecording() {
    if (!microphoneReady) await createRecorder();
    if (recorder?.state !== 'recording') {
      audioChunks = [];
      recorder.start();
    }
  }

  voiceButton.addEventListener('click', async () => {
    if (!microphoneReady) {
      try {
        await authorizeMicrophone();
        if (window.zetaMicListening) await startRecording();
      } catch (error) {
        voiceStatus.textContent = error.name === 'NotAllowedError'
          ? 'permissao do microfone negada neste navegador'
          : 'nao foi possivel acessar o microfone';
      }
      return;
    }
    if (recorder?.state === 'recording') {
      recorder.stop();
      return;
    }
    try {
      await startRecording();
    } catch (error) {
      voiceStatus.textContent = error.name === 'NotAllowedError'
        ? 'clique no microfone para autorizar a captura'
        : 'nao foi possivel acessar o microfone';
    }
  });
  voiceStatus.textContent = 'clique no microfone uma vez para autorizar o shaka';
  window.addEventListener('zeta-mic', (event) => {
    if (event.detail.listening && recorder?.state !== 'recording') {
      startRecording().catch((error) => {
        voiceStatus.textContent = error.name === 'NotAllowedError'
          ? 'clique no microfone para autorizar a captura'
          : (error.message || 'nao foi possivel iniciar o microfone');
      });
    }
    if (!event.detail.listening && recorder?.state === 'recording') recorder.stop();
  });
}
terminalForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const command = terminalInput.value.trim();
  if (!command) return;
  terminalOutput.textContent = `> ${command}\n\nExecutando...`;
  terminalInput.value = '';
  const result = await post('/api/terminal', {command});
  terminalOutput.textContent = `> ${command}\n\n${result.output}`;
});

async function servo(x, y) { await post('/api/servo', {x, y}); }

let servoPointer = null;
function updateServo(event) {
  const bounds = servoStick.getBoundingClientRect();
  const radius = bounds.width / 2;
  const x = Math.max(-1, Math.min(1, (event.clientX - (bounds.left + radius)) / (radius * 0.72)));
  const y = Math.max(-1, Math.min(1, (event.clientY - (bounds.top + radius)) / (radius * 0.72)));
  servoKnob.style.left = `${50 + x * 36}%`;
  servoKnob.style.top = `${50 + y * 36}%`;
  servo(x, y);
}
servoStick.addEventListener('pointerdown', (event) => { event.preventDefault(); servoPointer = event.pointerId; servoStick.setPointerCapture(event.pointerId); updateServo(event); });
servoStick.addEventListener('pointermove', (event) => { if (event.pointerId === servoPointer) updateServo(event); });
servoStick.addEventListener('pointerup', () => { servoPointer = null; });
function centerServo() {
  servoPointer = null;
  servoKnob.style.left = '50%';
  servoKnob.style.top = '50%';
  servo(0, 0);
}
servoStick.addEventListener('pointerup', centerServo);
servoStick.addEventListener('pointercancel', centerServo);

document.querySelectorAll('[data-mouse]').forEach((button) => {
  button.addEventListener('pointerdown', (event) => { event.preventDefault(); button.setPointerCapture(event.pointerId); });
  button.addEventListener('pointerup', (event) => { event.preventDefault(); mouse(button.dataset.mouse); });
});

const mousePad = document.querySelector('#mouse-pad');
const dragToggle = document.querySelector('#drag-toggle');
const notebook = document.querySelector('.notebook');
const desktopStage = document.querySelector('#desktop-stage');
const inputFeedback = document.querySelector('#input-feedback');
function showInputResult(result) {
  if (!inputFeedback || !result || result.accepted !== false) return;
  inputFeedback.textContent = result.error || 'Entrada virtual indisponivel.';
  inputFeedback.classList.add('error');
}
async function mouse(action, x, y) { showInputResult(await post('/api/mouse', {action, x, y})); }
async function key(keyName, pressed = false) { showInputResult(await post('/api/key', {key: keyName, pressed})); }
const notebookAnchor = document.createComment('controles do notebook');
notebook.parentNode.insertBefore(notebookAnchor, notebook);
document.addEventListener('fullscreenchange', () => {
  if (document.fullscreenElement === desktopStage) desktopStage.append(notebook);
  else if (notebook.parentNode !== notebookAnchor.parentNode) notebookAnchor.parentNode.insertBefore(notebook, notebookAnchor.nextSibling);
});
let lastPointer = null;
let pendingMouseMove = null;
let mouseMoveFrame = null;
let leftButtonDown = false;
function releaseLeftButton() {
  if (!leftButtonDown) return;
  leftButtonDown = false;
  mouse('left_up');
  dragToggle.textContent = '⇱ segurar';
  dragToggle.classList.remove('pressed');
}
dragToggle.addEventListener('pointerup', (event) => {
  event.preventDefault();
  leftButtonDown = !leftButtonDown;
  mouse(leftButtonDown ? 'left_down' : 'left_up');
  dragToggle.textContent = leftButtonDown ? '↙ soltar' : '⇱ segurar';
  dragToggle.classList.toggle('pressed', leftButtonDown);
});
dragToggle.addEventListener('pointercancel', releaseLeftButton);
window.addEventListener('blur', releaseLeftButton);
document.addEventListener('visibilitychange', () => { if (document.hidden) releaseLeftButton(); });
mousePad.addEventListener('pointerdown', (event) => { event.preventDefault(); lastPointer = {x: event.clientX, y: event.clientY}; mousePad.setPointerCapture(event.pointerId); });
mousePad.addEventListener('pointermove', (event) => {
  if (!lastPointer) return;
  const dx = event.clientX - lastPointer.x;
  const dy = event.clientY - lastPointer.y;
  lastPointer = {x: event.clientX, y: event.clientY};
  if (!dx && !dy) return;
  pendingMouseMove = {
    x: (pendingMouseMove?.x || 0) + dx,
    y: (pendingMouseMove?.y || 0) + dy,
  };
  if (mouseMoveFrame !== null) return;
  mouseMoveFrame = requestAnimationFrame(() => {
    const movement = pendingMouseMove;
    pendingMouseMove = null;
    mouseMoveFrame = null;
    if (movement && (movement.x || movement.y)) mouse('move_relative', movement.x, movement.y);
  });
});
function stopMousePointer(event) {
  event.preventDefault();
  lastPointer = null;
  pendingMouseMove = null;
  if (mouseMoveFrame !== null) cancelAnimationFrame(mouseMoveFrame);
  mouseMoveFrame = null;
}
mousePad.addEventListener('pointerup', stopMousePointer);
mousePad.addEventListener('pointercancel', stopMousePointer);

document.querySelectorAll('[data-key]').forEach((button) => {
  const keyName = button.dataset.key;
  button.addEventListener('pointerdown', (event) => { event.preventDefault(); button.setPointerCapture(event.pointerId); button.classList.add('pressed'); key(keyName, true); });
  const release = (event) => { event.preventDefault(); button.classList.remove('pressed'); key(keyName, false); };
  button.addEventListener('pointerup', release);
  button.addEventListener('pointercancel', release);
});

function connect() {
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const socket = new WebSocket(`${protocol}://${location.host}/ws`);
  socket.addEventListener('open', () => { connection.textContent = 'online'; connection.className = 'connection online'; });
  socket.addEventListener('message', (event) => render(JSON.parse(event.data)));
  socket.addEventListener('close', () => { connection.textContent = 'offline'; connection.className = 'connection'; setTimeout(connect, 1500); });
}

fetch('/api/status').then((response) => response.json()).then(render);
connect();
fetch('/api/settings').then((response) => response.json()).then(renderSettings);
settingsForm?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const response = await fetch('/api/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({
      tts_enabled: document.querySelector('#setting-tts').checked,
      ai_provider: document.querySelector('#setting-ai').value,
      transcription_provider: document.querySelector('#setting-transcription').value,
      menu_navigation: document.querySelector('#setting-navigation').value,
      menu_dwell_time_s: Number(document.querySelector('#setting-dwell').value),
    }),
  });
  const result = await response.json();
  settingsFeedback.textContent = result.accepted
    ? 'Preferências salvas. Reinicie o Zeta para aplicar.'
    : (result.error || 'Não foi possível salvar.');
});
