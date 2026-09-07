"""Passphrase-based encryption for `.minisbak` packages.

Ported from: src/android/app/src/main/java/com/openminis/app/backup/BackupCrypto.kt
Original package: com.openminis.app.backup

docs/backup-restore-design.md §5, scheme `minisbak-enc/1`.

This is the Android column of the §5.2.1 parameter table, implemented against
`src/ios/Agent/Backup/BackupCrypto.swift`. Every value here is wire format —
a deviation makes packages mutually unreadable:

  - KDF: PBKDF2-HMAC-SHA256, 600 000 iterations, 16-byte salt, 256-bit KEK.
  - Subkeys: HKDF-SHA256 (empty salt) with infos `minisbak/{data,secrets,mac,verify}`.
  - Cipher: AES-256-GCM, 12-byte random nonce, 128-bit tag, wire layout per
    segment = `UInt32 BE length ‖ nonce ‖ ciphertext ‖ tag`.
  - Segments: 4 MiB plaintext each, AAD = UTF-8 `"<path>#<segment>"`.
  - Member magic: ASCII `MBK1`.

Why PBKDF2 and not Argon2id: neither platform ships Argon2 in its standard
library, and §5.2.1 rules that dual-platform standard-library availability
beats single-platform optimality. `kdf.alg` keeps both decodable by
construction; an unknown alg is refused loudly, never silently substituted.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .backup_format import BackupManifest

__all__ = [
    "BackupCrypto",
    "CryptoException",
    "WrongPassphraseException",
    "ManifestTamperedException",
    "CorruptMemberException",
    "UnsupportedKDFException",
]


class CryptoException(Exception):
    """Generic crypto failure."""


class WrongPassphraseException(Exception):
    def __init__(self) -> None:
        super().__init__("Incorrect passphrase.")


class ManifestTamperedException(Exception):
    def __init__(self) -> None:
        super().__init__(
            "The backup's manifest failed authentication — it may have been modified."
        )


class CorruptMemberException(Exception):
    def __init__(self, path: str) -> None:
        super().__init__(f"Encrypted content is damaged or was tampered with: {path}")


class UnsupportedKDFException(Exception):
    def __init__(self, alg: str) -> None:
        super().__init__(
            f"This backup uses an unsupported key-derivation algorithm ({alg}). Please update the app."
        )


def _hmac_sha256(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256.

    PORT: JCA forbids a zero-length key, so Kotlin pads an empty key to 32 zero
    bytes — HMAC's own key padding makes that identical. We replicate the same
    padding so PRK derivation matches CryptoKit exactly.
    """
    effective_key = key if key else bytes(32)
    return hmac.new(effective_key, data, hashlib.sha256).digest()


def _hkdf_sha256(ikm: bytes, info: str) -> bytes:
    """HKDF-SHA256, RFC 5869, empty salt, single-block expand (32 bytes out).

    Matches CryptoKit's `HKDF<SHA256>.deriveKey(inputKeyMaterial:info:)`.
    """
    prk = _hmac_sha256(bytes(0), ikm)  # extract, salt = empty
    return _hmac_sha256(prk, info.encode("utf-8") + bytes([0x01]))


