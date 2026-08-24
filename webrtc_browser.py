import base64
import json
import os
import secrets
import time
from collections import defaultdict
from flask import Flask, render_template_string, request, Response
from flask_socketio import SocketIO, emit, join_room

app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading")

APP_VERSION = "1.1.1"
UPDATE_REPO = "maco30001/Mirror-Mirror"

sessions = {}
PAIRING_TTL = 300
DISCONNECT_GRACE = 45
VIEWER_RECONNECT_GRACE = 6 * 60 * 60

MAX_ATTEMPTS_PER_WINDOW = 10
ATTEMPT_WINDOW_SECONDS = 60
_failed_attempts = defaultdict(list)

_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'MirrorMirror')
os.makedirs(_data_dir, exist_ok=True)
TRUSTED_DEVICES_FILE = os.path.join(_data_dir, 'trusted_devices.json')


def _load_trusted_devices():
    try:
        with open(TRUSTED_DEVICES_FILE, 'r') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_trusted_devices():
    try:
        with open(TRUSTED_DEVICES_FILE, 'w') as f:
            json.dump(trusted_devices, f)
    except Exception as e:
        print(f"[warn] couldn't save trusted devices: {e}")


trusted_devices = _load_trusted_devices()


def _rate_limited(ip):
    now = time.time()
    recent = [t for t in _failed_attempts[ip] if now - t < ATTEMPT_WINDOW_SECONDS]
    _failed_attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS_PER_WINDOW


def _record_failed_attempt(ip):
    _failed_attempts[ip].append(time.time())

try:
    import pyautogui
    pyautogui.FAILSAFE = False
    HAVE_PYAUTOGUI = True
except Exception:
    HAVE_PYAUTOGUI = False



def _new_code():
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in sessions:
            return code


def _session_for(code):
    s = sessions.get(code)
    if not s:
        return None
    if not s['claimed'] and time.time() - s['created_at'] > PAIRING_TTL:
        del sessions[code]
        return None
    return s


def _authorized(code, token, sid, role):
    s = sessions.get(code)
    if not s or s.get('token') != token:
        return False
    if role == 'host':
        return s.get('host_sid') == sid
    return s.get('viewer_sid') == sid


ICON_B64 = "iVBORw0KGgoAAAANSUhEUgAAAMAAAADACAIAAADdvvtQAAAClUlEQVR4nO3d0U3jQBRA0c2KBlwDRVIDRVIDLfCBxMdqv7jzJkM4pwAb5d2M7cFSbtd1/YHv+nvvP4CfTUAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiI5GnDOd5f3jachf+6Xp9Hj38b/clL6RxiLqPBS5h6zjE3i6mA1HOaoYmMBKSeM03MZX1A6jnZ8ul4jCdZHJDl53xrZ7RjH+jT9IYE/9jzZd50CVPPfns+c/dAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIJJNAb2/vO05EV/2fOZPG87xSUMPafEKdL0+rz0gy62dkXsgkvUBWYROtnw6IyuQhs40MZepS5iGTjM0kcF7IA2dY24Wt+u6hg79xQP8HU1/jXcExAPzGE8iIBIBkQiIREAkAiLZ9zrHITZsSv2qHVQrEImASAREIiASAZEIiERAJAIiERDJg7xQ9kNfenyAPWsrEImASAREIiASAZEIiERAJAIiERCJgEge5F8Z3IsViERAJAIiERCJgEgERCIgEgGRCIhEQCQCIhEQiYBIBEQiIBIBkQiIREAkAiIREImASAREIiASAZEIiERAJAIiERCJgEgERCIgEgGRfADk8Dr46J8LAgAAAABJRU5ErkJggg=="

MANIFEST = """
{
  "name": "Mirror Mirror",
  "short_name": "Mirror Mirror",
  "start_url": "/",
  "display": "standalone",
  "background_color": "#111111",
  "theme_color": "#111111",
  "icons": [
    { "src": "/icon-192.png", "sizes": "192x192", "type": "image/png" }
  ]
}
"""

SW_JS = """
self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', () => self.clients.claim());
self.addEventListener('fetch', () => {});
"""

