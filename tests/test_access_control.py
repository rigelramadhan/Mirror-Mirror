"""Socket.IO security regression tests; desktop automation is always mocked."""
import pytest


def event(client, name):
    return next(item['args'][0] for item in client.get_received() if item['name'] == name)


def request_pair(server):
    host = server.local_client()
    host.emit('host_create_session')
    created = event(host, 'session_created')
    viewer = server.client()
    viewer.emit('viewer_join_session', {'code': created['code']})
    assert any(e['name'] == 'approval_pending' for e in viewer.get_received())
    approval = event(host, 'pairing_request')
    return host, viewer, created, approval


def pair(server, control=True):
    host, viewer, created, approval = request_pair(server)
    host.emit('approve_viewer', {'room': created['code'], 'token': created['token'],
                               'request_id': approval['request_id'], 'allow': True, 'control': control})
    joined = event(viewer, 'session_joined')
    host.get_received()
    return host, viewer, created, joined


@pytest.mark.parametrize('ip,host,secret', [
    ('192.168.1.20', 'localhost:5000', True),
    ('127.0.0.1', 'attacker.example:5000', True),
    ('127.0.0.1', '192.168.1.10:5443', True),
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
        '/', headers={'Host': host}, environ_overrides={'REMOTE_ADDR': ip, 'wsgi.url_scheme': 'https'})
    assert (server._host_capability in response.text) == exposed
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['X-Frame-Options'] == 'DENY'


def test_plain_http_viewer_rejected_even_with_forwarded_headers(server):
    attacker = server.client(secure=False, headers={'X-Forwarded-Proto': 'https'})
    assert not attacker.is_connected()
    response = server.app.test_client().get('/', headers={'Host': '192.168.1.10:5443',
         'X-Forwarded-Proto': 'https'}, environ_overrides={'REMOTE_ADDR': '192.168.1.20'})
    assert response.status_code == 403


def test_original_two_remote_clients_cannot_control_mouse(server):
    attacker = server.client()
    attacker.emit('host_create_session')
    other = server.client()
    other.emit('viewer_join_session', {'code': '123456'})
    other.emit('mouse_click', {'room': '123456', 'token': None})
    server.pyautogui.click.assert_not_called()
    assert not server.sessions


def test_no_credentials_or_control_before_pc_approval(server):
    host, viewer, created, approval = request_pair(server)
    s = server.sessions[created['code']]
    assert not s['claimed'] and s['viewer_sid'] is None
    viewer.emit('mouse_click', {'room': created['code'], 'token': created['token']})
    viewer.emit('approve_viewer', {'room': created['code'], 'token': created['token'],
          'request_id': approval['request_id'], 'allow': True, 'control': True})
    assert not viewer.get_received()
    server.pyautogui.click.assert_not_called()
    assert not s['claimed']


@pytest.mark.parametrize('action,payload,method', [
    ('mouse_move', {'dx': 4, 'dy': 2}, 'moveRel'),
    ('mouse_click', {'button': 'left'}, 'click'),
    ('mouse_scroll', {'dy': 2}, 'scroll'),
])
def test_only_approved_viewer_can_control_live_host(server, action, payload, method):
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
    assert not server.sessions


def test_view_only_approval_cannot_send_mouse_commands(server):
    host, viewer, created, joined = pair(server, control=False)
    assert joined['control'] is False
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    viewer.emit('mouse_move', {'room': joined['code'], 'token': joined['token'], 'dx': 1, 'dy': 1})
    viewer.emit('mouse_scroll', {'room': joined['code'], 'token': joined['token'], 'dy': 1})
    assert not server.pyautogui.method_calls


@pytest.mark.parametrize('allow', [False, None, 'true', 1])
def test_denied_approval_or_non_boolean_allow_never_claims_session(server, allow):
    host, viewer, created, approval = request_pair(server)
    host.emit('approve_viewer', {**approval, 'token': created['token'], 'allow': allow, 'control': True})
    assert event(viewer, 'pairing_error')
    assert not server.sessions[created['code']]['claimed']


def test_old_or_incorrect_approval_request_id_rejected(server):
    host, viewer, created, approval = request_pair(server)
    host.emit('approve_viewer', {**approval, 'token': created['token'], 'request_id': 'wrong', 'allow': True})
    assert not viewer.get_received()
    server.sessions[created['code']]['pending']['created_at'] -= server.APPROVAL_TTL + 1
    host.emit('approve_viewer', {**approval, 'token': created['token'], 'allow': True})
    assert event(viewer, 'pairing_error')
    assert not server.sessions[created['code']]['claimed']


def test_cancel_pending_and_disconnected_viewer_cannot_be_approved(server):
    host, viewer, created, approval = request_pair(server)
    viewer.emit('cancel_pairing')
    assert 'pending' not in server.sessions[created['code']]
    viewer.emit('viewer_join_session', {'code': created['code']})
    host.get_received()
    viewer.disconnect()
    assert 'pending' not in server.sessions[created['code']]
    host.emit('approve_viewer', {**approval, 'token': created['token'], 'allow': True})
    assert not server.sessions[created['code']]['claimed']


def test_approval_attempts_are_rate_limited(server):
    viewer = server.client()
    for _ in range(server.MAX_ATTEMPTS_PER_WINDOW):
        viewer.emit('viewer_join_session', {'code': '000000'})
        viewer.get_received()
    viewer.emit('viewer_join_session', {'code': '000000'})
    assert 'Too many' in event(viewer, 'pairing_error')['message']


@pytest.mark.parametrize('role', ['host', 'viewer', 'invalid'])
def test_all_rejoins_require_fresh_pairing(server, role):
    host, viewer, created, joined = pair(server)
    viewer.emit('rejoin_session', {**joined, 'role': role})
    assert event(viewer, 'pairing_error')


def test_old_remembered_credentials_disabled(server):
    host, viewer, created, joined = pair(server)
    assert 'device_token' not in joined
    assert not hasattr(server, 'trusted_devices')
    viewer.emit('quick_reconnect', {'device_token': 'legacy-token'})
    assert any(e['name'] == 'quick_reconnect_unavailable' for e in viewer.get_received())


def test_signaling_only_goes_to_peer_without_credentials(server):
    host, viewer, created, joined = pair(server)
    bystander = server.client()
    host.emit('offer', {'room': created['code'], 'token': created['token'], 'sdp': 'test-offer', 'type': 'offer'})
    assert event(viewer, 'offer') == {'sdp': 'test-offer', 'type': 'offer'}
    viewer.emit('answer', {'room': joined['code'], 'token': joined['token'], 'sdp': 'test-answer', 'type': 'answer'})
    assert event(host, 'answer') == {'sdp': 'test-answer', 'type': 'answer'}
    for sender, receiver, credentials in [(host, viewer, created), (viewer, host, joined)]:
        sender.emit('ice_candidate', {'room': credentials['code'], 'token': credentials['token'],
                                      'candidate': {'candidate': 'test'}})
        assert event(receiver, 'ice_candidate') == {'candidate': {'candidate': 'test'}}
    assert not bystander.get_received()


def test_offer_before_approval_is_not_broadcast(server):
    host, viewer, created, approval = request_pair(server)
    host.emit('offer', {'room': created['code'], 'token': created['token'], 'sdp': 'private', 'type': 'offer'})
    assert not viewer.get_received()


def test_refresh_revokes_old_session_and_approval(server):
    host, viewer, created, joined = pair(server)
    host.emit('refresh_code', {'room': created['code'], 'token': created['token']})
    refreshed = event(host, 'session_created')
    assert refreshed['code'] != created['code']
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()


@pytest.mark.parametrize('field,age', [('approved_at', 3601), ('last_activity', 901)])
def test_absolute_and_idle_expiry_checked_before_commands(server, field, age):
    host, viewer, created, joined = pair(server)
    server.sessions[created['code']][field] -= age
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()
    assert not server.sessions


def test_sweeper_expires_unclaimed_and_pending_sessions(server):
    host, viewer, created, approval = request_pair(server)
    server.sessions[created['code']]['pending']['created_at'] -= server.APPROVAL_TTL + 1
    server._expire_sessions()
    assert 'pending' not in server.sessions[created['code']]
    server.sessions[created['code']]['created_at'] -= server.PAIRING_TTL + 1
    server._expire_sessions()
    assert not server.sessions


@pytest.mark.parametrize('payload', [None, [], 'bad', {'room': []}, {'room': {}}, {'room': '000000', 'token': []}])
def test_malformed_payloads_do_not_reach_mouse(server, payload):
    viewer = server.client()
    for action in ('mouse_move', 'mouse_click', 'mouse_scroll', 'viewer_join_session', 'approve_viewer'):
        viewer.emit(action, payload)
    assert not server.pyautogui.method_calls


@pytest.mark.parametrize('value', ['1', True, float('inf'), float('nan'), 1001, 10**90])
def test_invalid_mouse_numbers_rejected(server, value):
    host, viewer, created, joined = pair(server)
    # Huge JSON integers are valid Python input but outside the permitted range.
    viewer.emit('mouse_move', {'room': joined['code'], 'token': joined['token'], 'dx': value, 'dy': 0})
    server.pyautogui.moveRel.assert_not_called()


def test_mouse_corner_failsafe_revokes_session(server):
    host, viewer, created, joined = pair(server)
    assert server.pyautogui.FAILSAFE is True
    server.pyautogui.click.side_effect = server.pyautogui.FailSafeException()
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    assert not server.sessions


def test_host_or_viewer_leaving_revokes_control(server):
    host, viewer, created, joined = pair(server)
    viewer.emit('leave_session', {'room': joined['code'], 'token': joined['token'], 'role': 'viewer'})
    viewer.emit('mouse_click', {'room': joined['code'], 'token': joined['token']})
    server.pyautogui.click.assert_not_called()
    assert not server.sessions


def test_local_bundle_no_cdn_stun_or_remembered_device_code(server):
    page = server.app.test_client().get('/', base_url='http://localhost:5000').text
    assert '/static/vendor/socket.io.min.js' in page
    assert 'cdnjs.cloudflare.com' not in page and 'stun.l.google' not in page
    assert 'quick_reconnect' not in page
    bundle = server.app.test_client().get('/static/vendor/socket.io.min.js', base_url='http://localhost:5000')
    assert bundle.status_code == 200


def test_profile_export_is_local_only(server):
    server.app.config['TLS_IDENTITY'] = {'profile': b'public-profile-only'}
    http = server.app.test_client()
    assert http.get('/setup/iphone-profile', base_url='http://localhost:5000').data == b'public-profile-only'
    response = http.get('/setup/iphone-profile', base_url='https://192.168.1.10:5443',
                        environ_overrides={'REMOTE_ADDR': '192.168.1.20'})
    assert response.status_code == 403
