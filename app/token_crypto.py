import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

TOKEN_ENCRYPTION_VERSION = 1


class InvalidTokenEncryptionKey(ValueError):
    pass


class TokenDecryptionError(ValueError):
    pass


@dataclass(frozen=True)
class EncryptedTokenValue:
    ciphertext: str
    nonce: str
    version: int = TOKEN_ENCRYPTION_VERSION


class AesGcmTokenCipher:
    def __init__(self, encoded_key: str) -> None:
        try:
            key = base64.b64decode(encoded_key.strip(), validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidTokenEncryptionKey(
                "Token encryption key must be valid base64"
            ) from exc
        if len(key) != 32:
            raise InvalidTokenEncryptionKey(
                "Token encryption key must decode to exactly 32 bytes"
            )
        self._cipher = AESGCM(key)

    def encrypt(self, plaintext: str, associated_data: str) -> EncryptedTokenValue:
        nonce = os.urandom(12)
        ciphertext = self._cipher.encrypt(
            nonce, plaintext.encode("utf-8"), associated_data.encode("utf-8")
        )
        return EncryptedTokenValue(
            ciphertext=base64.b64encode(ciphertext).decode("ascii"),
            nonce=base64.b64encode(nonce).decode("ascii"),
        )

    def decrypt(self, value: EncryptedTokenValue, associated_data: str) -> str:
        if value.version != TOKEN_ENCRYPTION_VERSION:
            raise TokenDecryptionError("Unsupported token encryption version")
        try:
            nonce = base64.b64decode(value.nonce, validate=True)
            ciphertext = base64.b64decode(value.ciphertext, validate=True)
            plaintext = self._cipher.decrypt(
                nonce, ciphertext, associated_data.encode("utf-8")
            )
            return plaintext.decode("utf-8")
        except (binascii.Error, InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise TokenDecryptionError("Stored token could not be decrypted") from exc