HTML = """
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<title>Mirror Mirror</title>
<meta name="theme-color" content="#111111">
<meta name="apple-mobile-web-app-capable" content="yes">
<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
<meta name="apple-mobile-web-app-title" content="Mirror Mirror">
<link rel="apple-touch-icon" href="/icon-192.png">
<script>
  var isIOS = /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;
  if (!isIOS) {
    var link = document.createElement('link');
    link.rel = 'manifest';
    link.href = '/manifest.json';
    document.head.appendChild(link);
  }
</script>
<style>
  * { margin:0; padding:0; box-sizing:border-box; -webkit-user-select:none; user-select:none; }
  body { background:#111; display:flex; flex-direction:column; align-items:center; justify-content:center;
         height:100vh; overflow:hidden; font-family:sans-serif; touch-action:none; }
  video { width:100vw; height:100vh; object-fit:contain; background:#000; touch-action:none; }
  #status { color:#0f0; font-family:monospace; position:fixed; top:10px; left:10px;
            background:rgba(0,0,0,0.85); padding:8px 14px; border-radius:8px; font-size:12px;
            z-index:10; max-width:60%; white-space:pre-line; pointer-events:none; }
  #menuToggle { position:fixed; top:12px; right:12px; z-index:25; width:44px; height:44px;
                border-radius:50%; background:rgba(0,0,0,0.6); border:none; color:#fff;
                font-size:20px; display:flex; align-items:center; justify-content:center;
                cursor:pointer; padding:0; }
  #menuBackdrop { position:fixed; inset:0; background:rgba(0,0,0,0.4); z-index:19;
                  opacity:0; pointer-events:none; transition:opacity 0.2s ease; }
  #menuBackdrop.open { opacity:1; pointer-events:auto; }
  #menuPanel { position:fixed; top:0; right:-240px; width:220px; height:100%;
               background:#181818; z-index:20; padding:70px 16px 16px;
               display:flex; flex-direction:column; gap:10px;
               transition:right 0.25s ease; box-shadow:-4px 0 20px rgba(0,0,0,0.4); }
  #menuPanel.open { right:0; }
  #menuPanel button, #menuPanel input { width:100%; }
  button { background:#333; color:#fff; border:none; padding:10px 22px; border-radius:6px;
           cursor:pointer; font-size:14px; }
  button.primary { background:#0066cc; }
  button.danger { background:#cc3333; }
  button:disabled { opacity:0.5; cursor:not-allowed; }
  input { background:#222; color:#fff; border:1px solid #444; padding:10px 14px; border-radius:6px;
          font-size:14px; width:110px; text-align:center; }
</style>
</head>
<body>
  <div id="status">loading...</div>
  <video id="remoteVideo" autoplay playsinline></video>
  <button id="menuToggle" onclick="toggleMenu()">&#9776;</button>
  <div id="menuBackdrop" onclick="toggleMenu()"></div>
  <div id="menuPanel">
    <div id="rememberedList" style="display:none; flex-direction:column; gap:6px;">
      <div style="font-size:11px; color:#888; text-transform:uppercase; letter-spacing:1px;">Previously connected</div>
      <div class="card" style="background:#222; border:1px solid #444; border-radius:8px; padding:10px; display:flex; flex-direction:column; gap:6px;">
        <div id="rememberedLabel" style="font-size:13px; color:#ccc;"></div>
        <div style="display:flex; gap:8px;">
          <button onclick="reconnectToDevice()" class="primary" style="flex:1; padding:8px; font-size:13px;">Reconnect</button>
          <button onclick="forgetDevice()" style="padding:8px 12px; font-size:13px;">Forget</button>
        </div>
      </div>
    </div>
    <div id="codeDisplay" style="display:none; align-items:center; justify-content:center; gap:10px; padding:10px 0;">
      <span id="codeText" style="font-size:28px; letter-spacing:4px; font-family:monospace; color:#0f0;"></span>
      <button id="newCodeBtn" onclick="requestNewCode()" title="Get a new code" style="display:flex; align-items:center; justify-content:center; width:30px; height:30px; padding:0; background:transparent; border:1px solid #444; border-radius:50%; flex-shrink:0;">
        <svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="#0f0" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"></polyline>
          <polyline points="1 20 1 14 7 14"></polyline>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"></path>
        </svg>
      </button>
    </div>
    <input id="codeInput" type="text" inputmode="numeric" maxlength="6" placeholder="Enter 6-digit code">
    <button id="shareBtn" class="primary" onclick="startSharing()">Share Screen</button>
    <button id="viewBtn" onclick="viewScreen()">View Screen</button>
    <button id="disconnectBtn" class="danger" onclick="disconnect()" disabled>Disconnect</button>
  </div>

<script src="https://cdnjs.cloudflare.com/ajax/libs/socket.io/4.7.5/socket.io.min.js"></script>
<script>
const socket = io();
const video = document.getElementById('remoteVideo');
const statusEl = document.getElementById('status');
const shareBtn = document.getElementById('shareBtn');
const viewBtn = document.getElementById('viewBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const codeInput = document.getElementById('codeInput');
const codeDisplay = document.getElementById('codeDisplay');
const codeText = document.getElementById('codeText');
const newCodeBtn = document.getElementById('newCodeBtn');
const rememberedList = document.getElementById('rememberedList');
const rememberedLabel = document.getElementById('rememberedLabel');

let pc = null;
let localStream = null;
let sessionCode = null;
let sessionToken = null;
let role = null;

if (!navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
  shareBtn.style.display = 'none';
}

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/sw.js').catch(() => {});
}

function log(msg, color='#0f0') {
  statusEl.textContent = msg;
  statusEl.style.color = color;
}

function toggleMenu() {
  document.getElementById('menuPanel').classList.toggle('open');
  document.getElementById('menuBackdrop').classList.toggle('open');
}

function closeMenu() {
  document.getElementById('menuPanel').classList.remove('open');
  document.getElementById('menuBackdrop').classList.remove('open');
}

async function newPC() {
  const conn = new RTCPeerConnection({
    iceServers: [
      { urls: 'stun:stun.l.google.com:19302' },
      { urls: 'stun:stun1.l.google.com:19302' }
    ],
    iceCandidatePoolSize: 4
  });
  conn.onicecandidate = (e) => {
    if (e.candidate) socket.emit('ice_candidate', { candidate: e.candidate, room: sessionCode, token: sessionToken });
  };
  conn.oniceconnectionstatechange = () => log('ICE: ' + conn.iceConnectionState);
  conn.onconnectionstatechange = () => {
    log('Connection: ' + conn.connectionState);
    if (conn.connectionState === 'connected' && role === 'sharer') {
      conn.getSenders().forEach(boostBitrate);
    }
  };
  return conn;
}

async function boostBitrate(sender) {
  if (!sender.track || sender.track.kind !== 'video') return;
  try {
    const params = sender.getParameters();
    if (!params.encodings) params.encodings = [{}];
    params.encodings[0].maxBitrate = 12_000_000;
    params.encodings[0].scaleResolutionDownBy = 1;
    params.degradationPreference = 'maintain-framerate';
    await sender.setParameters(params);
  } catch (e) {}
}

async function createAndSendOffer() {
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  socket.emit('offer', { sdp: pc.localDescription.sdp, type: pc.localDescription.type, room: sessionCode, token: sessionToken });
}

async function startSharing() {
  try {
    role = 'sharer';
    log('Requesting screen capture...', '#ff0');
    localStream = await navigator.mediaDevices.getDisplayMedia({
      video: { width: 1920, height: 1080, frameRate: { ideal: 30, max: 60 } },
      audio: { echoCancellation: false, noiseSuppression: false, autoGainControl: false }
    });
    localStream.getVideoTracks()[0].contentHint = 'motion';
    shareBtn.disabled = true; viewBtn.disabled = true; disconnectBtn.disabled = false;
    pc = await newPC();
    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
    localStream.getVideoTracks()[0].onended = disconnect;
    socket.emit('host_create_session');
  } catch (e) {
    log('Error: ' + e.message, '#f00');
    shareBtn.disabled = false; viewBtn.disabled = false;
  }
}

function requestNewCode() {
  if (role !== 'sharer' || !sessionCode || !sessionToken) return;
  socket.emit('refresh_code', { room: sessionCode, token: sessionToken });
}

async function viewScreen() {
  try {
    const code = codeInput.value.trim();
    if (!/^\\d{6}$/.test(code)) {
      log('Enter the 6-digit code shown on the host', '#f00');
      return;
    }
    role = 'viewer';
    shareBtn.disabled = true; viewBtn.disabled = true; disconnectBtn.disabled = false;
    pc = await newPC();
    pc.ontrack = (event) => { video.srcObject = event.streams[0]; video.play().catch(() => {}); log('Streaming', '#0f0'); };
    socket.emit('viewer_join_session', { code });
    setupTouchpad();
  } catch (e) {
    log('Error: ' + e.message, '#f00');
    shareBtn.disabled = false; viewBtn.disabled = false;
  }
}

function renderRememberedDevice() {
  const saved = localStorage.getItem('mirrorMirrorDevice');
  if (!saved) {
    rememberedList.style.display = 'none';
    return;
  }
  const info = JSON.parse(saved);
  rememberedLabel.textContent = 'Last connected ' + new Date(info.lastConnected).toLocaleString();
  rememberedList.style.display = 'flex';
}

async function reconnectToDevice() {
  const saved = localStorage.getItem('mirrorMirrorDevice');
  if (!saved) return;
  const info = JSON.parse(saved);
  role = 'viewer';
  log('Reconnecting...', '#ff0');
  pc = await newPC();
  pc.ontrack = (event) => { video.srcObject = event.streams[0]; video.play().catch(() => {}); log('Streaming', '#0f0'); };
  setupTouchpad();
  socket.emit('quick_reconnect', { device_token: info.token });
}

function forgetDevice() {
  const saved = localStorage.getItem('mirrorMirrorDevice');
  if (saved) {
    const info = JSON.parse(saved);
    socket.emit('forget_device', { device_token: info.token });
  }
  localStorage.removeItem('mirrorMirrorDevice');
  renderRememberedDevice();
}

socket.on('quick_reconnect_unavailable', () => {
  if (pc) { pc.close(); pc = null; }
  role = null;
  log('Host isn\\'t sharing right now', '#f00');
});

function disconnect() {
  if (sessionCode && sessionToken && role) {
    socket.emit('leave_session', { room: sessionCode, token: sessionToken, role: role === 'sharer' ? 'host' : 'viewer' });
  }
  if (pc) { pc.close(); pc = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  video.srcObject = null;
  log('Disconnected', '#aaa');
  shareBtn.disabled = false; viewBtn.disabled = false; disconnectBtn.disabled = true;
  codeDisplay.style.display = 'none';
  sessionCode = null; sessionToken = null; role = null;
  renderRememberedDevice();
}

socket.on('session_created', (data) => {
  sessionCode = data.code;
  sessionToken = data.token;
  codeText.textContent = data.code;
  codeDisplay.style.display = 'flex';
  log('Code ' + data.code + ' -- waiting for a viewer...', '#0f0');
});

socket.on('session_joined', (data) => {
  sessionCode = data.code;
  sessionToken = data.token;
  if (data.device_token) {
    localStorage.setItem('mirrorMirrorDevice', JSON.stringify({
      token: data.device_token,
      lastConnected: Date.now()
    }));
  }
  log('Paired -- waiting for video...', '#ff0');
  closeMenu();
});

socket.on('pairing_error', (data) => {
  log('Pairing failed: ' + data.message, '#f00');
  shareBtn.disabled = false; viewBtn.disabled = false; disconnectBtn.disabled = true;
});

socket.on('code_refreshed', () => {
  disconnect();
  log('Host started a new code', '#f00');
});

socket.on('peer_joined', async (data) => {
  if (role === 'sharer' && data.role === 'viewer') {
    log('Viewer joined, connecting...', '#0f0');
    closeMenu();
    if (!pc || pc.connectionState === 'closed') {
      pc = await newPC();
      localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
    }
    await createAndSendOffer();
  }
});
socket.on('peer_left', () => { log('Other side disconnected', '#f00'); disconnect(); });

socket.on('viewer_disconnected', () => {
  if (role !== 'sharer') return;
  if (pc) { pc.close(); pc = null; }
  log('Viewer disconnected -- waiting to reconnect...', '#ff0');
});

socket.on('rejoined', () => { log('Reconnected -- restoring video...', '#ff0'); });

socket.on('please_reoffer', async () => {
  if (role !== 'sharer' || !localStream) return;
  if (pc) { pc.close(); pc = null; }
  pc = await newPC();
  localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
  await createAndSendOffer();
});

socket.on('offer', async (data) => {
  if (role !== 'viewer') return;
  try {
    if (pc) { pc.close(); }
    pc = await newPC();
    pc.ontrack = (event) => { video.srcObject = event.streams[0]; video.play().catch(() => {}); log('Streaming', '#0f0'); };
    await pc.setRemoteDescription(new RTCSessionDescription(data));
    const answer = await pc.createAnswer();
    await pc.setLocalDescription(answer);
    socket.emit('answer', { sdp: pc.localDescription.sdp, type: pc.localDescription.type, room: sessionCode, token: sessionToken });
  } catch (e) { log('Offer error: ' + e.message, '#f00'); }
});
socket.on('answer', async (data) => {
  if (role !== 'sharer') return;
  try { await pc.setRemoteDescription(new RTCSessionDescription(data)); }
  catch (e) { log('Answer error: ' + e.message, '#f00'); }
});
socket.on('ice_candidate', async (data) => {
  if (!pc) return;
  try { await pc.addIceCandidate(new RTCIceCandidate(data.candidate)); } catch (e) {}
});

socket.on('connect', () => {
  if (sessionCode && sessionToken && role) {
    log('Reconnecting...', '#ff0');
    socket.emit('rejoin_session', { code: sessionCode, token: sessionToken, role: role === 'sharer' ? 'host' : 'viewer' });
  } else {
    log('Connected -- pick a role', '#0f0');
    renderRememberedDevice();
    if (localStorage.getItem('mirrorMirrorDevice')) {
      toggleMenu();
    }
  }
});
socket.on('disconnect', () => {
  log('Connection lost, retrying...', '#f00');
});

const SENSITIVITY = 1.6;
let touchState = null;

function setupTouchpad() {
  video.addEventListener('touchstart', onTouchStart, { passive: false });
  video.addEventListener('touchmove', onTouchMove, { passive: false });
  video.addEventListener('touchend', onTouchEnd, { passive: false });
}

function onTouchStart(e) {
  e.preventDefault();
  const touches = e.touches;
  touchState = {
    fingers: touches.length,
    startX: touches[0].clientX, startY: touches[0].clientY,
    lastX: touches[0].clientX, lastY: touches[0].clientY,
    moved: false, startTime: Date.now()
  };
}

function onTouchMove(e) {
  e.preventDefault();
  if (!touchState) return;
  const t = e.touches[0];
  const dx = (t.clientX - touchState.lastX) * SENSITIVITY;
  const dy = (t.clientY - touchState.lastY) * SENSITIVITY;
  touchState.lastX = t.clientX; touchState.lastY = t.clientY;
  if (Math.abs(t.clientX - touchState.startX) > 6 || Math.abs(t.clientY - touchState.startY) > 6) {
    touchState.moved = true;
  }
  if (touchState.fingers >= 2) {
    socket.emit('mouse_scroll', { room: sessionCode, token: sessionToken, dy: dy });
  } else {
    socket.emit('mouse_move', { room: sessionCode, token: sessionToken, dx: dx, dy: dy });
  }
}

function onTouchEnd(e) {
  e.preventDefault();
  if (!touchState) return;
  const elapsed = Date.now() - touchState.startTime;
  if (!touchState.moved && elapsed < 300) {
    if (touchState.fingers >= 2) socket.emit('mouse_click', { room: sessionCode, token: sessionToken, button: 'right' });
    else socket.emit('mouse_click', { room: sessionCode, token: sessionToken, button: 'left' });
  }
  touchState = null;
}
</script>
</body>
</html>
"""


