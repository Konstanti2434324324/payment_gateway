import hmac
import hashlib


def verify_signature(body: bytes, secret_key: str, signature: str) -> bool:
    expected = hmac.new(secret_key.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


def generate_signature(body: bytes, secret_key: str) -> str:
    return hmac.new(secret_key.encode(), body, hashlib.sha256).hexdigest()
