import importlib.util
from pathlib import Path
from unittest.mock import Mock
import pytest


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    import sys
    mouse = Mock()
    mouse.FailSafeException = type('FailSafeException', (Exception,), {})
    monkeypatch.setitem(sys.modules, 'pyautogui', mouse)
    spec = importlib.util.spec_from_file_location(
        'mirror_under_test', Path(__file__).resolve().parents[1] / 'webrtc_browser.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    clients = []

    def client(ip='192.168.1.20', host='192.168.1.10:5443', capability=None,
               headers=None, secure=True):
        http = module.app.test_client()
        http.environ_base['REMOTE_ADDR'] = ip
        build_request = http._request_from_builder_args
        def build(args, kwargs):
            return build_request(args, {**kwargs, 'base_url': ('https' if secure else 'http') + '://' + host})
        http._request_from_builder_args = build
        sock = module.socketio.test_client(
            module.app, flask_test_client=http, headers={'Host': host, **(headers or {})},
            auth={'host_capability': capability})
        clients.append(sock)
        return sock

    module.client = client
    module.local_client = lambda: client('127.0.0.1', 'localhost:5000', module._host_capability, secure=False)
    yield module
    for sock in clients:
        if sock.is_connected():
            sock.disconnect()


