"""Authenticated encryption for private advisor originals and recognition candidates."""

import base64
import os

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .changes import Conflict

PREFIX = b"ACME-ADVISOR-AES1\x00"


def _cipher():
    try:
        key = base64.b64decode(os.environ.get("WAREHOUSE_ADVISOR_MATERIAL_KEY", ""), validate=True)
        if len(key) != 32:
            raise ValueError("key unavailable")
        return AESGCM(key)
    except (ValueError, TypeError) as error:
        raise Conflict("私有材料加密尚未配置，请联系管理员") from error


def encrypt(body, context):
    nonce = os.urandom(12)
    return PREFIX + nonce + _cipher().encrypt(nonce, body, str(context).encode())


def decrypt(body, context):
    if not body.startswith(PREFIX):
        raise Conflict("材料加密格式不可用，请联系管理员核对")
    offset = len(PREFIX)
    try:
        return _cipher().decrypt(
            body[offset : offset + 12], body[offset + 12 :], str(context).encode()
        )
    except (InvalidTag, ValueError) as error:
        raise Conflict("材料密钥或完整性校验失败") from error
