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

正本は `cs_tasks/bridge/payload.py`。**現行 `SCHEMA_VERSION=2`**。
復路は `SUPPORTED_INBOUND_SCHEMAS={1, 2}` で旧版も無停止受け入れ。

往路(社内→Mac, 本文埋め込み):
```
-----CS-SYNC-BEGIN-----
{ "type":"snapshot","schema":2,"seq":<int>,"generated_at":...,"since":...,
  "meta":{ "assignees":[ { "email","display_name","is_staff" } ] },
  "tasks":[{ "id","title","title_ja","description","description_ja","client_name",
             "assignee","assignee_email","due_date","is_closed",
             "progress_updates":[{ "id","author","content","content_ja","created_at","is_closed",
               "comments":[{ "id","content","content_ja","created_at" }] }] }] }
-----CS-SYNC-END-----
```
- `meta.assignees`(v2 で追加): Mac 側の `add_task`/`edit_task` 担当者ドロップダウンに使う候補リスト。
  `User.objects.filter(is_active=True, is_superuser=False)` で生成。Mac 側は選んだ `email` を
  書き戻し op の `assignee_email` に入れる。サーバ側のユーザー追加/削除は次回送信で自動反映。

復路(Mac→社内, 本文埋め込み + 署名):
```
-----CS-WB-BEGIN-----
{ "schema":2, "nonce":"<一意>", "issued_at":...,
  "ops":[
    {"op_id":"<一意>","action":"add_comment","progress_id":<id>,"content_zh":"...","content_ja":"..."},
    {"op_id":...,"action":"edit_progress","progress_id":<id>,"content_zh":"...","content_ja":"..."},
    {"op_id":...,"action":"edit_comment","comment_id":<id>,"content_zh":"...","content_ja":"..."},
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

### 5.1 Mac 側アプリ(リポジトリ外。本仕様の中心)
方針(2026-06 決定):
- **接続方式**: 公式 Gmail コネクタは使わず、**IMAP+SMTP 直接実装**。Gmail のアプリパスワードを発行して使用。
- **同期アカウント**: 社内側=`cs_info@ngls.sh.cn`(既存)、Mac 側=`tosh.m909@gmail.com`(当面)。件名 `[CS-SYNC]`(往路)/ `[CS-WB]`(復路)で分離。
- **MVP 対象**: 全機能(`add_comment` / `edit_progress` / `edit_comment` / `edit_task` / `add_task`)を最初から扱う。社内側は実装済み(`edit_comment` は v2.1 で追加)。
- **誤送信防止**: コネクタによる下書きゲートが無くなるので、Mac 側 UI に「送信前プレビュー+承認ボタン」を必ず置く。
- **担当者ドロップダウン**: 往路スナップショットの `meta.assignees`(SCHEMA_VERSION=2 で追加)からそのまま選択 UI を作る。

作業項目:
- IMAP で `[CS-SYNC]` を定期取得 → 本文マーカー間の JSON を保存(`seq` で取りこぼし検知)
- **翻訳ギャップ自動補完(v2.1)**: スナップショットを舐め「片言語だけ埋まっている」エントリを検出 →
  欠落側を翻訳 → 復路で **`edit_task` / `edit_progress` / `edit_comment`** に該当側のみ書き戻し。
  社内側は両言語入力を受け付けるが翻訳機能を持たないので、Mac 稼働中は両言語が揃った状態へ収束する。
  ユーザー編集の有無に関係なく定期実行する。
- Claude が中↔日翻訳 → レビュー画面で原文+訳を並記
- 日本語で 上長コメント追加 / タスク編集・新規追加 / 進捗編集 / 担当者割当
- 日→中翻訳 → 復路 JSON(`-----CS-WB-*-----` マーカー) + HMAC 署名 → SMTP 送信(プレビュー承認後)

### 5.2 フェーズ2(準リアルタイム)
- `cs_tasks/signals.py` を新設し `post_save`(Task/ProgressUpdate/SupervisorComment)で
  往路の差分送信を即時トリガー(デバウンス推奨)。`apps.ready()` で接続。
- Mac 側取得間隔の短縮。

### 5.3 フェーズ3 / 将来構想
- リアルタイム連動(spec 14章): Tailscale 等で接続路を作り、メール→API+SSE/WebSocket へ。
  JSON 契約は流用、経路だけ差し替え。
- Claude による課題・進捗の自動起票(spec 15章): 復路 op に `add_progress` を追加。
  必ず承認ゲート経由。Claude 起票が分かる印を付け監査可能に。

## 6. 未決事項
### 6.1 決定済み
- 同期用 Gmail: 社内 `cs_info@ngls.sh.cn` / Mac `tosh.m909@gmail.com`
- Mac 接続方式: IMAP+SMTP 直接
- Mac の MVP スコープ: 全機能
- 担当者リスト配信: 往路スナップショット `meta.assignees`(SCHEMA_VERSION=2)

### 6.2 残課題
- Mac 取得間隔の確定値(初期 5 分推奨)
- HMAC 秘密鍵のローテーション運用方針(初期は手動・必要時のみ)
- `content_ja` のサーバ側表示: v2.1 で社内側に**両言語入力ポリシー**を導入(`_route_text`)。
  入力時に検出言語側のみ保存・逆側は空。`pick_lang` は双方向フォールバック対応済み。
  Mac 側翻訳ワークフロー前提で、両言語が揃った状態に Mac 稼働中は収束する。
- 復路適用結果(成功/スキップ/エラー)を Mac 側に戻す経路(当面はサーバログ閲覧で代替)

## 7. テスト/開発メモ
- テスト: `python manage.py test cs_tasks`(venv は Python 3.14)。
- 規約(CLAUDE.md): インデント4スペース、UI日本語/識別子英語、論理削除、
  パスは pathlib、authsys.User を FK 参照、メールは mailcenter 経由。
- データ契約を変えるときは `payload.py` の `SCHEMA_VERSION` を上げ、Mac側と整合を取る。
