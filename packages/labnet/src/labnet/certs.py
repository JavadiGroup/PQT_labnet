"""Create the private CA and server certificates for a LabNet network.

    labnet-certs init-ca --dir C:\\LabNet-CA
    labnet-certs issue central --ip 192.0.2.10 --dir C:\\LabNet-CA
    labnet-certs issue lab-pc-01 --ip 192.0.2.20 --dir C:\\LabNet-CA
    labnet-certs show C:\\LabNet-CA\\lab-pc-01.crt --ca C:\\LabNet-CA\\labnet-ca.pem

The result matches the minimal model in the deployment guide: one CA that
signs server certificates only. ``labnet-ca.key`` must never leave the
signing computer; servers receive their own ``NAME.crt`` and ``NAME.key``;
everyone else receives only the public ``labnet-ca.pem``.

Requires the ``certs`` extra: ``pip install labnet[certs]``.
"""

from __future__ import annotations

import argparse
import getpass
import ipaddress
import os
import sys
from collections.abc import Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    from cryptography import x509
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
except ImportError as exc:  # pragma: no cover - exercised only without the extra
    raise ImportError(
        "labnet-certs needs the 'certs' extra: pip install \"labnet[certs]\""
    ) from exc


CA_KEY_FILE = "labnet-ca.key"
CA_CERT_FILE = "labnet-ca.pem"
CA_COMMON_NAME = "LabNet Laboratory Root CA"
PASSPHRASE_ENV = "LABNET_CA_PASSPHRASE"

# Back-date slightly so a computer whose clock is a little behind still
# accepts a certificate issued moments ago.
_CLOCK_SKEW = timedelta(minutes=5)


