import base64
import math
import os
import secrets
import threading
import time
from collections import defaultdict
from functools import wraps
from flask import Flask, render_template_string, request, Response, abort
from flask_socketio import SocketIO, emit, join_room, leave_room

app = Flask(__name__)
socketio = SocketIO(app, async_mode="threading", max_http_buffer_size=65536)

APP_VERSION = "2.0.0"

sessions = {}
# Never send this capability to LAN viewers. It authorizes local host sockets,
# independently of the credentials issued to a paired viewer.
_host_capability = secrets.token_hex(32)
_host_sids = set()
_connected_sids = set()
_session_lock = threading.RLock()
_LOCAL_HOSTS = {'localhost:5000', '127.0.0.1:5000', '[::1]:5000'}
PAIRING_TTL = 300
APPROVAL_TTL = 60
SESSION_TTL = 60 * 60
IDLE_TTL = 15 * 60

MAX_ATTEMPTS_PER_WINDOW = 10
ATTEMPT_WINDOW_SECONDS = 60
_failed_attempts = defaultdict(list)


def _locked(handler):
    @wraps(handler)
    def wrapped(*args):
        # Malformed event payloads are ignored before reaching any handler.
        if args and not isinstance(args[0], dict):
            return
        with _session_lock:
            if not args and handler.__name__ != 'handle_host_create_session':
                args = ({},)
            return handler(*args)
    return wrapped


def _local_host_request():
    # Check the peer address, not spoofable Forwarded/X-Forwarded-For headers.
    # Literal Host names also prevent DNS rebinding from exposing the capability.
    return (request.remote_addr in {'127.0.0.1', '::1'}
            and request.host.lower() in _LOCAL_HOSTS)


@socketio.on('connect')
def handle_connect(auth=None):
    if not request.is_secure and not _local_host_request():
        return False
    auth = auth if isinstance(auth, dict) else {}
    capability = auth.get('host_capability')
    with _session_lock:
        _connected_sids.add(request.sid)
        if (_local_host_request() and isinstance(capability, str)
                and secrets.compare_digest(capability, _host_capability)):
            _host_sids.add(request.sid)

_data_dir = os.path.join(os.environ.get('LOCALAPPDATA', os.path.expanduser('~')), 'MirrorMirror')
os.makedirs(_data_dir, exist_ok=True)


def _rate_limited(ip):
    now = time.monotonic()
    recent = [t for t in _failed_attempts[ip] if now - t < ATTEMPT_WINDOW_SECONDS]
    _failed_attempts[ip] = recent
    return len(recent) >= MAX_ATTEMPTS_PER_WINDOW


def _record_failed_attempt(ip):
    _failed_attempts[ip].append(time.monotonic())

try:
    import pyautogui
    pyautogui.FAILSAFE = True
    HAVE_PYAUTOGUI = True
except Exception:
    HAVE_PYAUTOGUI = False



def _new_code():
    while True:
        code = f"{secrets.randbelow(1_000_000):06d}"
        if code not in sessions:
            return code


def _session_for(code):
    if not isinstance(code, str):
        return None
    s = sessions.get(code)
    if not s:
        return None
    now = time.monotonic()
    if ((not s['claimed'] and now - s['created_at'] > PAIRING_TTL)
            or (s['claimed'] and (now - s['approved_at'] > SESSION_TTL
                                 or now - s['last_activity'] > IDLE_TTL))):
        _end_session(code, 'Session expired; start sharing again')
        return None
    return s


