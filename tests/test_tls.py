"""Certificate issuance and real TLS handshakes, with no system trust changes."""
from datetime import timedelta
from ipaddress import ip_address
from pathlib import Path
import plistlib
import socket
import ssl
import threading

from cryptography import x509
from cryptography.hazmat.primitives import serialization
import pytest

from tls_setup import prepare_tls


def test_tls_identity_persists_root_and_rotates_leaf(tmp_path):
    first = prepare_tls(tmp_path, '192.168.1.10')
    old_leaf = (tmp_path / 'tls/server.pem').read_bytes()
    second = prepare_tls(tmp_path, '192.168.1.11')
    assert first['fingerprint'] == second['fingerprint']
    assert old_leaf != (tmp_path / 'tls/server.pem').read_bytes()
    leaf = x509.load_pem_x509_certificate((tmp_path / 'tls/server.pem').read_bytes())
    assert ip_address('192.168.1.11') in leaf.extensions.get_extension_for_class(
        x509.SubjectAlternativeName).value.get_values_for_type(x509.IPAddress)
    assert leaf.not_valid_after_utc - leaf.not_valid_before_utc <= timedelta(days=91)
    ca = x509.load_pem_x509_certificate(Path(first['ca_file']).read_bytes())
    assert ca.extensions.get_extension_for_class(x509.BasicConstraints).value.path_length == 0
    assert ca.extensions.get_extension_for_class(x509.NameConstraints).critical


def test_profile_contains_only_public_root_certificate(tmp_path):
    identity = prepare_tls(tmp_path, '192.168.1.10')
    profile = plistlib.loads(identity['profile'])
    assert len(profile['PayloadContent']) == 1
    payload = profile['PayloadContent'][0]
    assert payload['PayloadType'] == 'com.apple.security.root'
    root = x509.load_der_x509_certificate(payload['PayloadContent'])
    assert root.public_bytes(serialization.Encoding.PEM) == Path(identity['ca_file']).read_bytes()
    assert 'PRIVATE KEY' not in str(profile)
    assert b'VPN' not in payload['PayloadContent']


def test_corrupted_existing_identity_fails_closed(tmp_path):
    prepare_tls(tmp_path, '192.168.1.10')
    (tmp_path / 'tls/ca-key.pem').write_bytes(b'broken')
    with pytest.raises(ValueError):
        prepare_tls(tmp_path, '192.168.1.10')


@pytest.mark.parametrize('address', ['8.8.8.8', '0.0.0.0', '224.0.0.1'])
def test_public_or_unspecified_lan_identity_rejected(tmp_path, address):
    with pytest.raises(ValueError):
        prepare_tls(tmp_path, address)


def test_real_tls_handshake_validates_chain_and_hostname(tmp_path):
    identity = prepare_tls(tmp_path, '192.168.1.10')
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    listener.listen()
    listener.settimeout(5)
    errors = []

    def serve():
        try:
            for _ in range(2):
                conn, _ = listener.accept()
                try:
                    with identity['context'].wrap_socket(conn, server_side=True) as tls:
                        tls.sendall(b'verified')
                except ssl.SSLError:
                    conn.close()  # Expected when the client rejects a wrong hostname.
        except Exception as error:
            errors.append(error)

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    context = ssl.create_default_context(cafile=identity['ca_file'])
    with socket.create_connection(listener.getsockname(), timeout=5) as conn:
        with context.wrap_socket(conn, server_hostname='localhost') as tls:
            assert tls.recv(8) == b'verified'
            assert tls.version() in ('TLSv1.2', 'TLSv1.3')
    with socket.create_connection(listener.getsockname(), timeout=5) as conn:
        with pytest.raises(ssl.SSLCertVerificationError):
            context.wrap_socket(conn, server_hostname='attacker.example')
    worker.join(timeout=5)
    listener.close()
    assert not worker.is_alive() and not errors
