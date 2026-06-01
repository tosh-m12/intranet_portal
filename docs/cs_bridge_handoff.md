# CS課題 翻訳ブリッジ 引き継ぎノート(→ Claude Code / VS Code)

このノートは、Cowork セッションで実装したフェーズ1(社内側アダプタ)の状態と、
以降の作業を Claude Code に引き継ぐためのもの。設計の全体像は
`docs/cs_translation_bridge_spec.md` を参照(データ契約・将来構想・自動起票含む)。

---

## 1. 何を作っているか(1行)

私用Mac上の管理者専用アプリ(Claude自動翻訳付き)と、社内 `cs_tasks` を
**メール2経路**で連携させる。社内側には最小の「受け口アダプタ + 二言語フィールド」を追加する。
Mac は社内LAN(`10.214.80.86`)に到達できないため、連携路はメールのみ。

## 2. フェーズ1で完了済み(社内側アダプタ)

すべて実装・テスト済み(`python manage.py test cs_tasks` = 22 passed)。

追加/変更ファイル:
- `cs_tasks/models.py`
  - 二言語: `Task.title_ja` / `Task.description_ja` / `ProgressUpdate.content_ja` / `SupervisorComment.content_ja`
  - 表示ヘルパ: `pick_lang()` と各モデルの `display_*(lang)`(`lang='ja'` かつ非空なら日本語、それ以外は原文)
  - 冪等用: `BridgeProcessedMessage`(nonce) / `BridgeProcessedOperation`(op_id)
- `cs_tasks/migrations/0003_*.py`(生成済み。**本番では `migrate` 必要**)
- `cs_tasks/bridge/`
  - `security.py` … HMAC-SHA256 署名/検証。鍵未設定なら検証は常に False(フェイルクローズ)
  - `payload.py` … マーカー埋め込みJSONの整形/抽出。**データ契約の正本**(`SCHEMA_VERSION=1`)
  - `inbound.py` … 復路適用。差出人限定→署名検証→nonce重複→op冪等→各opをsavepointで適用
  - `outbound.py` … ID付きスナップショット構築 + 同期メール送信
- `cs_tasks/management/commands/`
  - `cs_sync_send.py` … 往路送信(`--minutes N` 差分 / `--dry-run`)
  - `cs_inbound_poll.py` … 復路IMAP受信→適用(未設定なら安全にスキップ)
- `mailcenter/email_utils.py` … `send_text_mail()` 追加(プレーンテキスト送信)
- `intranet_portal/settings.py` … `CS_BRIDGE_*` を環境変数から読み込み(下記)

対応する書き戻し操作(action): `add_comment` / `edit_progress` / `edit_task` / `add_task`。

## 3. データ契約(メールでもAPIでも同一構造)

正本は `cs_tasks/bridge/payload.py`。

往路(社内→Mac, 本文埋め込み):
```
-----CS-SYNC-BEGIN-----
{ "type":"snapshot","schema":1,"seq":<int>,"generated_at":...,"since":...,
  "tasks":[{ "id","title","title_ja","description","description_ja","client_name",
             "assignee","assignee_email","due_date","is_closed",
             "progress_updates":[{ "id","author","content","content_ja","created_at","is_closed",
               "comments":[{ "id","content","content_ja","created_at" }] }] }] }
-----CS-SYNC-END-----
```

復路(Mac→社内, 本文埋め込み + 署名):
```
-----CS-WB-BEGIN-----
{ "schema":1, "nonce":"<一意>", "issued_at":...,
  "ops":[
    {"op_id":"<一意>","action":"add_comment","progress_id":<id>,"content_zh":"...","content_ja":"..."},
    {"op_id":...,"action":"edit_progress","progress_id":<id>,"content_zh":"...","content_ja":"..."},
    {"op_id":...,"action":"edit_task","task_id":<id>,"fields":{
        "title_zh","title_ja","description_zh","description_ja","client_name","due_date","assignee_email"}},
    {"op_id":...,"action":"add_task","fields":{ 同上。title_zh 必須 }}
  ] }
-----CS-WB-END-----
-----CS-WB-SIG-----
<HMAC-SHA256 hex。payload を sort_keys+compact で正規化した JSON に対する署名>
-----CS-WB-SIG-END-----
```
- 署名対象は payload dict の**正規化**(`security.canonical_bytes`: `sort_keys=True, separators=(",",":")`)。
  受信側は JSON を再パースしてから同じ正規化で検証するので、メール折返しの影響を受けない。
