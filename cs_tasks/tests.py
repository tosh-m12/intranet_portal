# cs_tasks/tests.py
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from .models import (
    Task,
    ProgressUpdate,
    SupervisorComment,
    BridgeProcessedMessage,
    BridgeProcessedOperation,
    pick_lang,
)
from .bridge import security, payload, inbound, outbound
from .views import _detect_lang, _route_text

User = get_user_model()

SECRET = "unit-test-secret-key"
SENDER = "tosh.m909@gmail.com"


def make_payload(ops, nonce="nonce-1"):
    return {
        "schema": payload.SCHEMA_VERSION,
        "nonce": nonce,
        "issued_at": "2026-06-01T18:30:00+09:00",
        "ops": ops,
    }


def signed(p):
    return security.sign(p, secret=SECRET)


class PickLangTests(TestCase):
    def test_fallback_to_primary(self):
        self.assertEqual(pick_lang("中文", "", "ja"), "中文")
        self.assertEqual(pick_lang("中文", "   ", "ja"), "中文")

    def test_ja_selected(self):
        self.assertEqual(pick_lang("中文", "日本語", "ja"), "日本語")

    def test_zh_default(self):
        self.assertEqual(pick_lang("中文", "日本語", "zh"), "中文")

    def test_zh_falls_back_to_ja_when_primary_empty(self):
        # 双方向フォールバック: JA だけ埋まっている時、ZH モードでも JA を出す
        self.assertEqual(pick_lang("", "日本語", "zh"), "日本語")
        self.assertEqual(pick_lang("   ", "日本語", "zh"), "日本語")

    def test_both_empty(self):
        self.assertEqual(pick_lang("", "", "ja"), "")
        self.assertEqual(pick_lang("", "", "zh"), "")


class SecurityTests(TestCase):
    def test_sign_verify_roundtrip(self):
        p = {"a": 1, "b": "テスト", "c": [1, 2, 3]}
        sig = security.sign(p, secret=SECRET)
        self.assertTrue(security.verify(p, sig, secret=SECRET))

    def test_key_order_independent(self):
        p1 = {"a": 1, "b": 2}
        p2 = {"b": 2, "a": 1}
        self.assertEqual(
            security.sign(p1, secret=SECRET), security.sign(p2, secret=SECRET)
        )

    def test_tampered_payload_fails(self):
        p = {"a": 1}
        sig = security.sign(p, secret=SECRET)
        p["a"] = 2
        self.assertFalse(security.verify(p, sig, secret=SECRET))

    def test_missing_secret_fails_closed(self):
        p = {"a": 1}
        sig = security.sign(p, secret=SECRET)
        self.assertFalse(security.verify(p, sig, secret=""))


class PayloadTests(TestCase):
    def test_sync_roundtrip(self):
        snap = {"type": "snapshot", "schema": 1, "tasks": [{"id": 1, "title": "中文"}]}
        text = payload.wrap_sync(snap)
        self.assertEqual(payload.extract_sync(text), snap)

    def test_writeback_roundtrip(self):
        p = make_payload([{"op_id": "x", "action": "add_comment"}])
        sig = signed(p)
        text = payload.wrap_writeback(p, sig)
        got_p, got_sig = payload.extract_writeback(text)
        self.assertEqual(got_p, p)
        self.assertEqual(got_sig, sig)

    def test_extract_missing(self):
        self.assertIsNone(payload.extract_sync("no markers here"))
        self.assertEqual(payload.extract_writeback("nothing"), (None, None))