@app.route('/')
def index():
    return render_template_string(HTML)


@app.route('/manifest.json')
def manifest():
    resp = Response(MANIFEST, mimetype='application/manifest+json')
    resp.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return resp


@app.route('/sw.js')
def sw():
    resp = Response(SW_JS, mimetype='application/javascript')
    resp.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return resp


@app.route('/icon-192.png')
def icon():
    resp = Response(base64.b64decode(ICON_B64), mimetype='image/png')
    resp.headers['Cache-Control'] = 'no-cache, max-age=0, must-revalidate'
    return resp


tray_status = {"code": None, "connected": False}


@socketio.on('host_create_session')
def handle_host_create_session():
    code = _new_code()
    token = secrets.token_hex(16)
    sessions[code] = {
        'host_sid': request.sid,
        'viewer_sid': None,
        'token': token,
        'created_at': time.time(),
        'claimed': False,
    }
    join_room(code)
    tray_status["code"] = code
    tray_status["connected"] = False
    emit('session_created', {'code': code, 'token': token})


@socketio.on('refresh_code')
def handle_refresh_code(data):
    old_code, token = data.get('room'), data.get('token')
    s = sessions.get(old_code)
    if not s or s.get('token') != token or s.get('host_sid') != request.sid:
        return
    if s['claimed'] and s.get('viewer_sid'):
        emit('code_refreshed', room=s['viewer_sid'])
    new_code = _new_code()
    new_token = secrets.token_hex(16)
    sessions[new_code] = {
        'host_sid': request.sid,
        'viewer_sid': None,
        'token': new_token,
        'created_at': time.time(),
        'claimed': False,
    }
    del sessions[old_code]
    join_room(new_code)
    tray_status["code"] = new_code
    tray_status["connected"] = False
    emit('session_created', {'code': new_code, 'token': new_token})


