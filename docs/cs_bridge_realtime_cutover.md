# CS Bridge リアルタイム化 本番切替ランブック（Phase 4）

メール2経路（[CS-SYNC]/[CS-WB]）を、**Cloudflare Tunnel 越しの HTTP API** に切り替える手順。
契約（payload/HMAC/op/seq）は不変。社内DjangoのAPIは実装・テスト済み（`cs_tasks/bridge/api.py`）、
Mac(cs_bridge)の http transport も実装・テスト済み。残るは「公開」と「切替」だけ。

```
 Mac(cs_bridge, menubar)            Cloudflare                 社内Windows(Django/Waitress)
   CS_BRIDGE_TRANSPORT=http  ─443─▶  bridge.<domain>  ◀─443(常張)─  cloudflared (Windowsサービス)
                                     + Access(Service Token)             │ localhost:<Waitressポート>
                                                                          ▼  GET /cs-tasks/bridge/api/sync
                                                                             POST /cs-tasks/bridge/api/writeback
```

---

## 0. IT 部門への説明（1枚）

| 項目 | 内容 |
|---|---|
| 目的 | 社内CS課題管理とMac管理アプリの連携を、メール→リアルタイムAPIに置換（遅延 数分→1秒以下） |
| インバウンド開放 | **不要**。社内サーバのポートは一切開けない |
| 外向き許可 | `cloudflared` が **443(TCP)** で Cloudflare エッジへ常時接続（中国成立確認済み。QUIC不可でも http2 にフォールバック） |
| 公開範囲 | `bridge.<domain>` の **API 2本のみ**（sync/writeback）。社内ポータル本体は晒さない |
| 認証 | ①Cloudflare Access(Service Token) ②アプリのBearerトークン ③writebackはHMAC署名 の**三層** |
| データ経路 | 全区間TLS。Cloudflareエッジを通過（CS課題本文）。機微度に応じ要確認 |
| 監査 | Cloudflare Access にアクセスログ。社内側も BridgeProcessedMessage に原文保存 |

---

## 1. 社内Windows: 名前付きトンネル + cloudflared サービス化

> 使い捨て Quick Tunnel ではなく、固定ホスト名の**名前付きトンネル**にする。

1. Cloudflare にドメインを登録（既存可）。Zero Trust > Networks > Tunnels > **Create a tunnel**（Cloudflared型）。
2. 表示される token で Windows にサービス導入（管理者 cmd）:
   ```
   cloudflared.exe service install <トンネルtoken>
   ```
   → 起動時に外向き接続を張る常駐サービスになる（中国でも実証済み。不安定なら
   `%ProgramData%\Cloudflare\cloudflared\config.yml` に `protocol: http2` を指定）。
3. Public Hostname を追加: `bridge.<domain>` → Service `http://localhost:<Waitressのポート>`
   （nginx を前段にしているなら nginx の listen ポート。Host ヘッダで弾かれる場合は
   nginx 側 server_name に bridge.<domain> を許可、または cloudflared の
   `originRequest.httpHostHeader` で内部ホストに合わせる）。

## 2. 社内Windows: Cloudflare Access（Service Token）

1. Zero Trust > Access > Applications > **Add application（Self-hosted）**。Domain = `bridge.<domain>`。
2. Policy: Action=**Service Auth**、Include = **Service Token**（新規作成）。
   → `Client ID` と `Client Secret` が発行される（Mac側 env に入れる）。
3. これでトークン無しのブラウザアクセスはエッジで遮断される。

## 3. 社内Windows: API トークンを Django に設定

- 本番の Waitress プロセスの環境変数に **`CS_BRIDGE_API_TOKEN`** を設定（十分長いランダム値）。
  未設定だと API は全拒否（フェイルクローズ）なので、設定するまで新APIは無効＝安全。
- `CS_BRIDGE_HMAC_SECRET` は既存のまま（writeback 署名検証に流用）。
- 反映後、ローカルで疎通確認（社内サーバ上で）:
  ```
  curl -H "Authorization: Bearer <APIトークン>" http://localhost:<port>/cs-tasks/bridge/api/sync
  ```
  → snapshot JSON が返れば OK。

## 4. Mac(cs_bridge): http へ切替

1. API トークンを Keychain へ（社内の `CS_BRIDGE_API_TOKEN` と同値）:
   ```
   security add-generic-password -s cs-bridge-api-token -a "$USER" -w '<APIトークン>'
   ```
2. `cs_bridge/.env` に（非秘密の設定。menubar が自動で読む）:
   ```
   CS_BRIDGE_TRANSPORT=http
   CS_BRIDGE_API_BASE=https://bridge.<domain>
   CF_ACCESS_CLIENT_ID=<Service Token の Client ID>
   CF_ACCESS_CLIENT_SECRET=<Service Token の Client Secret>
   # 任意: リアルタイム寄りに
   CS_BRIDGE_POLL_SEC=20
   ```
3. メニューバーの **「再起動（コード再読込）」**で反映（.env を読み直す）。

## 5. 検証（切替直後）

- メニュー「今すぐ同期」→ `data/menubar.log` に取得が出る（IMAPでなくHTTP経由）。
- 課題にコメントを足して送信 → 社内ポータルに反映されることを確認（往復）。
- ログに `errors=0` が続くか、数サイクル観察。

## 6. 切替完了 → メール経路の停止・撤去

順序を守る（いきなり消さない）:
1. **並行運用で数日検証**（http で問題が無いこと）。
2. 社内 `scheduler.py` の `cs_sync_send` / `cs_inbound_poll` 定期実行を停止
   （または CS_BRIDGE_SYNC_RECIPIENTS / INTAKE_IMAP を空に）。
3. Mac は http のみで稼働。
4. 安定後にコード撤去: 社内 `outbound.send_snapshot` 周辺のメール送信・`cs_inbound_poll`、
   Mac `app/mail/`（imap_fetch/smtp_send）と email 分岐。**契約(payload/security/contract)は残す**。
5. `docs/cs_bridge_handoff.md` と本書を更新。

## ロールバック

問題時は **Mac の `.env` で `CS_BRIDGE_TRANSPORT=email` に戻して「再起動」**するだけ
（社内のメール経路を 6-2 で止めていなければ即復帰）。社内 API は `CS_BRIDGE_API_TOKEN` を
空にすれば無効化できる。

---

## 現状の実装（Phase 1–3 完了済み・参照）

- 社内: `cs_tasks/bridge/api.py`（GET sync / POST writeback, Bearer + HMAC, CSRF除外）、
  `inbound.apply_writeback(..., enforce_sender=False)`、`settings.CS_BRIDGE_API_TOKEN`、
  テスト `cs_tasks/tests.py::BridgeApiTests`。
- Mac: `app/transport.py`（fetch/deliver を email|http でディスパッチ）、`keychain.api_token()`、
  `config.TRANSPORT/API_BASE/CF_ACCESS_*`、`app/menubar.py`(.env自動読込)、テスト `tests/test_transport.py`。