@override_settings(
    CS_BRIDGE_HMAC_SECRET=SECRET,
    CS_BRIDGE_ALLOWED_SENDERS=[SENDER],
    CS_BRIDGE_AUTHOR_EMAIL="boss@ngls.sh.cn",
)
class InboundApplyTests(TestCase):
    def setUp(self):
        self.boss = User.objects.create_user(
            email="boss@ngls.sh.cn", password="x", is_staff=True
        )
        self.member = User.objects.create_user(
            email="member@ngls.sh.cn", password="x", last_name="李", first_name="四"
        )
        self.task = Task.objects.create(title="任务A", client_name="客户X")
        self.progress = ProgressUpdate.objects.create(
            task=self.task, author=self.member, content="进展内容"
        )

    def _apply(self, ops, nonce="nonce-1", sender=SENDER):
        p = make_payload(ops, nonce=nonce)
        return inbound.apply_writeback(p, signed(p), sender=sender)

    def test_add_comment(self):
        res = self._apply(
            [{
                "op_id": "op-1", "action": "add_comment",
                "progress_id": self.progress.id,
                "content_zh": "请尽快处理", "content_ja": "至急対応してください",
            }]
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["op-1"])
        c = SupervisorComment.objects.get()
        self.assertEqual(c.content, "请尽快处理")
        self.assertEqual(c.content_ja, "至急対応してください")
        self.assertEqual(c.author, self.boss)  # CS_BRIDGE_AUTHOR_EMAIL

    def test_edit_progress(self):
        res = self._apply(
            [{
                "op_id": "op-2", "action": "edit_progress",
                "progress_id": self.progress.id,
                "content_zh": "改后", "content_ja": "修正後",
            }]
        )
        self.assertTrue(res["ok"])
        self.progress.refresh_from_db()
        self.assertEqual(self.progress.content, "改后")
        self.assertEqual(self.progress.content_ja, "修正後")

    def test_edit_comment(self):
        c = SupervisorComment.objects.create(
            progress=self.progress, author=self.boss,
            content="旧中文", content_ja="",
        )
        res = self._apply(
            [{
                "op_id": "op-ec", "action": "edit_comment",
                "comment_id": c.id,
                "content_zh": "新中文", "content_ja": "新日本語",
            }]
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["op-ec"])
        c.refresh_from_db()
        self.assertEqual(c.content, "新中文")
        self.assertEqual(c.content_ja, "新日本語")

    def test_edit_comment_partial(self):
        # 片側だけ送る → 送られた側だけ更新（Mac の翻訳穴埋め経路の主用途）
        c = SupervisorComment.objects.create(
            progress=self.progress, author=self.boss,
            content="原文中文", content_ja="",
        )
        res = self._apply(
            [{
                "op_id": "op-ec2", "action": "edit_comment",
                "comment_id": c.id,
                "content_ja": "翻訳のみ追加",
            }]
        )
        self.assertTrue(res["ok"])
        c.refresh_from_db()
        self.assertEqual(c.content, "原文中文")  # 不変
        self.assertEqual(c.content_ja, "翻訳のみ追加")

    def test_delete_task_soft(self):
        # task は既存の論理削除運用に合わせて is_cancelled=True 化
        res = self._apply(
            [{"op_id": "op-dt", "action": "delete",
              "target": "task", "id": self.task.id}]
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["op-dt"])
        self.task.refresh_from_db()
        self.assertTrue(self.task.is_cancelled)
        self.assertIsNotNone(self.task.cancelled_at)

    def test_delete_progress_hard(self):
        res = self._apply(
            [{"op_id": "op-dp", "action": "delete",
              "target": "progress", "id": self.progress.id}]
        )
        self.assertTrue(res["ok"])
        self.assertFalse(
            ProgressUpdate.objects.filter(pk=self.progress.id).exists()
        )

    def test_delete_comment_hard(self):
        c = SupervisorComment.objects.create(
            progress=self.progress, author=self.boss, content="x"
        )
        res = self._apply(
            [{"op_id": "op-dc", "action": "delete",
              "target": "comment", "id": c.id}]
        )
        self.assertTrue(res["ok"])
        self.assertFalse(SupervisorComment.objects.filter(pk=c.id).exists())

    def test_delete_unknown_target_is_error(self):
        res = self._apply(
            [{"op_id": "op-du", "action": "delete",
              "target": "unknown", "id": 1}]
        )
        # メッセージ自体は処理完了するが、op はエラー扱いで未記録
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)
        self.assertFalse(
            BridgeProcessedOperation.objects.filter(op_id="op-du").exists()
        )

    def test_delete_idempotent(self):
        # 同じ op_id を別メールで再送 → 2回目はスキップ
        op = {"op_id": "op-di", "action": "delete",
              "target": "progress", "id": self.progress.id}
        r1 = self._apply([op], nonce="msg-d1")
        r2 = self._apply([op], nonce="msg-d2")
        self.assertEqual(r1["applied"], ["op-di"])
        self.assertEqual(r2["skipped"], ["op-di"])

    def test_delete_missing_target_is_noop(self):
        # 既に存在しない id の delete でも例外にはせず冪等的に no-op
        res = self._apply(
            [{"op_id": "op-dm", "action": "delete",
              "target": "comment", "id": 99999}]
        )
        self.assertTrue(res["ok"])
        # 例外なく applied 扱いに（無いものを消したという意味で）
        self.assertEqual(res["applied"], ["op-dm"])

    def test_edit_task(self):
        res = self._apply(
            [{
                "op_id": "op-3", "action": "edit_task",
                "task_id": self.task.id,
                "fields": {
                    "title_zh": "新任务名", "title_ja": "新しい課題名",
                    "client_name": "客户Y", "due_date": "2026-06-15",
                    "assignee_email": "member@ngls.sh.cn",
                },
            }]
        )
        self.assertTrue(res["ok"])
        self.task.refresh_from_db()
        self.assertEqual(self.task.title, "新任务名")
        self.assertEqual(self.task.title_ja, "新しい課題名")
        self.assertEqual(self.task.client_name, "客户Y")
        self.assertEqual(self.task.due_date.isoformat(), "2026-06-15")
        self.assertEqual(self.task.assignee, self.member)

    def test_add_task(self):
        res = self._apply(
            [{
                "op_id": "op-4", "action": "add_task",
                "fields": {"title_zh": "全新任务", "title_ja": "新規課題", "client_name": "客户Z"},
            }]
        )
        self.assertTrue(res["ok"])
        t = Task.objects.get(title="全新任务")
        self.assertEqual(t.title_ja, "新規課題")
        self.assertEqual(t.owner, self.boss)

    def test_add_task_without_title_is_error(self):
        res = self._apply(
            [{"op_id": "op-5", "action": "add_task", "fields": {"client_name": "X"}}]
        )
        self.assertTrue(res["ok"])  # メッセージ自体は処理完了
        self.assertEqual(res["applied"], [])
        self.assertEqual(len(res["errors"]), 1)
        # 失敗opは未記録(再送で再適用可能)
        self.assertFalse(
            BridgeProcessedOperation.objects.filter(op_id="op-5").exists()
        )

    def test_op_idempotency_across_messages(self):
        op = {
            "op_id": "dup-op", "action": "add_comment",
            "progress_id": self.progress.id, "content_zh": "a", "content_ja": "あ",
        }
        r1 = self._apply([op], nonce="msg-1")
        r2 = self._apply([op], nonce="msg-2")  # 別メール・同じop_id
        self.assertEqual(r1["applied"], ["dup-op"])
        self.assertEqual(r2["skipped"], ["dup-op"])
        self.assertEqual(SupervisorComment.objects.count(), 1)

    def test_message_replay_rejected(self):
        op = {
            "op_id": "op-x", "action": "add_comment",
            "progress_id": self.progress.id, "content_zh": "a", "content_ja": "あ",
        }
        self._apply([op], nonce="same-nonce")
        r2 = self._apply([op], nonce="same-nonce")  # 同一nonce再送
        self.assertIn("重複", r2["reason"])
        self.assertEqual(SupervisorComment.objects.count(), 1)
        self.assertEqual(
            BridgeProcessedMessage.objects.filter(nonce="same-nonce").count(), 1
        )

    def test_bad_signature_rejected(self):
        p = make_payload(
            [{"op_id": "z", "action": "add_comment", "progress_id": self.progress.id,
              "content_zh": "a", "content_ja": "あ"}]
        )
        res = inbound.apply_writeback(p, "deadbeef", sender=SENDER)
        self.assertFalse(res["ok"])
        self.assertIn("署名", res["reason"])
        self.assertEqual(SupervisorComment.objects.count(), 0)

    def test_sender_not_allowed_rejected(self):
        res = self._apply(
            [{"op_id": "z2", "action": "add_comment", "progress_id": self.progress.id,
              "content_zh": "a", "content_ja": "あ"}],
            sender="attacker@evil.com",
        )
        self.assertFalse(res["ok"])
        self.assertIn("差出人", res["reason"])

    def test_end_to_end_via_text(self):
        p = make_payload(
            [{"op_id": "e2e", "action": "add_comment", "progress_id": self.progress.id,
              "content_zh": "文本", "content_ja": "本文"}],
            nonce="e2e-nonce",
        )
        text = payload.wrap_writeback(p, signed(p))
        res = inbound.apply_writeback_text(text, sender=SENDER)
        self.assertTrue(res["ok"])
        self.assertEqual(SupervisorComment.objects.get().content_ja, "本文")


