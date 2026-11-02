"""Per-installation LAN TLS identity. Never distribute the generated private keys."""
import csv
from datetime import datetime, timedelta, timezone
from ipaddress import ip_address, ip_network
import os
from pathlib import Path
import plistlib
import ssl
import subprocess
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def protect_directory(directory):
    """Keep private keys accessible only to this Windows user and SYSTEM."""
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == 'nt':
        sid = next(csv.reader(subprocess.check_output(
            ['whoami', '/user', '/fo', 'csv', '/nh'], text=True,
            creationflags=subprocess.CREATE_NO_WINDOW).splitlines()))[1]
        if not sid.startswith('S-1-'):
            raise RuntimeError('Unable to identify the current Windows user')
        subprocess.run(['icacls', str(directory), '/inheritance:r', '/grant:r',
                        f'*{sid}:(OI)(CI)F', '*S-1-5-18:(OI)(CI)F'],
                       check=True, capture_output=True,
                       creationflags=subprocess.CREATE_NO_WINDOW)
    else:
        directory.chmod(0o700)


def _write_key(path, key):
    content = key.private_bytes(serialization.Encoding.PEM,
                                serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption())
    # The containing directory is protected before writing any secret.
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'wb') as file:
        file.write(content)


def prepare_tls(data_dir, lan_ip):
    address = ip_address(lan_ip)
    if not address.is_private or address.is_unspecified or address.is_multicast:
        raise ValueError('Mirror Mirror requires a private LAN address')
    directory = Path(data_dir) / 'tls'
    protect_directory(directory)
    now = datetime.now(timezone.utc)
    ca_file, ca_key_file = directory / 'ca.pem', directory / 'ca-key.pem'
    if ca_file.exists() or ca_key_file.exists():
        # Fail closed on damaged or incomplete identities; do not silently replace
        # a root that a user may already have trusted on their phone.
        ca = x509.load_pem_x509_certificate(ca_file.read_bytes())
        ca_key = serialization.load_pem_private_key(ca_key_file.read_bytes(), password=None)
        if (ca.not_valid_after_utc <= now or not ca.extensions.get_extension_for_class(
                x509.BasicConstraints).value.ca
                or ca.public_key().public_bytes(serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo) !=
                ca_key.public_key().public_bytes(serialization.Encoding.DER,
                    serialization.PublicFormat.SubjectPublicKeyInfo)):
            raise ValueError('TLS identity invalid or expired; remove its iPhone trust before resetting it')
    else:
        ca_key = ec.generate_private_key(ec.SECP256R1())
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,
                        'Mirror Mirror Local ' + uuid.uuid4().hex[:8])])
        ca = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
              .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(minutes=5))
              .not_valid_after(now + timedelta(days=1825))
              .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                  key_encipherment=False, data_encipherment=False, key_agreement=False,
                  key_cert_sign=True, crl_sign=True, encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
              .add_extension(x509.NameConstraints(permitted_subtrees=[x509.DNSName('localhost')] + [
                  x509.IPAddress(ip_network(network)) for network in
                  ['10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16', '127.0.0.0/8',
                   '::1/128', 'fc00::/7', 'fe80::/10']], excluded_subtrees=None), critical=True)
              .sign(ca_key, hashes.SHA256()))
        _write_key(ca_key_file, ca_key)
        ca_file.write_bytes(ca.public_bytes(serialization.Encoding.PEM))

    # Renew the server identity each launch, preserving the already trusted root.
    key = ec.generate_private_key(ec.SECP256R1())
    leaf = (x509.CertificateBuilder().subject_name(x509.Name([
                x509.NameAttribute(NameOID.COMMON_NAME, 'Mirror Mirror LAN')]))
            .issuer_name(ca.subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5))
            .not_valid_after(min(now + timedelta(days=90), ca.not_valid_after_utc))
            .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                key_encipherment=False, data_encipherment=False, key_agreement=False,
                key_cert_sign=False, crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')] + [
                x509.IPAddress(ip_address(ip)) for ip in sorted({str(address), '127.0.0.1', '::1'})]), critical=False)
            .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
            .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
            .sign(ca_key, hashes.SHA256()))
    cert_file, key_file = directory / 'server.pem', directory / 'server-key.pem'
    cert_file.write_bytes(leaf.public_bytes(serialization.Encoding.PEM))
    _write_key(key_file, key)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(cert_file, key_file)
    fingerprint = ca.fingerprint(hashes.SHA256()).hex().upper()
    profile = plistlib.dumps({
        'PayloadType': 'Configuration', 'PayloadVersion': 1,
        'PayloadIdentifier': 'local.mirrormirror.' + fingerprint[:16],
        'PayloadUUID': str(uuid.uuid4()), 'PayloadDisplayName': ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
        'PayloadDescription': 'Local Mirror Mirror certificate only. No MDM, VPN, or device management.',
        'PayloadContent': [{
            'PayloadType': 'com.apple.security.root', 'PayloadVersion': 1,
            'PayloadIdentifier': 'local.mirrormirror.root.' + fingerprint[:16],
            'PayloadUUID': str(uuid.uuid4()), 'PayloadDisplayName': 'Mirror Mirror local CA',
            'PayloadContent': ca.public_bytes(serialization.Encoding.DER),
        }],
    })
    return {'context': context, 'profile': profile, 'fingerprint': fingerprint,
            'name': ca.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value,
            'ca_file': str(ca_file)}