def _complete_pairing(code, s, sid, existing_device_token=None):
    s['claimed'] = True
    s['viewer_sid'] = sid
    join_room(code)
    tray_status["connected"] = True

    if existing_device_token and existing_device_token in trusted_devices:
        device_token = existing_device_token
    else:
        device_token = secrets.token_hex(16)
        trusted_devices[device_token] = {'added_at': time.time()}
        _save_trusted_devices()

    s['device_token'] = device_token
    emit('session_joined', {'code': code, 'token': s['token'], 'device_token': device_token})
    emit('peer_joined', {'role': 'viewer'}, room=s['host_sid'])


@socketio.on('viewer_join_session')
def handle_viewer_join_session(data):
    ip = request.remote_addr
    if _rate_limited(ip):
        emit('pairing_error', {'message': 'Too many attempts, try again later'})
        return
    code = (data.get('code') or '').strip()
    s = _session_for(code)
    if not s:
        _record_failed_attempt(ip)
        emit('pairing_error', {'message': 'Invalid or expired code'})
        return
    if s['claimed']:
        _record_failed_attempt(ip)
        emit('pairing_error', {'message': 'Code already used'})
        return
    _complete_pairing(code, s, request.sid)


@socketio.on('quick_reconnect')
def handle_quick_reconnect(data):
    device_token = data.get('device_token')
    if not device_token or device_token not in trusted_devices:
        emit('quick_reconnect_unavailable')
        return

    for code, s in sessions.items():
        if s.get('device_token') == device_token and s.get('host_sid'):
            _complete_pairing(code, s, request.sid, existing_device_token=device_token)
            return

    waiting_code = None
    for code, s in sessions.items():
        if not s['claimed'] and s.get('host_sid'):
            waiting_code = code
            break
    if not waiting_code:
        emit('quick_reconnect_unavailable')
        return
    _complete_pairing(waiting_code, sessions[waiting_code], request.sid, existing_device_token=device_token)