class OutboundSnapshotTests(TestCase):
    def test_snapshot_contains_ids_and_fields(self):
        task = Task.objects.create(title="任务A", client_name="客户X")
        prog = ProgressUpdate.objects.create(task=task, content="进展")
        SupervisorComment.objects.create(
            progress=prog, content="评论", content_ja="コメント"
        )

        snap = outbound.build_snapshot()
        self.assertEqual(snap["schema"], payload.SCHEMA_VERSION)
        self.assertEqual(len(snap["tasks"]), 1)
        t = snap["tasks"][0]
        self.assertEqual(t["id"], task.id)
        self.assertEqual(t["title"], "任务A")
        self.assertEqual(t["progress_updates"][0]["id"], prog.id)
        self.assertEqual(
            t["progress_updates"][0]["comments"][0]["content_ja"], "コメント"
        )

    def test_cancelled_task_excluded(self):
        Task.objects.create(title="取消", is_cancelled=True)
        snap = outbound.build_snapshot()
        self.assertEqual(len(snap["tasks"]), 0)

    def test_schema_version_is_v2(self):
        """v2 への昇格を明示的に確認。"""
        self.assertEqual(payload.SCHEMA_VERSION, 2)
        snap = outbound.build_snapshot()
        self.assertEqual(snap["schema"], 2)

    def test_snapshot_includes_assignee_candidates(self):
        """Mac 側の担当者ドロップダウン用に meta.assignees が含まれる。
        is_active=True かつ非 superuser だけが対象。"""
        User.objects.create_user(
            email="active@ngls.sh.cn", password="x",
            last_name="活動", first_name="太郎",
        )
        User.objects.create_user(
            email="staff@ngls.sh.cn", password="x", is_staff=True,
            last_name="上長", first_name="花子",
        )
        # 除外されるべきユーザー
        User.objects.create_user(
            email="su@ngls.sh.cn", password="x",
            is_staff=True, is_superuser=True,
        )
        inactive = User.objects.create_user(
            email="inactive@ngls.sh.cn", password="x",
        )
        inactive.is_active = False
        inactive.save(update_fields=["is_active"])

        snap = outbound.build_snapshot()
        self.assertIn("meta", snap)
        self.assertIn("assignees", snap["meta"])
        emails = {a["email"] for a in snap["meta"]["assignees"]}
        self.assertIn("active@ngls.sh.cn", emails)
        self.assertIn("staff@ngls.sh.cn", emails)
        self.assertNotIn("su@ngls.sh.cn", emails)
        self.assertNotIn("inactive@ngls.sh.cn", emails)
        # display_name / is_staff の形状を確認
        staff_entry = next(
            a for a in snap["meta"]["assignees"] if a["email"] == "staff@ngls.sh.cn"
        )
        self.assertEqual(staff_entry["display_name"], "上長 花子")
        self.assertTrue(staff_entry["is_staff"])