def _pbkdf2(passphrase: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac(
        "sha256", passphrase.encode("utf-8"), salt, iterations, dklen=32
    )


class BackupCrypto:
    """Object translation of the Kotlin `object BackupCrypto`."""

    SCHEME = "minisbak-enc/1"
    PBKDF2_ITERATIONS = 600_000
    SALT_BYTES = 16

    # 4 MiB plaintext per independently sealed segment (§5.3).
    SEGMENT_SIZE = 4 * 1024 * 1024

    # `MBK1` — identifies an encrypted member before any JSON parse attempt.
    MAGIC = bytes([0x4D, 0x42, 0x4B, 0x31])

    _NONCE_BYTES = 12
    _TAG_BITS = 128
    _TAG_BYTES = _TAG_BITS // 8

    class Keys:
        """The derived key set.

        Lives only for the duration of an export/import; call :meth:`destroy`
        when finished — the passphrase itself is never persisted.
        """

        def __init__(self, kek: bytes) -> None:
            # Everything under `data/` and `blobs/`.
            self.data_key: bytes = _hkdf_sha256(kek, "minisbak/data")
            # `secrets.json` only.
            self.secrets_key: bytes = _hkdf_sha256(kek, "minisbak/secrets")
            # Authenticates manifest.json.
            self.mac_key: bytes = _hkdf_sha256(kek, "minisbak/mac")
            # Answers "is this passphrase right?" without touching any payload.
            self.verifier_key: bytes = _hkdf_sha256(kek, "minisbak/verify")
            kek = bytes(len(kek))  # PORT: kek.fill(0)

        @property
        def verifier(self) -> str:
            """HMAC(verifier_key, "minisbak-v1") truncated to 16 bytes, base64."""
            mac = _hmac_sha256(self.verifier_key, b"minisbak-v1")
            return base64.b64encode(mac[:16]).decode("ascii")

        def destroy(self) -> None:
            self.data_key = bytes(len(self.data_key))
            self.secrets_key = bytes(len(self.secrets_key))
            self.mac_key = bytes(len(self.mac_key))
            self.verifier_key = bytes(len(self.verifier_key))

    # MARK: - Derivation

    @staticmethod
    def make_salt() -> bytes:
        return os.urandom(BackupCrypto.SALT_BYTES)

    @staticmethod
    def derive_keys(passphrase: str, kdf: BackupManifest.Encryption.KDF) -> Keys:
        """Derive the key set from a passphrase.

        Deliberately synchronous and slow — 600k PBKDF2 rounds are the point.
        """
        try:
            salt = base64.b64decode(kdf.salt)
        except (ValueError, base64.binascii.Error):
            raise CorruptMemberException("manifest.encryption.kdf.salt")
        if kdf.alg == "pbkdf2-hmac-sha256":
            iterations = kdf.iterations if kdf.iterations is not None else BackupCrypto.PBKDF2_ITERATIONS
            return BackupCrypto.Keys(_pbkdf2(passphrase, salt, iterations))
        # "argon2id" would come from a future build that vendors Argon2;
        # refusing loudly is correct — see the header comment.
        raise UnsupportedKDFException(kdf.alg)

    @staticmethod
    def current_kdf(salt: bytes) -> BackupManifest.Encryption.KDF:
        """The KDF descriptor to write into a new package."""
        return BackupManifest.Encryption.KDF(
            alg="pbkdf2-hmac-sha256",
            salt=base64.b64encode(salt).decode("ascii"),
            iterations=BackupCrypto.PBKDF2_ITERATIONS,
        )

    # MARK: - Member encryption (§5.3)

    @staticmethod
    def encrypt_file(source, destination, key: bytes, path: str) -> None:
        """Encrypt one package member, streaming through 4 MiB segments.

        Layout: `MBK1` then, per segment, `UInt32 big-endian length` followed by
        `nonce ‖ ciphertext ‖ tag`. A zero-length file writes zero segments; the
        magic alone marks it as encrypted.
        """
        # `source`/`destination` are pathlib.Path (PORT: Kotlin File).
        with open(source, "rb") as src, open(destination, "wb") as dst:
            dst.write(BackupCrypto.MAGIC)
            index = 0
            while True:
                chunk = _read_up_to(src, BackupCrypto.SEGMENT_SIZE)
                if chunk is None:
                    break
                nonce = os.urandom(BackupCrypto._NONCE_BYTES)
                aes = AESGCM(key)
                # cipher.doFinal(chunk) == ct||tag; combined = nonce||ct||tag.
                sealed = aes.encrypt(nonce, chunk, BackupCrypto._aad(path, index))
                combined = nonce + sealed
                dst.write(len(combined).to_bytes(4, byteorder="big"))
                dst.write(combined)
                index += 1

    @staticmethod
    def decrypt_file(source, destination, key: bytes, path: str) -> None:
        with open(source, "rb") as src:
            header = src.read(len(BackupCrypto.MAGIC))
            if len(header) != len(BackupCrypto.MAGIC) or header != BackupCrypto.MAGIC:
                raise CorruptMemberException(path)
            with open(destination, "wb") as dst:
                BackupCrypto.decrypt_stream(src, dst, key, path)

    @staticmethod
    def decrypt_stream(src, dst, key: bytes, path: str) -> None:
        """Decrypt a member whose `MBK1` magic has already been consumed."""
        index = 0
        while True:
            first = src.read(1)
            if not first:
                break  # clean EOF at a segment boundary
            len_bytes = first + src.read(3)
            if len(len_bytes) != 4:
                raise CorruptMemberException(path)
            length = int.from_bytes(len_bytes, byteorder="big")
            max_len = (
                BackupCrypto.SEGMENT_SIZE
                + BackupCrypto._NONCE_BYTES
                + BackupCrypto._TAG_BYTES
                + 64
            )
            if length < BackupCrypto._NONCE_BYTES + BackupCrypto._TAG_BYTES or length > max_len:
                raise CorruptMemberException(path)
            body = src.read(length)
            if len(body) != length:
                raise CorruptMemberException(path)
            try:
                aes = AESGCM(key)
                nonce = body[: BackupCrypto._NONCE_BYTES]
                ct = body[BackupCrypto._NONCE_BYTES:]
                plaintext = aes.decrypt(nonce, ct, BackupCrypto._aad(path, index))
            except Exception:
                # An auth failure here is indistinguishable from a wrong key,
                # but the caller checks the verifier first, so by this point
                # the passphrase is known-good and this really is damage.
                raise CorruptMemberException(path)
            dst.write(plaintext)
            index += 1

    @staticmethod
    def _aad(path: str, segment: int) -> bytes:
        return f"{path}#{segment}".encode("utf-8")

    # MARK: - Manifest authentication (§5.3)

    @staticmethod
    def manifest_mac(raw_bytes: bytes, key: bytes) -> str:
        return base64.b64encode(_hmac_sha256(key, raw_bytes)).decode("ascii")

    @staticmethod
    def verify_manifest_mac(raw_bytes: bytes, expected: str, key: bytes) -> None:
        actual = base64.b64decode(BackupCrypto.manifest_mac(raw_bytes, key))
        try:
            exp = base64.b64decode(expected.strip())
        except (ValueError, base64.binascii.Error):
            raise ManifestTamperedException()
        # Constant-time compare (MessageDigest.isEqual).
        if not hmac.compare_digest(actual, exp):
            raise ManifestTamperedException()

    @staticmethod
    def verifier_matches(stored: str, keys: Keys) -> bool:
        """Constant-time verifier comparison, so a wrong passphrase can't be timed."""
        try:
            a = base64.b64decode(stored)
        except (ValueError, base64.binascii.Error):
            return False
        try:
            b = base64.b64decode(keys.verifier)
        except (ValueError, base64.binascii.Error):
            return False
        return hmac.compare_digest(a, b)


# MARK: - Stream helpers

def _read_up_to(input, max_bytes: int) -> bytes | None:
    """Read up to `max_bytes`; None at clean EOF, never a zero-length array."""
    buf = input.read(max_bytes)
    if not buf:
        return None
    return buf
