"""Socket.IO regression tests. Never capture a screen or move the real mouse."""
import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


@pytest.fixture
def server(monkeypatch, tmp_path):
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path))
    # Import a fresh instance, with no real desktop automation dependency.
    import sys
    monkeypatch.setitem(sys.modules, 'pyautogui', Mock())
    spec = importlib.util.spec_from_file_location(
        'mirror_under_test', Path(__file__).resolve().parents[1] / 'webrtc_browser.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.app.config['TESTING'] = True
    clients = []

    def client(ip='192.168.1.20', host='192.168.1.10:5000', capability=None, headers=None):
        http = module.app.test_client()
        http.environ_base['REMOTE_ADDR'] = ip
        sock = module.socketio.test_client(
            module.app, flask_test_client=http,
            headers={'Host': host, **(headers or {})},
            auth={'host_capability': capability})
        clients.append(sock)
        return sock

    module.client = client
    module.local_client = lambda: client('127.0.0.1', 'localhost:5000', module._host_capability)
    yield module
    for sock in clients:
        if sock.is_connected():
            sock.disconnect()


def event(client, name):
    return next(item['args'][0] for item in client.get_received() if item['name'] == name)


def pair(server):
    host = server.local_client()
    host.emit('host_create_session')
    created = event(host, 'session_created')
    viewer = server.client()
    viewer.emit('viewer_join_session', {'code': created['code']})
    joined = event(viewer, 'session_joined')
    host.get_received()
    return host, viewer, created, joined


@pytest.mark.parametrize('ip,host,secret', [
    ('192.168.1.20', 'localhost:5000', True),
    ('127.0.0.1', 'attacker.example:5000', True),
    ('127.0.0.1', '192.168.1.10:5000', True),
    ('127.0.0.1', 'localhost:5000', False),
])
def test_unprivileged_client_cannot_create_host(server, ip, host, secret):
    attacker = server.client(ip, host, server._host_capability if secret else None,
                             {'X-Forwarded-For': '127.0.0.1'})
    attacker.emit('host_create_session')
    assert not server.sessions
    assert event(attacker, 'pairing_error')


@pytest.mark.parametrize('ip,host,exposed', [
    ('127.0.0.1', 'localhost:5000', True),
    ('::1', '[::1]:5000', True),
    ('192.168.1.20', 'localhost:5000', False),
    ('127.0.0.1', 'attacker.example:5000', False),
])
def test_capability_only_in_literal_localhost_page(server, ip, host, exposed):
    response = server.app.test_client().get(
        '/', headers={'Host': host}, environ_overrides={'REMOTE_ADDR': ip})
    assert (server._host_capability in response.text) == exposed
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'


def test_original_two_remote_clients_cannot_control_mouse(server):
    attacker = server.client()
    attacker.emit('host_create_session')
    other = server.client()
    other.emit('viewer_join_session', {'code': '123456'})
    other.emit('mouse_click', {'room': '123456', 'token': None})
    server.pyautogui.click.assert_not_called()
    assert not server.sessions


@pytest.mark.parametrize('action,payload,method', [
    ('mouse_move', {'dx': 4, 'dy': 2}, 'moveRel'),
    ('mouse_click', {'button': 'left'}, 'click'),
    ('mouse_scroll', {'dy': 2}, 'scroll'),
])
def test_only_paired_viewer_can_control_live_host(server, action, payload, method):
    host, viewer, created, joined = pair(server)
    assert created['token'] != joined['token']
    attacker = server.client()
    command = {'room': joined['code'], 'token': joined['token'], **payload}
    attacker.emit(action, command)
    host.emit(action, {'room': created['code'], 'token': created['token'], **payload})
    getattr(server.pyautogui, method).assert_not_called()
    viewer.emit(action, command)
    getattr(server.pyautogui, method).assert_called_once()
    getattr(server.pyautogui, method).reset_mock()
    host.disconnect()
    viewer.emit(action, command)
    getattr(server.pyautogui, method).assert_not_called()


def test_viewer_cannot_escalate_to_host_even_after_host_disconnect(server):
    host, viewer, created, joined = pair(server)
    host.disconnect()
    viewer.emit('rejoin_session', {**joined, 'role': 'host'})
    assert event(viewer, 'pairing_error')
    assert server.sessions[created['code']]['host_sid'] is None
    viewer.emit('refresh_code', {'room': joined['code'], 'token': joined['token']})
    assert list(server.sessions) == [created['code']]


def test_remote_client_cannot_use_even_leaked_host_token(server):
    host, viewer, created, joined = pair(server)
    host.disconnect()
    viewer.emit('rejoin_session', {**created, 'role': 'host'})
    assert event(viewer, 'pairing_error')


def test_local_socket_cannot_resume_host_with_viewer_token(server):
    host, viewer, created, joined = pair(server)
    host.disconnect()
    other_local_host = server.local_client()
    other_local_host.emit('rejoin_session', {**joined, 'role': 'host'})
    assert event(other_local_host, 'pairing_error')
    assert server.sessions[created['code']]['host_sid'] is None


def test_ipv6_loopback_can_create_host(server):
    host = server.client('::1', '[::1]:5000', server._host_capability)
    host.emit('host_create_session')
    assert event(host, 'session_created')['code'] in server.sessions


def test_viewer_leaving_revokes_control(server):
    host, viewer, created, joined = pair(server)
    viewer.emit('leave_session', {'room': joined['code'], 'token': joined['token'], 'role': 'viewer'})
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()


def test_local_host_and_viewer_can_rejoin(server):
    host, viewer, created, joined = pair(server)
    host.disconnect()
    host = server.local_client()
    host.emit('rejoin_session', {**created, 'role': 'host'})
    assert event(host, 'rejoined')['code'] == created['code']
    viewer.disconnect()
    viewer = server.client()
    viewer.emit('rejoin_session', {**joined, 'role': 'viewer'})
    assert event(viewer, 'rejoined')['code'] == created['code']
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_called_once()


def test_signaling_only_goes_to_peer_without_credentials(server):
    host, viewer, created, joined = pair(server)
    bystander = server.client()
    host.emit('offer', {'room': created['code'], 'token': created['token'],
                        'sdp': 'test-offer', 'type': 'offer'})
    assert event(viewer, 'offer') == {'sdp': 'test-offer', 'type': 'offer'}
    viewer.emit('answer', {'room': joined['code'], 'token': joined['token'],
                           'sdp': 'test-answer', 'type': 'answer'})
    assert event(host, 'answer') == {'sdp': 'test-answer', 'type': 'answer'}
    for sender, receiver, credentials in [(host, viewer, created), (viewer, host, joined)]:
        sender.emit('ice_candidate', {'room': credentials['code'], 'token': credentials['token'],
                                      'candidate': {'candidate': 'test'}})
        assert event(receiver, 'ice_candidate') == {'candidate': {'candidate': 'test'}}
    assert not bystander.get_received()


def test_offer_without_viewer_is_not_broadcast(server):
    host = server.local_client()
    host.emit('host_create_session')
    created = event(host, 'session_created')
    bystander = server.client()
    host.emit('offer', {'room': created['code'], 'token': created['token'],
                        'sdp': 'private', 'type': 'offer'})
    assert not bystander.get_received()


def test_refresh_revokes_old_session(server):
    host, viewer, created, joined = pair(server)
    host.emit('refresh_code', {'room': created['code'], 'token': created['token']})
    refreshed = event(host, 'session_created')
    assert refreshed['code'] != created['code']
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()
    viewer.emit('rejoin_session', {**joined, 'role': 'viewer'})
    assert event(viewer, 'pairing_error')


def test_remembered_device_reconnect_rotates_viewer_token(server):
    host, viewer, created, joined = pair(server)
    viewer.disconnect()
    viewer = server.client()
    viewer.emit('quick_reconnect', {'device_token': joined['device_token']})
    rejoined = event(viewer, 'session_joined')
    assert rejoined['token'] != joined['token']
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()
    viewer.emit('mouse_click', {'room': rejoined['code'], 'token': rejoined['token']})
    server.pyautogui.click.assert_called_once()


def test_remembered_device_cannot_replace_connected_viewer(server):
    host, viewer, created, joined = pair(server)
    other = server.client()
    other.emit('quick_reconnect', {'device_token': joined['device_token']})
    assert any(e['name'] == 'quick_reconnect_unavailable' for e in other.get_received())
    other.emit('rejoin_session', {**joined, 'role': 'viewer'})
    assert event(other, 'pairing_error')


def test_remembered_device_cannot_claim_expired_code(server):
    host = server.local_client()
    host.emit('host_create_session')
    created = event(host, 'session_created')
    server.sessions[created['code']]['created_at'] -= server.PAIRING_TTL + 1
    server.trusted_devices['saved-token'] = {}
    viewer = server.client()
    viewer.emit('quick_reconnect', {'device_token': 'saved-token'})
    assert any(e['name'] == 'quick_reconnect_unavailable' for e in viewer.get_received())
    assert not server.sessions


def test_host_stopping_session_revokes_mouse_control(server):
    host, viewer, created, joined = pair(server)
    host.emit('leave_session', {'room': created['code'], 'token': created['token'], 'role': 'host'})
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()
    assert not server.sessions