def create_ca(
    directory: Path,
    *,
    passphrase: bytes | None,
    days: int = 3650,
    force: bool = False,
) -> tuple[Path, Path]:
    """Create ``labnet-ca.key`` (optionally encrypted) and ``labnet-ca.pem``."""

    key_path = directory / CA_KEY_FILE
    cert_path = directory / CA_CERT_FILE
    _refuse_overwrite([key_path, cert_path], force)

    key = rsa.generate_private_key(public_exponent=65537, key_size=4096)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME)])
    now = datetime.now(timezone.utc)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            _key_usage(key_cert_sign=True, crl_sign=True),
            critical=True,
        )
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )

    directory.mkdir(parents=True, exist_ok=True)
    _write_private_key(key_path, key, passphrase)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def issue_server_certificate(
    directory: Path,
    name: str,
    *,
    ips: Sequence[str] = (),
    dns_names: Sequence[str] = (),
    passphrase: bytes | None = None,
    days: int = 365,
    force: bool = False,
) -> tuple[Path, Path]:
    """Sign ``NAME.crt`` for a server and write its unencrypted ``NAME.key``.

    Clients check the subject alternative names, so ``ips`` / ``dns_names``
    must contain exactly what they dial (for a Lab PC: the host part of
    ``LABNET_GRPC_ADVERTISED_TARGET``).
    """

    if not ips and not dns_names:
        raise ValueError("Give at least one --ip or --dns the clients will dial.")
    addresses = [ipaddress.ip_address(value) for value in ips]
    key_path = directory / f"{name}.key"
    cert_path = directory / f"{name}.crt"
    _refuse_overwrite([key_path, cert_path], force)

    ca_key = _load_ca_key(directory / CA_KEY_FILE, passphrase)
    ca_cert = x509.load_pem_x509_certificate((directory / CA_CERT_FILE).read_bytes())

    key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    common_name = str(addresses[0]) if addresses else dns_names[0]
    now = datetime.now(timezone.utc)
    alternative_names: list[x509.GeneralName] = [
        x509.IPAddress(address) for address in addresses
    ]
    alternative_names += [x509.DNSName(value) for value in dns_names]
    certificate = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .issuer_name(ca_cert.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - _CLOCK_SKEW)
        .not_valid_after(now + timedelta(days=days))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            _key_usage(digital_signature=True, key_encipherment=True),
            critical=True,
        )
        .add_extension(
            x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
        .add_extension(x509.SubjectAlternativeName(alternative_names), critical=False)
        .add_extension(
            x509.SubjectKeyIdentifier.from_public_key(key.public_key()),
            critical=False,
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # The server reads its key at startup without a prompt; protect it with
    # file permissions on the server instead.
    _write_private_key(key_path, key, None)
    cert_path.write_bytes(certificate.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


def describe_certificate(path: Path, ca_path: Path | None = None) -> tuple[str, bool]:
    """Return a readable summary and whether every check passed."""

    certificate = x509.load_pem_x509_certificate(path.read_bytes())
    now = datetime.now(timezone.utc)
    lines = [
        f"File:      {path}",
        f"Subject:   {certificate.subject.rfc4514_string()}",
        f"Issuer:    {certificate.issuer.rfc4514_string()}",
        f"Valid:     {certificate.not_valid_before_utc:%Y-%m-%d} to "
        f"{certificate.not_valid_after_utc:%Y-%m-%d}",
    ]
    ok = True
    try:
        names = certificate.extensions.get_extension_for_class(
            x509.SubjectAlternativeName
        ).value
        san = [f"IP:{value}" for value in names.get_values_for_type(x509.IPAddress)]
        san += [f"DNS:{value}" for value in names.get_values_for_type(x509.DNSName)]
        lines.append(f"Names:     {', '.join(san)}")
    except x509.ExtensionNotFound:
        is_ca = certificate.extensions.get_extension_for_class(
            x509.BasicConstraints
        ).value.ca
        lines.append("Names:     (none; CA certificate)" if is_ca else "Names:     MISSING")
        ok = ok and is_ca

    if certificate.not_valid_after_utc < now:
        lines.append("Status:    EXPIRED")
        ok = False
    elif certificate.not_valid_after_utc < now + timedelta(days=30):
        lines.append("Status:    expires within 30 days; renew it")

    if ca_path is not None:
        ca_cert = x509.load_pem_x509_certificate(ca_path.read_bytes())
        try:
            certificate.verify_directly_issued_by(ca_cert)
            lines.append(f"Signature: valid, issued by {ca_cert.subject.rfc4514_string()}")
        except (InvalidSignature, ValueError, TypeError) as exc:
            lines.append(f"Signature: NOT issued by {ca_path} ({type(exc).__name__})")
            ok = False
    return "\n".join(lines), ok


def _key_usage(**enabled: bool) -> x509.KeyUsage:
    flags = {
        "digital_signature": False,
        "content_commitment": False,
        "key_encipherment": False,
        "data_encipherment": False,
        "key_agreement": False,
        "key_cert_sign": False,
        "crl_sign": False,
        "encipher_only": False,
        "decipher_only": False,
    }
    flags.update(enabled)
    return x509.KeyUsage(**flags)


def _refuse_overwrite(paths: Sequence[Path], force: bool) -> None:
    existing = [str(path) for path in paths if path.exists()]
    if existing and not force:
        raise FileExistsError(
            f"Refusing to overwrite {', '.join(existing)} (use --force to replace)."
        )


def _write_private_key(
    path: Path,
    key: rsa.RSAPrivateKey,
    passphrase: bytes | None,
) -> None:
    encryption: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            encryption,
        )
    )
    if os.name != "nt":
        path.chmod(0o600)


def _load_ca_key(path: Path, passphrase: bytes | None) -> rsa.RSAPrivateKey:
    data = path.read_bytes()
    try:
        key = serialization.load_pem_private_key(data, password=passphrase)
    except TypeError:
        # Encrypted key and no passphrase yet: ask for it.
        key = serialization.load_pem_private_key(
            data, password=_passphrase_from_user(confirm=False)
        )
    if not isinstance(key, rsa.RSAPrivateKey):
        raise ValueError(f"{path} is not an RSA private key.")
    return key


def _passphrase_from_user(*, confirm: bool) -> bytes:
    if os.getenv(PASSPHRASE_ENV):
        return os.environ[PASSPHRASE_ENV].encode("utf-8")
    first = getpass.getpass("CA key passphrase: ")
    if confirm and getpass.getpass("Repeat passphrase: ") != first:
        raise ValueError("The passphrases did not match.")
    if not first:
        raise ValueError("An empty passphrase is not allowed (use --no-passphrase).")
    return first.encode("utf-8")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="labnet-certs",
        description="Create the LabNet private CA and server certificates.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    init = commands.add_parser("init-ca", help="Create labnet-ca.key and labnet-ca.pem.")
    init.add_argument("--dir", type=Path, required=True)
    init.add_argument("--days", type=int, default=3650)
    init.add_argument(
        "--no-passphrase", action="store_true",
        help="Leave the CA key unencrypted (tests and throwaway networks only).",
    )
    init.add_argument("--force", action="store_true")

    issue = commands.add_parser("issue", help="Sign NAME.crt and write NAME.key.")
    issue.add_argument("name", help="File name stem, e.g. central or lab-pc-01.")
    issue.add_argument("--dir", type=Path, required=True, help="Directory holding the CA.")
    issue.add_argument("--ip", action="append", default=[], help="IP address clients dial.")
    issue.add_argument("--dns", action="append", default=[], help="DNS name clients dial.")
    issue.add_argument("--days", type=int, default=365)
    issue.add_argument("--force", action="store_true")

    show = commands.add_parser("show", help="Summarize a certificate and check it.")
    show.add_argument("file", type=Path)
    show.add_argument("--ca", type=Path, help="Also verify it was issued by this CA.")

    args = parser.parse_args(argv)
    try:
        if args.command == "init-ca":
            passphrase = None if args.no_passphrase else _passphrase_from_user(confirm=True)
            key_path, cert_path = create_ca(
                args.dir, passphrase=passphrase, days=args.days, force=args.force
            )
            print(f"Created {cert_path}  (public: copy to every Lab PC and client)")
            print(f"Created {key_path}  (SECRET: never copy it off this computer)")
        elif args.command == "issue":
            key_path, cert_path = issue_server_certificate(
                args.dir, args.name, ips=args.ip, dns_names=args.dns,
                days=args.days, force=args.force,
            )
            print(f"Created {cert_path}  (public)")
            print(f"Created {key_path}  (SECRET: copy only to the server it names)")
        else:
            summary, ok = describe_certificate(args.file, args.ca)
            print(summary)
            if not ok:
                sys.exit(1)
    except (FileExistsError, FileNotFoundError, ValueError) as exc:
        parser.exit(2, f"labnet-certs: error: {exc}\n")


if __name__ == "__main__":
    main()


__all__ = ["create_ca", "describe_certificate", "issue_server_certificate", "main"]
