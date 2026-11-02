"""Real listeners and encrypted Socket.IO; no screen capture or desktop input."""
from queue import Queue
import threading

import pytest
import requests
import socketio


@pytest.mark.parametrize('transport', ['polling', 'websocket'])
def test_encrypted_viewer_waits_for_real_local_host_approval(server, monkeypatch, transport):
    local, secure = server.create_servers('192.168.1.10', local_port=0, secure_port=0)
    assert local.server_address[0] == '127.0.0.1'
    monkeypatch.setattr(server, '_LOCAL_HOSTS', {f'127.0.0.1:{local.server_port}'})
    threads = [threading.Thread(target=listener.serve_forever, daemon=True) for listener in (local, secure)]
    for thread in threads:
        thread.start()
    host_http, viewer_http = requests.Session(), requests.Session()
    host_http.trust_env = viewer_http.trust_env = False
    viewer_http.verify = server.app.config['TLS_IDENTITY']['ca_file']
    host = socketio.Client(http_session=host_http, reconnection=False, request_timeout=3)
    viewer = socketio.Client(http_session=viewer_http, reconnection=False, request_timeout=3,
                            websocket_extra_options={'http_no_proxy': ['127.0.0.1']})
    created, approval, pending, joined, ended = [Queue() for _ in range(5)]
    host.on('session_created', created.put)
    host.on('pairing_request', approval.put)
    viewer.on('approval_pending', lambda: pending.put(True))
    viewer.on('session_joined', joined.put)
    viewer.on('peer_left', ended.put)
    local_url, secure_url = f'http://127.0.0.1:{local.server_port}', f'https://127.0.0.1:{secure.server_port}'
    try:
        page = host_http.get(local_url, timeout=3)
        assert server._host_capability in page.text
        phone_page = viewer_http.get(secure_url, timeout=3)
        assert phone_page.status_code == 200 and 'const hostCapability = null;' in phone_page.text
        assert server._host_capability not in phone_page.text
        assert viewer_http.get(secure_url + '/socket.io/?EIO=4&transport=polling',
            headers={'Origin': 'https://attacker.example'}, timeout=3).status_code == 400
        assert viewer_http.get(secure_url + '/setup/iphone-profile', timeout=3).status_code == 403
        profile = host_http.get(local_url + '/setup/iphone-profile', timeout=3)
        assert profile.status_code == 200 and b'com.apple.security.root' in profile.content
        host.connect(local_url, auth={'host_capability': server._host_capability}, transports=['polling'])
        viewer.connect(secure_url, transports=[transport])
        assert viewer.transport() == transport
        host.call('host_create_session', timeout=5)
        credentials = created.get(timeout=5)
        viewer.call('viewer_join_session', {'code': credentials['code']}, timeout=5)
        assert pending.get(timeout=5) is True
        request = approval.get(timeout=5)
        assert joined.empty()
        host.call('approve_viewer', {**request, 'token': credentials['token'], 'allow': True, 'control': False}, timeout=5)
        approved = joined.get(timeout=5)
        assert approved['control'] is False and approved['token'] != credentials['token']
        host.call('leave_session', {'room': credentials['code'], 'token': credentials['token'], 'role': 'host'}, timeout=5)
        assert 'message' in ended.get(timeout=5)
        assert not server.sessions
        assert not server.pyautogui.method_calls
    finally:
        host.disconnect()
        viewer.disconnect()
        for listener in (local, secure):
            listener.shutdown()
            listener.server_close()
        for thread in threads:
            thread.join(timeout=3)