def _authorized(code, token, sid, role):
    s = _session_for(code)
    if not s:
        return False
    if role == 'host':
        return (sid in _host_sids and s.get('host_sid') == sid
                and s.get('host_token') == token)
    if role == 'viewer':
        return (s.get('host_sid') in _host_sids and s.get('claimed')
                and s.get('viewer_sid') == sid and s.get('token') == token)
    return False


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
  #menuPanel { overflow-y:auto; }
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
    {% if host_capability %}
    <details style="color:#ddd;font-size:13px;line-height:1.5;overflow:auto;">
      <summary>iPhone HTTPS setup</summary>
      <p>Phone address: <strong>{{ lan_url }}</strong></p>
      <p><a href="/setup/iphone-profile" style="color:#8cf;">Download iPhone certificate profile</a></p>
      <p>Transfer this public profile directly to your iPhone, for example using your own iCloud Drive. Open it on the iPhone, then install it in Settings &gt; General &gt; VPN &amp; Device Management.</p>
      <p>Enable full trust for <strong>{{ ca_name }}</strong> in Settings &gt; General &gt; About &gt; Certificate Trust Settings. Verify this root's SHA-256 fingerprint before trusting it:</p>
      <code style="display:block;overflow-wrap:anywhere;">{{ fingerprint }}</code>
      <p>This adds a local certificate authority to your phone. Keep its private keys on this PC; remove its profile when you stop using the app. Do not bypass Safari certificate warnings.</p>
    </details>
    {% endif %}
    <div id="approvalPanel" style="display:none;color:#fff;gap:8px;flex-direction:column;">
      <strong>Approve your iPhone</strong>
      <span id="approvalLabel" style="font-size:13px;"></span>
      <span style="font-size:12px;">Approve only if you just entered this code on your phone.</span>
      <button onclick="approveConnection(true, false)">Allow view only</button>
      <button onclick="approveConnection(true, true)" class="primary">Allow view + mouse</button>
      <button onclick="approveConnection(false, false)" class="danger">Deny</button>
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
    <p style="color:#aaa;font-size:11px;line-height:1.5;">Each connection needs PC approval. Sessions end after 15 minutes without mouse activity or 60 minutes total.</p>
  </div>

<script src="/static/vendor/socket.io.min.js"></script>
<script>
const hostCapability = {{ host_capability|tojson }};
const socket = io({ auth: { host_capability: hostCapability } });
const video = document.getElementById('remoteVideo');
const statusEl = document.getElementById('status');
const shareBtn = document.getElementById('shareBtn');
const viewBtn = document.getElementById('viewBtn');
const disconnectBtn = document.getElementById('disconnectBtn');
const codeInput = document.getElementById('codeInput');
const codeDisplay = document.getElementById('codeDisplay');
const codeText = document.getElementById('codeText');
const newCodeBtn = document.getElementById('newCodeBtn');
const approvalPanel = document.getElementById('approvalPanel');
const approvalLabel = document.getElementById('approvalLabel');
let pendingApproval = null;
let canControl = false;
try { localStorage.removeItem('mirrorMirrorDevice'); } catch (e) {}

let pc = null;
let localStream = null;
let sessionCode = null;
let sessionToken = null;
let role = null;
let iceRecoveryTimer = null;
let reofferInProgress = false;

if (!hostCapability || !navigator.mediaDevices || !navigator.mediaDevices.getDisplayMedia) {
  shareBtn.style.display = 'none';
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
    iceServers: [],
    iceCandidatePoolSize: 4
  });
  conn.onicecandidate = (e) => {
    if (e.candidate) socket.emit('ice_candidate', { candidate: e.candidate, room: sessionCode, token: sessionToken });
  };
  conn.oniceconnectionstatechange = () => {
    log('ICE: ' + conn.iceConnectionState);
    const state = conn.iceConnectionState;
    if (state === 'connected' || state === 'completed') {
      if (iceRecoveryTimer) { clearTimeout(iceRecoveryTimer); iceRecoveryTimer = null; }
      return;
    }
    if (state !== 'disconnected' && state !== 'failed') return;
    if (iceRecoveryTimer || role !== 'sharer') return;
    const delay = state === 'failed' ? 1000 : 5000;
    iceRecoveryTimer = setTimeout(() => {
      iceRecoveryTimer = null;
      if (conn !== pc) return;
      reofferFromHost();
    }, delay);
  };
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
    pc.ontrack = (event) => { video.srcObject = event.streams[0]; video.play().catch(() => {}); log(canControl ? 'Streaming -- mouse enabled' : 'Streaming -- view only', '#0f0'); };
    socket.emit('viewer_join_session', { code });
    setupTouchpad();
  } catch (e) {
    log('Error: ' + e.message, '#f00');
    shareBtn.disabled = false; viewBtn.disabled = false;
  }
}

function approveConnection(allow, control) {
  if (!pendingApproval || !hostCapability || !sessionCode || !sessionToken) return;
  socket.emit('approve_viewer', { room: sessionCode, token: sessionToken,
    request_id: pendingApproval, allow, control });
  pendingApproval = null;
  approvalPanel.style.display = 'none';
}

