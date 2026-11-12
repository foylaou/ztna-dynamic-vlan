"""產生 Phase 1 POC 用的測試憑證（僅供測試，勿用於正式環境）。

用法：
    python phase1_poc/make_certs.py

輸出到 phase1_poc/certs/（已被 .gitignore 排除）：
    ca.pem / ca.key            測試 Root CA
    server.pem                 RADIUS 伺服器憑證 + 私鑰
    client-vlan11.pem / .key   OU=VLAN11 的用戶端憑證
    client-vlan20.pem / .key   OU=VLAN20 的用戶端憑證
    client-noou.pem / .key     受信任 CA 簽發、但沒有 VLAN OU
    client-rogue.pem / .key    由另一個不受信任的 CA 簽發
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

OUT = Path(__file__).parent / "certs"
DOMAIN = "example.com"
NOW = dt.datetime.now(dt.timezone.utc)


def name(cn: str, ou: str | None = None) -> x509.Name:
    attrs = [x509.NameAttribute(NameOID.COMMON_NAME, cn)]
    if ou:
        attrs.append(x509.NameAttribute(NameOID.ORGANIZATIONAL_UNIT_NAME, ou))
    return x509.Name(attrs)


def key_pem(key: ec.EllipticCurvePrivateKey) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def cert_pem(cert: x509.Certificate) -> bytes:
    return cert.public_bytes(serialization.Encoding.PEM)


def make_ca(cn: str):
    key = ec.generate_private_key(ec.SECP256R1())
    subject = name(cn)
    cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(minutes=5))
        .not_valid_after(NOW + dt.timedelta(days=365))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True, key_cert_sign=True, crl_sign=True,
                content_commitment=False, key_encipherment=False,
                data_encipherment=False, key_agreement=False,
                encipher_only=False, decipher_only=False,
            ),
            critical=True,
        )
        .sign(key, hashes.SHA256())
    )
    return key, cert


def make_leaf(ca_key, ca_cert, subject: x509.Name, usage, dns: str | None = None):
    key = ec.generate_private_key(ec.SECP256R1())
    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(NOW - dt.timedelta(minutes=5))
        .not_valid_after(NOW + dt.timedelta(days=90))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
    )
    if dns:
        builder = builder.add_extension(
            x509.SubjectAlternativeName([x509.DNSName(dns)]), critical=False
        )
    return key, builder.sign(ca_key, hashes.SHA256())


def write_client(stem: str, ca_key, ca_cert, cn: str, ou: str | None) -> None:
    key, cert = make_leaf(ca_key, ca_cert, name(cn, ou), ExtendedKeyUsageOID.CLIENT_AUTH)
    (OUT / f"{stem}.pem").write_bytes(cert_pem(cert))
    (OUT / f"{stem}.key").write_bytes(key_pem(key))
    print(f"  {stem}.pem  {cert.subject.rfc4514_string()}")


def main() -> None:
    OUT.mkdir(exist_ok=True)
    ca_key, ca_cert = make_ca("ZTNA POC Test Root CA")
    (OUT / "ca.pem").write_bytes(cert_pem(ca_cert))
    (OUT / "ca.key").write_bytes(key_pem(ca_key))

    srv_key, srv_cert = make_leaf(
        ca_key, ca_cert, name("radius.poc.test"),
        ExtendedKeyUsageOID.SERVER_AUTH, dns="radius.poc.test",
    )
    (OUT / "server.pem").write_bytes(cert_pem(srv_cert) + key_pem(srv_key))

    print(f"輸出目錄：{OUT}")
    write_client("client-vlan11", ca_key, ca_cert, f"alice@{DOMAIN}", "VLAN11")
    write_client("client-vlan20", ca_key, ca_cert, f"bob@{DOMAIN}", "VLAN20")
    write_client("client-noou", ca_key, ca_cert, f"carol@{DOMAIN}", None)

    rogue_key, rogue_cert = make_ca("Untrusted Rogue CA")
    write_client("client-rogue", rogue_key, rogue_cert, f"mallory@{DOMAIN}", "VLAN11")


if __name__ == "__main__":
    main()
