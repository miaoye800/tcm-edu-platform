"""密码哈希与校验。"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os

HASH_NAME = "sha256"
PBKDF2_ITERATIONS = 260_000
PASSWORD_SCHEME = "pbkdf2_sha256"


def hash_password(password: str) -> str:
    """使用 PBKDF2 和随机盐生成不可逆密码摘要。"""

    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(HASH_NAME, password.encode("utf-8"), salt, PBKDF2_ITERATIONS)
    return "$".join(
        [
            PASSWORD_SCHEME,
            str(PBKDF2_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        ]
    )


def verify_password(password: str, password_hash: str | None) -> bool:
    """校验明文密码是否匹配已保存的哈希。"""

    if not password_hash:
        return False
    try:
        scheme, iterations_text, salt_text, digest_text = password_hash.split("$", maxsplit=3)
        if scheme != PASSWORD_SCHEME:
            return False
        iterations = int(iterations_text)
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(digest_text.encode("ascii"))
    except (ValueError, TypeError):
        return False

    actual = hashlib.pbkdf2_hmac(HASH_NAME, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)