socket.on('pairing_request', (data) => {
  if (role !== 'sharer' || data.room !== sessionCode) return;
  pendingApproval = data.request_id;
  approvalLabel.textContent = 'Device address: ' + data.ip;
  approvalPanel.style.display = 'flex';
  document.getElementById('menuPanel').classList.add('open');
  document.getElementById('menuBackdrop').classList.add('open');
  log('A phone is waiting for your approval', '#ff0');
});
socket.on('approval_cancelled', (data) => {
  if (pendingApproval === data.request_id) {
    pendingApproval = null;
    approvalPanel.style.display = 'none';
  }
});
socket.on('approval_pending', () => log('Waiting for approval on the PC', '#ff0'));

function disconnect(notify = true) {
  if (notify && socket.connected && role === 'viewer' && !sessionCode) socket.emit('cancel_pairing');
  if (notify && socket.connected && sessionCode && sessionToken && role) {
    socket.emit('leave_session', { room: sessionCode, token: sessionToken, role: role === 'sharer' ? 'host' : 'viewer' });
  }
  if (pc) { pc.close(); pc = null; }
  if (localStream) { localStream.getTracks().forEach(t => t.stop()); localStream = null; }
  video.srcObject = null;
  log('Disconnected', '#aaa');
  shareBtn.disabled = false; viewBtn.disabled = false; disconnectBtn.disabled = true;
  codeDisplay.style.display = 'none';
  sessionCode = null; sessionToken = null; role = null;
  canControl = false;
  pendingApproval = null;
  approvalPanel.style.display = 'none';
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
  canControl = data.control === true;
  log('Paired -- waiting for video...', '#ff0');
  closeMenu();
});

