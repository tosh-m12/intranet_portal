# cs_tasks/bridge/payload.py
"""往路/復路メールのペイロード(マーカー埋め込みJSON)の組み立てと抽出。

Gmailコネクタは添付を取得できないため、JSONは本文(プレーンテキスト)に
マーカーで囲んで埋め込む。
"""
import json

# 現行スキーマ。送信(往路スナップショット/復路書き戻し)はこの値で発行する。
# v1 → v2: 往路スナップショットに meta.assignees(担当者候補リスト)を追加。
# v2 → v3: 新規顧客課題のビジネス概要項目(状態/スタート時期/継続スポット/予想売上/
#          ビジネス形態/グループ内客先窓口)を Task に追加。往路・復路とも対応。
SCHEMA_VERSION = 3

# 復路(書き戻し)で受け入れ可能な schema 集合。Mac/サーバが段階的に
# アップグレードしても無停止で繋がるよう、後方互換を残す。
SUPPORTED_INBOUND_SCHEMAS = {1, 2, 3}

# 往路(社内→Mac): スナップショット
SYNC_BEGIN = "-----CS-SYNC-BEGIN-----"
SYNC_END = "-----CS-SYNC-END-----"

# 復路(Mac→社内): 書き戻し操作 + 署名
WB_BEGIN = "-----CS-WB-BEGIN-----"
WB_END = "-----CS-WB-END-----"
SIG_BEGIN = "-----CS-WB-SIG-----"
SIG_END = "-----CS-WB-SIG-END-----"


def _extract_block(text, begin, end):
    start = text.find(begin)
    if start == -1:
        return None
    start += len(begin)
    stop = text.find(end, start)
    if stop == -1:
        return None
    return text[start:stop].strip()


def wrap_sync(payload):
    """スナップショットpayload(dict)を本文用テキストに整形。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"{SYNC_BEGIN}\n{body}\n{SYNC_END}\n"


def extract_sync(text):
    """本文テキストからスナップショットpayload(dict)を取り出す。無ければNone。"""
    block = _extract_block(text, SYNC_BEGIN, SYNC_END)
    if not block:
        return None
    return json.loads(block)


def wrap_writeback(payload, signature):
    """書き戻しpayload(dict)と署名を本文用テキストに整形。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    return (
        f"{WB_BEGIN}\n{body}\n{WB_END}\n"
        f"{SIG_BEGIN}\n{signature}\n{SIG_END}\n"
    )


def extract_writeback(text):
    """本文テキストから (payload(dict), signature(str)) を取り出す。

    payload が無ければ (None, None)。署名が無ければ signature は None。
    """
    block = _extract_block(text, WB_BEGIN, WB_END)
    sig = _extract_block(text, SIG_BEGIN, SIG_END)
    payload = json.loads(block) if block else None
    return payload, (sig or None)
