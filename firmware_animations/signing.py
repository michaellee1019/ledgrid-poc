"""Deterministic ECDSA P-256 signatures.

The small ``ecdsa`` dependency is loaded lazily so package listing and frame
conversion still work in minimal controller environments. Signing and strict
verification fail closed with an actionable dependency error.
"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import Any

from .errors import PackageValidationError, SignatureDependencyError


def _ecdsa() -> Any:
    try:
        import ecdsa
    except ImportError as exc:
        raise SignatureDependencyError(
            "ECDSA support requires the optional 'ecdsa>=0.19.0' dependency"
        ) from exc
    return ecdsa


def generate_keypair(private_path: str | Path, public_path: str | Path) -> str:
    ecdsa = _ecdsa()
    signing_key = ecdsa.SigningKey.generate(curve=ecdsa.NIST256p)
    private = Path(private_path)
    public = Path(public_path)
    private_bytes = signing_key.to_pem(format="pkcs8")
    public_bytes = signing_key.verifying_key.to_pem()
    private_created = False
    public_created = False
    try:
        private_fd = os.open(private, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        private_created = True
        with os.fdopen(private_fd, "wb") as private_file:
            private_file.write(private_bytes)

        public_fd = os.open(public, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        public_created = True
        with os.fdopen(public_fd, "wb") as public_file:
            public_file.write(public_bytes)
    except OSError as exc:
        # Only reclaim files this invocation created. Existing paths are never
        # truncated, and the private key exists with 0600 from its first byte.
        if public_created:
            public.unlink(missing_ok=True)
        if private_created:
            private.unlink(missing_ok=True)
        raise PackageValidationError(f"cannot create signing keypair: {exc}") from exc
    return public_key_id(public_bytes)


def public_key_id(public_key: bytes | str | Path) -> str:
    verifying_key = load_verifying_key(public_key)
    fingerprint = hashlib.sha256(verifying_key.to_der()).hexdigest()
    return f"key-{fingerprint[:16]}"


def _read_key(value: bytes | str | Path) -> bytes:
    if isinstance(value, bytes):
        return value
    path = Path(value)
    try:
        return path.read_bytes()
    except OSError as exc:
        raise PackageValidationError(f"cannot read signing key {path}: {exc}") from exc


def load_signing_key(value: bytes | str | Path) -> Any:
    ecdsa = _ecdsa()
    try:
        key = ecdsa.SigningKey.from_pem(_read_key(value))
    except (ValueError, TypeError) as exc:
        raise PackageValidationError("invalid ECDSA private key") from exc
    if key.curve != ecdsa.NIST256p:
        raise PackageValidationError("signing key must use ECDSA P-256")
    return key


def load_verifying_key(value: bytes | str | Path) -> Any:
    ecdsa = _ecdsa()
    try:
        key = ecdsa.VerifyingKey.from_pem(_read_key(value))
    except (ValueError, TypeError) as exc:
        raise PackageValidationError("invalid ECDSA public key") from exc
    if key.curve != ecdsa.NIST256p:
        raise PackageValidationError("verification key must use ECDSA P-256")
    return key


def sign(data: bytes, private_key: bytes | str | Path) -> bytes:
    ecdsa = _ecdsa()
    key = load_signing_key(private_key)
    digest = hashlib.sha256(data).digest()
    return key.sign_digest_deterministic(
        digest,
        hashfunc=hashlib.sha256,
        sigencode=ecdsa.util.sigencode_string_canonize,
    )


def verify(data: bytes, signature: bytes, public_key: bytes | str | Path) -> None:
    ecdsa = _ecdsa()
    if len(signature) != 64:
        raise PackageValidationError("P-256 signature must be 64 bytes")
    key = load_verifying_key(public_key)
    try:
        _r, scalar_s = ecdsa.util.sigdecode_string(signature, key.curve.order)
        if scalar_s > key.curve.order // 2:
            raise PackageValidationError("package signature is not canonical low-S P-256")
        valid = key.verify_digest(
            signature,
            hashlib.sha256(data).digest(),
            sigdecode=ecdsa.util.sigdecode_string,
        )
    except PackageValidationError:
        raise
    except (ecdsa.BadSignatureError, ValueError) as exc:
        raise PackageValidationError("package signature verification failed") from exc
    if not valid:
        raise PackageValidationError("package signature verification failed")