class SchemaCompatibilityTests(TestCase):
    """復路の schema 受け入れ範囲(v1/v2)を確認。"""

    @override_settings(
        CS_BRIDGE_HMAC_SECRET=SECRET,
        CS_BRIDGE_ALLOWED_SENDERS=[SENDER],
        CS_BRIDGE_AUTHOR_EMAIL="boss@ngls.sh.cn",
    )
    def test_v1_writeback_still_accepted(self):
        User.objects.create_user(email="boss@ngls.sh.cn", password="x", is_staff=True)
        task = Task.objects.create(title="t", client_name="c")
        prog = ProgressUpdate.objects.create(task=task, content="x")
        p = {
            "schema": 1,  # 旧バージョンの書き戻し
            "nonce": "v1-nonce",
            "issued_at": "2026-06-01T18:30:00+09:00",
            "ops": [{
                "op_id": "v1-op", "action": "add_comment",
                "progress_id": prog.id,
                "content_zh": "a", "content_ja": "あ",
            }],
        }
        res = inbound.apply_writeback(p, signed(p), sender=SENDER)
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["applied"], ["v1-op"])

    @override_settings(CS_BRIDGE_HMAC_SECRET=SECRET, CS_BRIDGE_ALLOWED_SENDERS=[SENDER])
    def test_unknown_schema_rejected(self):
        p = {"schema": 99, "nonce": "n", "issued_at": "x", "ops": []}
        res = inbound.apply_writeback(p, signed(p), sender=SENDER)
        self.assertFalse(res["ok"])
        self.assertIn("schema", res["reason"])


class LangDetectTests(TestCase):
    """入力テキストの言語自動判定（社内側でのフィールド振り分け用）。"""

    def test_zh_or_other_defaults_to_zh(self):
        self.assertEqual(_detect_lang("中文文本"), "zh")
        self.assertEqual(_detect_lang("Hello"), "zh")
        self.assertEqual(_detect_lang(""), "zh")
        self.assertEqual(_detect_lang(None), "zh")

    def test_hiragana_is_ja(self):
        self.assertEqual(_detect_lang("日本語のテキスト"), "ja")
        self.assertEqual(_detect_lang("これ"), "ja")
        self.assertEqual(_detect_lang("漢字とひらがな"), "ja")

    def test_katakana_is_ja(self):
        self.assertEqual(_detect_lang("カタカナ"), "ja")
        self.assertEqual(_detect_lang("ハロー"), "ja")
        self.assertEqual(_detect_lang("漢字とカタカナ"), "ja")

    def test_kanji_only_defaults_to_zh(self):
        # 漢字のみは中文扱い（簡易ヒューリスティック・JA との曖昧性は受け入れ）
        self.assertEqual(_detect_lang("漢字"), "zh")
        self.assertEqual(_detect_lang("文書管理"), "zh")


class RouteTextTests(TestCase):
    """検出言語に応じて (zh, ja) タプルに振り分け、逆側を空にする。"""

    def test_zh_to_primary_clears_ja(self):
        zh, ja = _route_text("中文内容")
        self.assertEqual(zh, "中文内容")
        self.assertEqual(ja, "")

    def test_ja_to_ja_clears_primary(self):
        zh, ja = _route_text("日本語の内容")
        self.assertEqual(zh, "")
        self.assertEqual(ja, "日本語の内容")
