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
  if (state.chat_messages) {
    messages.replaceChildren(...state.chat_messages.map((message) => {
      const item = document.createElement('p');
      item.className = `message ${message.kind}`;
      item.textContent = message.text;
      return item;
    }));
    messages.scrollTop = messages.scrollHeight;
  }
  renderMetrics(state.metrics);
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

form.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage(text, 'outgoing');
  input.value = '';
  await post('/api/chat', {text});
});

terminalForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  const command = terminalInput.value.trim();
  if (!command) return;
  terminalOutput.textContent = `> ${command}\n\nExecutando...`;
  terminalInput.value = '';
  const result = await post('/api/terminal', {command});
  terminalOutput.textContent = `> ${command}\n\n${result.output}`;
});

async function mouse(action, x, y) { await post('/api/mouse', {action, x, y}); }
async function key(keyName, pressed = false) { await post('/api/key', {key: keyName, pressed}); }
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
const notebookAnchor = document.createComment('controles do notebook');
notebook.parentNode.insertBefore(notebookAnchor, notebook);
document.addEventListener('fullscreenchange', () => {
  if (document.fullscreenElement === desktopStage) desktopStage.append(notebook);
  else if (notebook.parentNode !== notebookAnchor.parentNode) notebookAnchor.parentNode.insertBefore(notebook, notebookAnchor.nextSibling);
});
let lastPointer = null;
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
  if (Math.abs(dx) + Math.abs(dy) < 4) return;
  lastPointer = {x: event.clientX, y: event.clientY};
  mouse('move_relative', dx, dy);
});
mousePad.addEventListener('pointerup', (event) => { event.preventDefault(); lastPointer = null; });
mousePad.addEventListener('pointercancel', () => { lastPointer = null; });

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
