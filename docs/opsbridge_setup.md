# opsbridge 本番公開 手順書

汎用メンテナンス用 API `opsbridge`（`/ops/api/export`・`/ops/api/writeback`）を本番で有効化する手順。
Mac から Cloudflare Tunnel 越しに、本番DBの**ホワイトリストされたモデル**を読取・書戻しできるようになる。
当面の用途は来客(`visitors.Visitor`)・訪問(`meetings.Meeting`)データのクリーニング。

## 前提：トンネル/認証は「既存流用・追加作業ゼロ」

`opsbridge` は新しい URL パス（`/ops/...`）を足すだけで、**新しいトンネルも Cloudflare Access も不要**。
理由は、`docs/cs_bridge_realtime_cutover.md` で設定済みの名前付きトンネル `bridge.<domain>` が
**ホスト単位**で Django(Waitress) にルートし、Cloudflare Access(Service Token) で保護しているため。
同じホストの別パスである `/ops/api/*` は、その三層認証（Access + Bearer + HMAC）に**自動で相乗り**する。

- Bearer は既存の `CS_BRIDGE_API_TOKEN` を再利用（新トークン発行不要）
- writeback の HMAC 署名は既存の `CS_BRIDGE_HMAC_SECRET` を再利用
- → **本番に投入する新しい環境変数はゼロ**

> もしまだ cs_bridge のトンネル本番公開（cutover doc §1〜3）が済んでいない場合は、先にそちらを実施。
> 済んでいれば、本書の4ステップだけで `opsbridge` も公開される。

## 本番での4ステップ

本番 Windows サーバー（`D:\INTRANET_PORTAL\intranet_portal`）で実行。

### ① 最新コードを取得
GitHub→Gitee ミラー→本番自動 pull の通常フロー（`main` マージ後 5 分以内）。手動で急ぐ場合は本番で:
```
cd /d D:\INTRANET_PORTAL\intranet_portal
git pull
```

### ② マイグレーション（★唯一の落とし穴：これを忘れると 500 になる）
```
venv\Scripts\python.exe manage.py migrate opsbridge
```
`OpsAuditLog` と `OpsProcessedMessage` の2テーブルを作る。**ここを忘れると writeback 監査記録で失敗する。**

### ③ Waitress 再起動
本番スケジューラの自己再起動（`run_portal.bat` のループ）で新コードが読まれる。手動なら Waitress プロセスを再起動。

### ④ 疎通確認（本番サーバー上の localhost で）
```
curl -X POST -H "Authorization: Bearer %CS_BRIDGE_API_TOKEN%" -H "Content-Type: application/json" ^
  -d "{\"model\":\"visitors.Visitor\",\"fields\":[\"id\",\"company_name\"]}" ^
  http://localhost:<Waitressのポート>/ops/api/export
```
`{"ok": true, "model": "visitors.Visitor", "schema": [...], "count": N, "rows": [...]}` が返れば成功。
`401` → トークン不一致 / `403` → モデル未許可 / `500` → ②のマイグレーション忘れを疑う。

## Mac からの取得（トンネル越し）

`work/ops_fetch.py` を使う（別ファイル）。Keychain の `cs-bridge-api-token` と `.env` の
`CS_BRIDGE_API_BASE`・`CF_ACCESS_CLIENT_ID`・`CF_ACCESS_CLIENT_SECRET`（いずれも cs_bridge の既存値）を読む。
```
python3 ops_fetch.py export visitors.Visitor
python3 ops_fetch.py export meetings.Meeting --fields id,company_name,last_name,first_name,title
```

## セキュリティ要点

- **export は読取専用**。`settings.OPSBRIDGE_EXPORT_MODELS` のモデルのみ。filters は単純等価のみ（`__lookup` 禁止）。
- **writeback はモデル＋フィールド単位のホワイトリスト**（`settings.OPSBRIDGE_WRITEBACK_MODELS`）。
  `cancelled`（論理削除フラグ）は意図的に許可外＝API経由で論理削除を誤操作できない。物理DELETEは未実装。
- writeback は **HMAC 署名必須**＋ **nonce 冪等**（同一リクエスト再送は二重適用しない）＋ **dry_run** 対応。
- 全 writeback は `OpsAuditLog` に before/after を記録（Django 管理画面 > 汎用API監査ログ で閲覧可）。

## 許可モデルを増やすとき

`intranet_portal/settings.py` の `OPSBRIDGE_EXPORT_MODELS` / `OPSBRIDGE_WRITEBACK_MODELS` に追記して
本番反映（①③）。モデル追加だけならマイグレーション不要。