- nonce = メール単位のリプレイ防止。op_id = 操作単位の二重適用防止。
  **再送は新しい nonce + 同じ op_id** で行えば、未適用opだけ反映される。

## 4. 本番デプロイ手順(社内サーバ)

1. 環境変数を設定(コミット禁止):
   - `CS_BRIDGE_HMAC_SECRET`(必須。長いランダム文字列。Mac側と共有)
   - `CS_BRIDGE_ALLOWED_SENDERS`(Mac側Gmail。カンマ区切り)
   - `CS_BRIDGE_SYNC_RECIPIENTS`(往路の宛先=Mac側Gmail)
   - `CS_BRIDGE_AUTHOR_EMAIL`(起票/反映の操作主体ユーザーの email)
   - `CS_BRIDGE_MAIL_ACCOUNT`(任意。既定 `cs_report`)
   - 受信箱: `CS_BRIDGE_INTAKE_IMAP_HOST/_PORT/_USER/_PASSWORD/_SSL`、`CS_BRIDGE_INTAKE_MAILBOX`
2. `python manage.py migrate cs_tasks`
3. 往路を定期実行(既存 `cs_tasks/scheduler.py` に相乗り or cron/タスクスケジューラ):
   `python manage.py cs_sync_send --minutes 15`
4. 復路を定期実行: `python manage.py cs_inbound_poll`
5. 送信用 `MailAccount`(code=`cs_report` 推奨)を用意(無ければ共通アカウントにフォールバック)。

> 既存の `settings.py` には SMTP パスワード等が平文でコミットされている。
> この機会に既存分も環境変数化することを推奨(別タスク)。

## 5. これからの作業(未実装)

### 5.1 Mac側アプリ(Cowork側。リポジトリ外の運用構築)
- Gmailコネクタ接続 → 定時タスクで `[CS-SYNC]` メール取得 → 本文JSON保存
- Claude が中→日翻訳 → レビュー画面(ライブ artifact)で原文+訳を並記
- 日本語で 上長コメント追加 / タスク欄 修正・追加
- 日→中翻訳 → 復路JSON生成 → HMAC付与 → **Gmail下書き作成**
  (公式Gmailコネクタは送信不可。下書き→人手で送信=最終承認ゲート)

### 5.2 フェーズ2(準リアルタイム)
- `cs_tasks/signals.py` を新設し `post_save`(Task/ProgressUpdate/SupervisorComment)で
  往路の差分送信を即時トリガー(デバウンス推奨)。`apps.ready()` で接続。
- Mac側取得間隔の短縮。

### 5.3 フェーズ3 / 将来構想
- 完全自動送信(Gmailアプリパスワードで SMTP 直送)。
- リアルタイム連動(spec 14章): Tailscale等で接続路を作り、メール→API+SSE/WebSocketへ。
  JSON契約は流用、経路だけ差し替え。
- Claudeによる課題・進捗の自動起票(spec 15章): 復路opに `add_progress` を追加。
  必ず承認ゲート経由。Claude起票が分かる印を付け監査可能に。

## 6. 未決事項(実装前に決める)
- 同期用Gmailアドレス(週報用と分けるか / 専用受信箱の用意)
- Mac取得の具体間隔(数分の目安)
- `content_ja` をサーバ表示でどこまで使うか(キャッシュ留め or トグルUI作り込み)
- 完全自動送信を入れるか、人手の送信ゲートを恒久化するか

## 7. テスト/開発メモ
- テスト: `python manage.py test cs_tasks`(venv は Python 3.14)。
- 規約(CLAUDE.md): インデント4スペース、UI日本語/識別子英語、論理削除、
  パスは pathlib、authsys.User を FK 参照、メールは mailcenter 経由。
- データ契約を変えるときは `payload.py` の `SCHEMA_VERSION` を上げ、Mac側と整合を取る。
