"""Datalastic API 呼び出し用の SSL コンテキスト。

本番Windowsで Python(urllib) が CERTIFICATE_VERIFY_FAILED になる問題への対応。
curl は通る(=Windows証明書ストアを使う)が Python は自前バンドルのため失敗する。
1) truststore があれば OSの証明書ストア(curlと同じ。社内SSL傍受にも対応)を使う
2) 無ければ certifi のCAバンドル
3) どちらも無ければ None(=既定。従来動作)
環境変数 DATALASTIC_INSECURE=1 で検証無効化(最終手段・非推奨)。
"""
import os
import ssl


def _flag_insecure():
    """DATALASTIC_INSECURE=1 を環境変数 or .env から検出(キーと同基準)。"""
    if os.environ.get('DATALASTIC_INSECURE') == '1':
        return True
    try:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        env_path = os.path.join(base, '.env')
        if os.path.exists(env_path):
            with open(env_path, encoding='utf-8') as f:
                for line in f:
                    s = line.strip()
                    if s.startswith('DATALASTIC_INSECURE=') and s.split('=', 1)[1].strip() == '1':
                        return True
    except Exception:
        pass
    return False


def api_ssl_context():
    if _flag_insecure():
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    try:
        import truststore
        return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    except Exception:
        pass
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        pass
    return None