@socketio.on('leave_session')
def handle_leave_session(data):
    code, token, role = data.get('room'), data.get('token'), data.get('role')
    s = sessions.get(code)
    if not s or s.get('token') != token:
        return
    if role == 'host' and s.get('host_sid') == request.sid:
        if s.get('viewer_sid'):
            emit('peer_left', room=s['viewer_sid'])
        sessions.pop(code, None)
        tray_status["code"] = None
        tray_status["connected"] = False
    elif role == 'viewer' and s.get('viewer_sid') == request.sid:
        s['viewer_sid'] = None
        s['viewer_disconnected_at'] = time.time()
        tray_status["connected"] = False
        if s.get('host_sid'):
            emit('viewer_disconnected', room=s['host_sid'])


@socketio.on('forget_device')
def handle_forget_device(data):
    device_token = data.get('device_token')
    if device_token in trusted_devices:
        del trusted_devices[device_token]
        _save_trusted_devices()


@socketio.on('disconnect')
def handle_disconnect():
    for code, s in list(sessions.items()):
        if s['host_sid'] == request.sid:
            s['host_sid'] = None
            s['host_disconnected_at'] = time.time()
            tray_status["connected"] = False
        elif s['viewer_sid'] == request.sid:
            s['viewer_sid'] = None
            s['viewer_disconnected_at'] = time.time()
            tray_status["connected"] = False
            if s.get('host_sid'):
                emit('viewer_disconnected', room=s['host_sid'])


