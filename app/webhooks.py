import hashlib
import hmac


def verify_line_signature(body: bytes, signature: str, channel_secret: str) -> bool:
    if not signature or not channel_secret:
        return False
    digest = hmac.new(channel_secret.encode(), body, hashlib.sha256).digest()
    import base64

    expected = base64.b64encode(digest).decode()
    return hmac.compare_digest(expected, signature)