socket.on('pairing_error', (data) => {
  disconnect(false);
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
socket.on('peer_left', (data) => { disconnect(false); log(data.message || 'Session ended', '#f00'); });

async function reofferFromHost() {
  if (role !== 'sharer' || !localStream || reofferInProgress) return;
  reofferInProgress = true;
  try {
    if (pc) { pc.close(); pc = null; }
    pc = await newPC();
    localStream.getTracks().forEach(track => pc.addTrack(track, localStream));
    await createAndSendOffer();
  } finally {
    reofferInProgress = false;
  }
}

socket.on('please_reoffer', () => { reofferFromHost(); });

socket.on('offer', async (data) => {
  if (role !== 'viewer') return;
  try {
    if (pc) { pc.close(); }
    pc = await newPC();
    pc.ontrack = (event) => { video.srcObject = event.streams[0]; video.play().catch(() => {}); log(canControl ? 'Streaming -- mouse enabled' : 'Streaming -- view only', '#0f0'); };
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
  log(hostCapability ? 'Ready -- share your screen, then approve your phone' : 'Connected securely -- enter the PC code', '#0f0');
});
socket.on('disconnect', () => {
  disconnect(false);
  log('Connection lost -- a new code and PC approval are required', '#f00');
});
socket.on('connect_error', () => log('Connection rejected -- use the HTTPS phone address', '#f00'));

document.addEventListener('visibilitychange', () => {
  if (document.visibilityState !== 'visible' || role !== 'viewer' || !video.srcObject) return;
  const stream = video.srcObject;
  video.srcObject = null;
  requestAnimationFrame(() => {
    video.srcObject = stream;
    video.play().catch(() => {});
  });
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
  if (!canControl) return;
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
  if (!canControl || !touchState) return;
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
  if (!canControl || !touchState) return;
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
    identity = app.config.get('TLS_IDENTITY', {})
    response = app.make_response(render_template_string(
        HTML, host_capability=_host_capability if _local_host_request() else None,
        lan_url=app.config.get('LAN_URL', ''), ca_name=identity.get('name', ''),
        fingerprint=identity.get('fingerprint', '')))
    response.headers['Cache-Control'] = 'no-store'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = "frame-ancestors 'none'"
    return response


@app.before_request
def require_secure_transport():
    # The only HTTP listener binds to loopback, and the same check is enforced
    # here and at Socket.IO connection time as defense in depth.
    if not request.is_secure and not _local_host_request():
        abort(403)


@app.after_request
def security_headers(response):
    response.headers['X-Content-Type-Options'] = 'nosniff'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Frame-Options'] = 'DENY'
    response.headers['Content-Security-Policy'] = (
        "default-src 'self'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; media-src 'self' blob:; "
        "connect-src 'self' ws: wss:; frame-ancestors 'none'; "
        "base-uri 'none'; object-src 'none'")
    return response


@app.route('/setup/iphone-profile')
def iphone_profile():
    if not _local_host_request():
        abort(403)
    identity = app.config.get('TLS_IDENTITY')
    if not identity:
        abort(503)
    response = Response(identity['profile'], mimetype='application/x-apple-aspen-config')
    response.headers['Content-Disposition'] = 'attachment; filename="MirrorMirror-iPhone.mobileconfig"'
    response.headers['Cache-Control'] = 'no-store'
    return response


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


def _end_session(code, message='Session ended; start sharing again'):
    s = sessions.pop(code, None)
    if not s:
        return
    for sid in {s.get('host_sid'), s.get('viewer_sid'), s.get('pending', {}).get('sid')} - {None}:
        socketio.emit('peer_left', {'message': message}, to=sid)
        socketio.server.leave_room(sid, code, namespace='/')
    tray_status.update(code=None, connected=False)


def _cancel_pending(s, message):
    pending = s.pop('pending', None)
    if pending:
        socketio.emit('pairing_error', {'message': message}, to=pending['sid'])
        socketio.emit('approval_cancelled', {'request_id': pending['id']}, to=s['host_sid'])


@socketio.on('host_create_session')
@_locked
def handle_host_create_session():
    if request.sid not in _host_sids:
        emit('pairing_error', {'message': 'Start sharing from http://localhost:5000 on the PC'})
        return
    # One share at a time; a fresh host share revokes every previous approval.
    for old_code in list(sessions):
        _end_session(old_code)
    code = _new_code()
    host_token = secrets.token_hex(32)
    sessions[code] = {
        'host_sid': request.sid, 'viewer_sid': None,
        'host_token': host_token, 'token': secrets.token_hex(32),
        'created_at': time.monotonic(), 'claimed': False, 'control': False,
    }
    join_room(code)
    tray_status.update(code=code, connected=False)
    emit('session_created', {'code': code, 'token': host_token})


@socketio.on('refresh_code')
@_locked
def handle_refresh_code(data):
    code, token = data.get('room'), data.get('token')
    if not _authorized(code, token, request.sid, 'host'):
        return
    s = sessions.pop(code)
    _cancel_pending(s, 'Pairing code changed')
    if s.get('viewer_sid'):
        emit('code_refreshed', to=s['viewer_sid'])
        leave_room(code, sid=s['viewer_sid'])
    leave_room(code)
    handle_host_create_session()


@socketio.on('viewer_join_session')
@_locked
def handle_viewer_join_session(data):
    ip = request.remote_addr
    if _rate_limited(ip):
        emit('pairing_error', {'message': 'Too many attempts; try again later'})
        return
    _record_failed_attempt(ip)  # Bound valid-code prompt spam as well as guesses.
    code = data.get('code')
    s = _session_for(code)
    if (not s or s['claimed'] or s.get('host_sid') not in _host_sids
            or not request.is_secure):
        emit('pairing_error', {'message': 'Invalid, unavailable, or expired code; use HTTPS'})
        return
    if s.get('pending'):
        if time.monotonic() - s['pending']['created_at'] <= APPROVAL_TTL:
            emit('pairing_error', {'message': 'A connection is already awaiting PC approval'})
            return
        _cancel_pending(s, 'Approval timed out')
    pending = {'sid': request.sid, 'id': secrets.token_hex(16),
               'created_at': time.monotonic(), 'ip': ip}
    s['pending'] = pending
    emit('approval_pending')
    emit('pairing_request', {'room': code, 'request_id': pending['id'], 'ip': ip}, to=s['host_sid'])


@socketio.on('approve_viewer')
@_locked
def handle_approve_viewer(data):
    code = data.get('room')
    if not _authorized(code, data.get('token'), request.sid, 'host'):
        return
    s = sessions[code]
    pending = s.get('pending')
    if not pending or pending['id'] != data.get('request_id'):
        return
    if (time.monotonic() - pending['created_at'] > APPROVAL_TTL
            or pending['sid'] not in _connected_sids):
        _cancel_pending(s, 'Approval timed out; pair again')
        return
    if data.get('allow') is not True:
        _cancel_pending(s, 'Connection denied on the PC')
        return
    s.pop('pending')
    s.update(claimed=True, viewer_sid=pending['sid'], token=secrets.token_hex(32),
             control=data.get('control') is True, approved_at=time.monotonic(),
             last_activity=time.monotonic())
    join_room(code, sid=pending['sid'])
    tray_status['connected'] = True
    emit('session_joined', {'code': code, 'token': s['token'], 'control': s['control']}, to=pending['sid'])
    emit('peer_joined', {'role': 'viewer', 'control': s['control']}, to=s['host_sid'])


@socketio.on('quick_reconnect')
@_locked
def handle_quick_reconnect(data):
    # Old on-disk remembered credentials are deliberately never loaded.
    emit('quick_reconnect_unavailable')


@socketio.on('cancel_pairing')
@_locked
def handle_cancel_pairing(data):
    for s in sessions.values():
        if s.get('pending', {}).get('sid') == request.sid:
            _cancel_pending(s, 'Pairing cancelled')


@socketio.on('rejoin_session')
@_locked
def handle_rejoin_session(data):
    emit('pairing_error', {'message': 'Reconnections require a new code and PC approval'})


@socketio.on('leave_session')
@_locked
def handle_leave_session(data):
    code = data.get('room')
    if _authorized(code, data.get('token'), request.sid, data.get('role')):
        _end_session(code)


@socketio.on('disconnect')
def handle_disconnect(reason=None):
    with _session_lock:
        _host_sids.discard(request.sid)
        _connected_sids.discard(request.sid)
        for code, s in list(sessions.items()):
            if request.sid in (s.get('host_sid'), s.get('viewer_sid')):
                _end_session(code)
            elif s.get('pending', {}).get('sid') == request.sid:
                _cancel_pending(s, 'Viewer disconnected')


def _expire_sessions():
    with _session_lock:
        for code in list(sessions):
            s = _session_for(code)
            if s and s.get('pending') and time.monotonic() - s['pending']['created_at'] > APPROVAL_TTL:
                _cancel_pending(s, 'Approval timed out; pair again')


def _sweep_expired_sessions():
    while True:
        socketio.sleep(5)
        _expire_sessions()


@socketio.on('offer')
@_locked
def handle_offer(data):
    code = data.get('room')
    if not _authorized(code, data.get('token'), request.sid, 'host'):
        return
    peer = sessions[code].get('viewer_sid')
    if peer and isinstance(data.get('sdp'), str) and data.get('type') == 'offer':
        emit('offer', {'sdp': data['sdp'], 'type': 'offer'}, to=peer)


@socketio.on('answer')
@_locked
def handle_answer(data):
    code = data.get('room')
    if not _authorized(code, data.get('token'), request.sid, 'viewer'):
        return
    if isinstance(data.get('sdp'), str) and data.get('type') == 'answer':
        emit('answer', {'sdp': data['sdp'], 'type': 'answer'}, to=sessions[code]['host_sid'])


@socketio.on('ice_candidate')
@_locked
def handle_ice_candidate(data):
    code, token = data.get('room'), data.get('token')
    is_host = _authorized(code, token, request.sid, 'host')
    if not is_host and not _authorized(code, token, request.sid, 'viewer'):
        return
    s = sessions[code]
    peer = s.get('viewer_sid') if is_host else s.get('host_sid')
    if peer and isinstance(data.get('candidate'), dict):
        emit('ice_candidate', {'candidate': data['candidate']}, to=peer)


def _control_session(data):
    code = data.get('room')
    if not HAVE_PYAUTOGUI or not _authorized(code, data.get('token'), request.sid, 'viewer'):
        return None
    s = sessions[code]
    return s if s.get('control') else None


def _number(value, limit):
    return type(value) in (int, float) and abs(value) <= limit and math.isfinite(value)


def _mouse_call(s, method, *args, **kwargs):
    try:
        method(*args, _pause=False, **kwargs)
        s['last_activity'] = time.monotonic()
    except pyautogui.FailSafeException:
        _end_session(next(code for code, session in sessions.items() if session is s),
                     'Mouse fail-safe triggered; session ended')


@socketio.on('mouse_move')
@_locked
def handle_mouse_move(data):
    s = _control_session(data)
    dx, dy = data.get('dx', 0), data.get('dy', 0)
    if s and _number(dx, 1000) and _number(dy, 1000):
        _mouse_call(s, pyautogui.moveRel, dx, dy, duration=0)


@socketio.on('mouse_click')
@_locked
def handle_mouse_click(data):
    s = _control_session(data)
    button = data.get('button', 'left')
    if s and button in ('left', 'right'):
        _mouse_call(s, pyautogui.click, button=button)


@socketio.on('mouse_scroll')
@_locked
def handle_mouse_scroll(data):
    s = _control_session(data)
    dy = data.get('dy', 0)
    if s and _number(dy, 100):
        _mouse_call(s, pyautogui.scroll, int(-dy * 2))


def create_servers(lan_ip, local_port=5000, secure_port=5443):
    """Bind both listeners before launching; never fall back to LAN HTTP."""
    from tls_setup import prepare_tls
    from werkzeug.serving import make_server
    identity = prepare_tls(_data_dir, lan_ip)
    app.config['TLS_IDENTITY'] = identity
    local = make_server('127.0.0.1', local_port, app, threaded=True)
    try:
        secure = make_server('0.0.0.0', secure_port, app, threaded=True, ssl_context=identity['context'])
    except BaseException:
        local.server_close()
        raise
    app.config['LAN_URL'] = f'https://{lan_ip}:{secure.server_port}'
    return local, secure


if __name__ == '__main__':
    import io
    import socket
    import sys
    import webbrowser
    import pystray
    from PIL import Image

    if sys.stdout is None:
        sys.stdout = open(os.devnull, 'w')
    if sys.stderr is None:
        sys.stderr = open(os.devnull, 'w')

    def get_lan_ip():
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            # UDP connect selects the route; it does not send a packet.
            probe.connect(('192.0.2.1', 9))
            return probe.getsockname()[0]

    try:
        LAN_IP = get_lan_ip()
        local_server, tls_server = create_servers(LAN_IP)
    except Exception as error:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('Mirror Mirror could not start securely', str(error))
        root.destroy()
        raise SystemExit(1)

    LAN_URL = app.config['LAN_URL']

    def status_label(item):
        if tray_status['connected']:
            return 'Status: Connected (PC approved)'
        return f"Code: {tray_status['code']}" if tray_status['code'] else 'Status: Idle'

    def address_label(item):
        return f'On iPhone: {LAN_URL}'

    def copy_address(icon, item):
        import pyperclip
        pyperclip.copy(LAN_URL)
        icon.notify(f'Copied {LAN_URL}', 'Mirror Mirror')

    def open_app(icon, item):
        webbrowser.open('http://localhost:5000')

    def stop_sharing(icon, item):
        with _session_lock:
            for code in list(sessions):
                _end_session(code, 'Stopped from the PC tray menu')

    def quit_app(icon, item):
        stop_sharing(icon, item)
        icon.stop()
        local_server.shutdown()
        tls_server.shutdown()
        os._exit(0)

    icon_image = Image.open(io.BytesIO(base64.b64decode(ICON_B64)))
    tray_icon = pystray.Icon('MirrorMirror', icon_image, 'Mirror Mirror hardened', pystray.Menu(
        pystray.MenuItem(status_label, None, enabled=False),
        pystray.MenuItem(address_label, copy_address),
        pystray.MenuItem(f'Version {APP_VERSION}', None, enabled=False),
        pystray.MenuItem('Open Mirror Mirror / iPhone setup', open_app, default=True),
        pystray.MenuItem('Stop sharing now', stop_sharing),
        pystray.MenuItem('Quit', quit_app),
    ))

    def show_welcome():
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showinfo('Mirror Mirror',
            'Mirror Mirror is running in the system tray.\n\n'
            'On the PC: http://localhost:5000\n'
            f'On your iPhone: {LAN_URL}\n\n'
            'Open the PC page for one-time iPhone certificate setup. '
            'Every phone connection requires approval on the PC.')
        root.destroy()

    socketio.start_background_task(_sweep_expired_sessions)
    threading.Thread(target=local_server.serve_forever, daemon=True).start()
    threading.Thread(target=tls_server.serve_forever, daemon=True).start()
    if '--no-welcome' not in sys.argv:
        threading.Thread(target=show_welcome, daemon=True).start()
    tray_icon.run()