@socketio.on('rejoin_session')
def handle_rejoin_session(data):
    code, token, role = data.get('code'), data.get('token'), data.get('role')
    s = sessions.get(code)
    if not s or s.get('token') != token:
        emit('pairing_error', {'message': 'Session expired, please reconnect'})
        return
    if role == 'host':
        s['host_sid'] = request.sid
        s.pop('host_disconnected_at', None)
    else:
        s['viewer_sid'] = request.sid
        s.pop('viewer_disconnected_at', None)
    join_room(code)
    tray_status["connected"] = bool(s.get('host_sid') and s.get('viewer_sid'))
    emit('rejoined', {'code': code})
    if s.get('host_sid') and s.get('viewer_sid'):
        emit('please_reoffer', room=s['host_sid'])


def _sweep_expired_sessions():
    while True:
        socketio.sleep(15)
        now = time.time()
        for code, s in list(sessions.items()):
            host_gone = s['host_sid'] is None and now - s.get('host_disconnected_at', now) > DISCONNECT_GRACE
            if host_gone:
                if s.get('viewer_sid'):
                    socketio.emit('peer_left', room=s['viewer_sid'], namespace='/')
                del sessions[code]
                tray_status["code"] = None
                tray_status["connected"] = False
                continue

            viewer_gone = (
                s['viewer_sid'] is None and s['claimed']
                and now - s.get('viewer_disconnected_at', now) > VIEWER_RECONNECT_GRACE
            )
            if viewer_gone:
                if s.get('host_sid'):
                    socketio.emit('peer_left', room=s['host_sid'], namespace='/')
                del sessions[code]
                tray_status["code"] = None
                tray_status["connected"] = False


