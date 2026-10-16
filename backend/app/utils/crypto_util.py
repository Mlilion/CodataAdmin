"""对称加解密工具（Fernet）。

用途：存储外部数据源连接密码等需可逆还原的敏感串（区别于 bcrypt 单向哈希）。
密钥从 settings.SECRET_KEY 经 SHA-256 派生为 32 字节 urlsafe-base64 的 Fernet key。
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config.setting import settings

_PREFIX = "enc::"  # 密文前缀，便于识别与幂等（已加密的不重复加密）


def _fernet() -> Fernet:
    digest = hashlib.sha256(str(settings.SECRET_KEY).encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(plain: str | None) -> str | None:
    """加密明文，返回带前缀的密文；空值原样返回。已是密文则不重复加密。"""
    if not plain:
        return plain
    if plain.startswith(_PREFIX):
        return plain
    token = _fernet().encrypt(plain.encode("utf-8")).decode("utf-8")
    return f"{_PREFIX}{token}"


def decrypt(enc: str | None) -> str | None:
    """解密带前缀的密文；空值或非本工具密文（无前缀）原样返回（兼容历史明文）。"""
    if not enc:
        return enc
    if not enc.startswith(_PREFIX):
        return enc
    token = enc[len(_PREFIX):]
    try:
        return _fernet().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken:
        return None
