# cs_tasks/bridge/security.py
"""復路メールの認証(案B: HMAC-SHA256 署名)。

署名対象は payload を正規化(キーソート・コンパクト)した JSON バイト列。
メール本文がMTAで折り返されても、受信側は JSON を再パースしてから
同じ正規化を行うため、書式差の影響を受けない。

秘密鍵は settings.CS_BRIDGE_HMAC_SECRET(環境変数由来)。
鍵が未設定なら verify は常に False を返す(フェイルクローズ)。
"""
import hashlib
import hmac
import json

from django.conf import settings


def get_secret():
    return getattr(settings, "CS_BRIDGE_HMAC_SECRET", "") or ""


def canonical_bytes(payload):
    """署名対象の正規化バイト列を返す。

    dict は キーソート + コンパクト区切りで安定化。str/bytes はそのまま。
    """
    if isinstance(payload, (bytes, bytearray)):
        return bytes(payload)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def sign(payload, secret=None):
    """payload(通常はdict)のHMAC-SHA256署名(hex)を返す。"""
    key = get_secret() if secret is None else secret
    if isinstance(key, str):
        key = key.encode("utf-8")
    return hmac.new(key, canonical_bytes(payload), hashlib.sha256).hexdigest()


def verify(payload, signature, secret=None):
    """署名を検証する。鍵未設定・署名空・不一致はすべて False。"""
    key = get_secret() if secret is None else secret
    if not key:
        return False
    if not signature:
        return False
    expected = sign(payload, secret=key)
    return hmac.compare_digest(expected, signature.strip())