@socketio.on('offer')
def handle_offer(data):
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'host'):
        return
    emit('offer', data, room=code, include_self=False)


@socketio.on('answer')
def handle_answer(data):
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'viewer'):
        return
    emit('answer', data, room=code, include_self=False)


@socketio.on('ice_candidate')
def handle_ice_candidate(data):
    code, token = data.get('room'), data.get('token')
    s = sessions.get(code)
    if not s or s.get('token') != token or request.sid not in (s.get('host_sid'), s.get('viewer_sid')):
        return
    emit('ice_candidate', data, room=code, include_self=False)


@socketio.on('mouse_move')
def handle_mouse_move(data):
    if not HAVE_PYAUTOGUI:
        return
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'viewer'):
        return
    dx, dy = data.get('dx', 0), data.get('dy', 0)
    pyautogui.moveRel(dx, dy, duration=0, _pause=False)


@socketio.on('mouse_click')
def handle_mouse_click(data):
    if not HAVE_PYAUTOGUI:
        return
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'viewer'):
        return
    pyautogui.click(button=data.get('button', 'left'), _pause=False)


@socketio.on('mouse_scroll')
def handle_mouse_scroll(data):
    if not HAVE_PYAUTOGUI:
        return
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'viewer'):
        return
    pyautogui.scroll(int(-data.get('dy', 0) * 2), _pause=False)


if __name__ == '__main__':
    import io
    import socket
    import sys
    import threading
    import webbrowser
    import pystray
    from PIL import Image

    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    def get_lan_ip():
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(('8.8.8.8', 80))
            return s.getsockname()[0]
        except Exception:
            return '127.0.0.1'
        finally:
            s.close()

    LAN_IP = get_lan_ip()
    LAN_URL = f"http://{LAN_IP}:5000"

    def run_server():
        socketio.run(app, host='0.0.0.0', port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)

    UPDATE_CHECK_INTERVAL = 6 * 60 * 60
    update_status = {"available": False, "version": None, "url": f"https://github.com/{UPDATE_REPO}/releases/latest"}

    def _parse_version(v):
        v = (v or "").strip().lstrip("vV")
        try:
            return tuple(int(p) for p in v.split("."))
        except ValueError:
            return None

    def _check_for_update():
        import urllib.request
        try:
            req = urllib.request.Request(
                f"https://api.github.com/repos/{UPDATE_REPO}/releases/latest",
                headers={"Accept": "application/vnd.github+json", "User-Agent": "MirrorMirror-UpdateCheck"},
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.load(resp)
            latest_tag = data.get("tag_name", "")
            latest = _parse_version(latest_tag)
            current = _parse_version(APP_VERSION)
            if not latest or not current or latest <= current:
                return
            already_notified = update_status["available"] and update_status["version"] == latest_tag
            update_status["available"] = True
            update_status["version"] = latest_tag
            update_status["url"] = data.get("html_url") or update_status["url"]
            if not already_notified:
                tray_icon.notify(
                    f"Version {latest_tag} is available (you're on {APP_VERSION}). "
                    "Click the tray icon to download it.",
                    "Mirror Mirror update available",
                )
        except Exception as e:
            print(f"[warn] update check failed: {e}")

    def _update_check_loop():
        time.sleep(5)
        while True:
            _check_for_update()
            time.sleep(UPDATE_CHECK_INTERVAL)

    def status_label(item):
        if tray_status["connected"]:
            return "Status: Connected"
        if tray_status["code"]:
            return f"Code: {tray_status['code']}"
        return "Status: Idle"

    def address_label(item):
        return f"On phone, go to: {LAN_URL}"

    def copy_address(icon, item):
        try:
            import pyperclip
            pyperclip.copy(LAN_URL)
            icon.notify(f"Copied {LAN_URL} to clipboard", "Mirror Mirror")
        except Exception:
            pass

    def open_app(icon, item):
        webbrowser.open("http://localhost:5000")

    def update_label(item):
        if update_status["available"]:
            return f"Update available: {update_status['version']} (click)"
        return f"Version {APP_VERSION}"

    def open_update(icon, item):
        if update_status["available"]:
            webbrowser.open(update_status["url"])

    def quit_app(icon, item):
        icon.stop()
        import os
        os._exit(0)

    icon_image = Image.open(io.BytesIO(base64.b64decode(ICON_B64)))
    menu = pystray.Menu(
        pystray.MenuItem(status_label, None, enabled=False),
        pystray.MenuItem(address_label, copy_address),
        pystray.MenuItem(update_label, open_update),
        pystray.MenuItem("Open Mirror Mirror", open_app, default=True),
        pystray.MenuItem("Quit", quit_app),
    )
    tray_icon = pystray.Icon("MirrorMirror", icon_image, "Mirror Mirror", menu)

    def _show_welcome_popup():
        try:
            import tkinter as tk
            from tkinter import messagebox
            _root = tk.Tk()
            _root.withdraw()
            messagebox.showinfo(
                "Mirror Mirror",
                "Mirror Mirror is now running in the system tray.\n\n"
                "Right-click the tray icon to see your pairing code or address, "
                "or left-click to open the app."
            )
            _root.destroy()
        except Exception:
            pass

    socketio.start_background_task(_sweep_expired_sessions)
    threading.Thread(target=run_server, daemon=True).start()
    threading.Thread(target=_update_check_loop, daemon=True).start()
    threading.Thread(target=_show_welcome_popup, daemon=True).start()
    tray_icon.run()
