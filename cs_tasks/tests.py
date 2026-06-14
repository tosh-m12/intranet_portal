# cs_tasks/tests.py
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

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

    def test_set_closed_task_cascades(self):
        res = self._apply([{"op_id": "op-sc", "action": "set_closed",
                            "target": "task", "id": self.task.id, "closed": True}])
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["op-sc"])
        self.task.refresh_from_db(); self.progress.refresh_from_db()
        self.assertTrue(self.task.is_closed)
        self.assertTrue(self.progress.is_closed)   # 配下進捗も連動
        # 再開
        self._apply([{"op_id": "op-sc2", "action": "set_closed",
                      "target": "task", "id": self.task.id, "closed": False}], nonce="n2")
        self.task.refresh_from_db(); self.progress.refresh_from_db()
        self.assertFalse(self.task.is_closed)
        self.assertFalse(self.progress.is_closed)

    def test_set_closed_progress(self):
        res = self._apply([{"op_id": "op-pc", "action": "set_closed",
                            "target": "progress", "id": self.progress.id, "closed": True}])
        self.assertTrue(res["ok"])
        self.progress.refresh_from_db()
        self.assertTrue(self.progress.is_closed)

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

    def test_add_progress(self):
        before = self.task.updated_at
        res = self._apply(
            [{
                "op_id": "op-ap", "action": "add_progress",
                "task_id": self.task.id,
                "content_zh": "已发货", "content_ja": "出荷済み",
                "execution_date": "2026-05-20",
            }]
        )
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["op-ap"])
        p = ProgressUpdate.objects.filter(task=self.task).order_by("-id").first()
        self.assertEqual(p.content, "已发货")
        self.assertEqual(p.content_ja, "出荷済み")
        self.assertEqual(str(p.execution_date), "2026-05-20")
        self.assertEqual(p.author, self.boss)   # CS_BRIDGE_AUTHOR_EMAIL
        self.task.refresh_from_db()
        self.assertGreater(self.task.updated_at, before)  # 親 touch（往路差分に載る）

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

    def test_edit_progress_execution_date(self):
        from datetime import date
        res = self._apply(
            [{"op_id": "op-ed", "action": "edit_progress",
              "progress_id": self.progress.id, "execution_date": "2026-05-15"}]
        )
        self.assertTrue(res["ok"])
        self.progress.refresh_from_db()
        self.assertEqual(self.progress.execution_date, date(2026, 5, 15))

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

    def test_delete_progress_touches_parent_for_diff(self):
        # 子の物理削除は「往路スナップショットからの不在」でしか伝わらない。
        # 親 Task.updated_at を touch しないと差分(since)に課題が乗らず、Mac 側で
        # 削除済み進捗が“復活”する（C-1 回帰防止）。
        since = timezone.now()
        self._apply(
            [{"op_id": "op-dp2", "action": "delete",
              "target": "progress", "id": self.progress.id}]
        )
        snap = outbound.build_snapshot(since=since)
        self.assertIn(self.task.id, [t["id"] for t in snap["tasks"]])

    def test_delete_comment_touches_parent_for_diff(self):
        c = SupervisorComment.objects.create(
            progress=self.progress, author=self.boss, content="x"
        )
        since = timezone.now()
        self._apply(
            [{"op_id": "op-dc2", "action": "delete",
              "target": "comment", "id": c.id}]
        )
        snap = outbound.build_snapshot(since=since)
        self.assertIn(self.task.id, [t["id"] for t in snap["tasks"]])

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

    def test_add_progress_via_task_ref_same_payload(self):
        # 同一メール内で「課題追加→その課題に進捗追加」をチェーン（未送信新規課題向け）
        res = self._apply([
            {"op_id": "t-1", "action": "add_task",
             "fields": {"title_zh": "新任务", "title_ja": "新課題", "client_name": "C"}},
            {"op_id": "p-1", "action": "add_progress", "task_ref": "t-1",
             "content_zh": "已开始", "content_ja": "着手しました"},
        ])
        self.assertTrue(res["ok"])
        self.assertEqual(res["applied"], ["t-1", "p-1"])
        t = Task.objects.get(title="新任务")
        p = ProgressUpdate.objects.get(task=t)
        self.assertEqual(p.content, "已开始")
        self.assertEqual(p.content_ja, "着手しました")

    def test_add_progress_via_task_ref_cross_payload(self):
        # 別メールで先に課題追加→後から task_ref で進捗追加（result_task_id 永続解決）
        r1 = self._apply(
            [{"op_id": "t-2", "action": "add_task",
              "fields": {"title_zh": "任务X", "title_ja": "課題X"}}],
            nonce="n-a",
        )
        self.assertEqual(r1["applied"], ["t-2"])
        r2 = self._apply(
            [{"op_id": "p-2", "action": "add_progress", "task_ref": "t-2",
              "content_zh": "继续", "content_ja": "継続"}],
            nonce="n-b",
        )
        self.assertEqual(r2["applied"], ["p-2"])
        t = Task.objects.get(title="任务X")
        self.assertEqual(ProgressUpdate.objects.get(task=t).content_ja, "継続")

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
        # 監査用に受信本文原文と差出人が保存される（メール削除後も追跡可能）
        rec = BridgeProcessedMessage.objects.get(nonce="e2e-nonce")
        self.assertEqual(rec.raw_body, text)
        self.assertEqual(rec.sender, SENDER)


class ViewChildEditPropagationTests(TestCase):
    """社内UIで既存の進捗/コメントを編集すると、親課題が touch され
    差分スナップショット(since)に乗ること（C-2 回帰防止）。
    新規追加は子の created_at で自然に乗るため、ここでは編集経路を検証する。"""

    def setUp(self):
        self.boss = User.objects.create_user(
            email="boss2@ngls.sh.cn", password="x", is_staff=True
        )
        self.task = Task.objects.create(title="任务B", client_name="客户Y")
        self.progress = ProgressUpdate.objects.create(
            task=self.task, author=self.boss, content="原内容"
        )
        self.client.force_login(self.boss)

    def test_edit_progress_view_propagates_via_diff(self):
        since = timezone.now()
        resp = self.client.post(
            reverse("cs_tasks:edit_progress", args=[self.progress.id]),
            {"content": "修正後の内容"},
        )
        self.assertEqual(resp.status_code, 302)
        snap = outbound.build_snapshot(since=since)
        self.assertIn(self.task.id, [t["id"] for t in snap["tasks"]])

    def test_edit_comment_view_propagates_via_diff(self):
        c = SupervisorComment.objects.create(
            progress=self.progress, author=self.boss, content="旧コメント"
        )
        since = timezone.now()
        resp = self.client.post(
            reverse("cs_tasks:edit_comment", args=[c.id]),
            {"content": "新コメント"},
        )
        self.assertEqual(resp.status_code, 302)
        snap = outbound.build_snapshot(since=since)
        self.assertIn(self.task.id, [t["id"] for t in snap["tasks"]])


class ProgressDateDescriptionTests(TestCase):
    """実施日(execution_date)のカレンダー編集・既定当日、課題詳細のインライン編集。"""

    def setUp(self):
        self.boss = User.objects.create_user(
            email="boss3@ngls.sh.cn", password="x", is_staff=True
        )
        self.task = Task.objects.create(title="任务C", client_name="X")
        self.progress = ProgressUpdate.objects.create(
            task=self.task, author=self.boss, content="进展"
        )
        self.client.force_login(self.boss)

    def test_add_progress_explicit_and_default_date(self):
        from datetime import date
        self.client.post(reverse("cs_tasks:add_progress", args=[self.task.id]),
                         {"content": "明示日付の進捗", "execution_date": "2026-05-10"})
        p = ProgressUpdate.objects.filter(task=self.task).order_by("-id").first()
        self.assertEqual(p.execution_date, date(2026, 5, 10))
        # 日付未指定 → 当日が入る
        self.client.post(reverse("cs_tasks:add_progress", args=[self.task.id]),
                         {"content": "日付なしの進捗"})
        p2 = ProgressUpdate.objects.filter(task=self.task).order_by("-id").first()
        self.assertEqual(p2.execution_date, timezone.localdate())

    def test_edit_progress_date(self):
        from datetime import date
        self.client.post(reverse("cs_tasks:edit_progress_date", args=[self.progress.id]),
                         {"execution_date": "2026-04-01"})
        self.progress.refresh_from_db()
        self.assertEqual(self.progress.execution_date, date(2026, 4, 1))

    def test_effective_date_fallback(self):
        # execution_date 未設定なら created_at の日付にフォールバック
        self.assertIsNone(self.progress.execution_date)
        self.assertEqual(self.progress.effective_date, self.progress.created_at.date())

    def test_edit_description_bilingual(self):
        # 日本語入力 → description_ja に入り、中文側は空（_route_text 仕様）
        self.client.post(reverse("cs_tasks:edit_description", args=[self.task.id]),
                         {"description": "詳細テキスト"})
        self.task.refresh_from_db()
        self.assertEqual(self.task.description_ja, "詳細テキスト")
        self.assertEqual(self.task.description, "")


class CategoryTabTests(TestCase):
    """区分(既存顧客/新規顧客/部内)タブの絞り込みと、部内の顧客列非表示。"""

    def setUp(self):
        self.boss = User.objects.create_user(
            email="boss4@ngls.sh.cn", password="x", is_staff=True
        )
        self.client.force_login(self.boss)
        self.t_exist = Task.objects.create(title="既存", client_name="客A",
                                           category=Task.CATEGORY_EXISTING, assignee=self.boss)
        self.t_new = Task.objects.create(title="新規", client_name="客B",
                                         category=Task.CATEGORY_NEW, assignee=self.boss)
        self.t_int = Task.objects.create(title="部内", category=Task.CATEGORY_INTERNAL,
                                         assignee=self.boss)

    def _ids(self, resp):
        return {t.id for g in resp.context["groups"]
                for c in g["clients"] for t in c["tasks"]}

    def test_existing_tab_default(self):
        r = self.client.get(reverse("cs_tasks:index"))
        self.assertEqual(r.context["active_tab"], "existing")
        self.assertFalse(r.context["hide_client"])
        self.assertEqual(self._ids(r), {self.t_exist.id})

    def test_new_tab(self):
        r = self.client.get(reverse("cs_tasks:index") + "?cat=new")
        self.assertEqual(self._ids(r), {self.t_new.id})
        self.assertFalse(r.context["hide_client"])

    def test_internal_tab_hides_client(self):
        r = self.client.get(reverse("cs_tasks:index") + "?cat=internal")
        self.assertEqual(self._ids(r), {self.t_int.id})
        self.assertTrue(r.context["hide_client"])
        self.assertNotContains(r, '<th class="col-client">')

    def test_add_inline_sets_category_and_no_client_for_internal(self):
        self.client.post(reverse("cs_tasks:task_add_inline"),
                         {"cs_subj": "部内の新課題", "cs_cust": "無視される客", "category": "internal"})
        # 日本語タイトルは title_ja 側に入る（_route_text 仕様）
        t = Task.objects.filter(title_ja="部内の新課題").first()
        self.assertIsNotNone(t)
        self.assertEqual(t.category, Task.CATEGORY_INTERNAL)
        self.assertEqual(t.client_name, "")   # 部内は顧客名を持たない

    def test_snapshot_includes_category(self):
        snap = outbound.build_snapshot()
        cats = {t["id"]: t["category"] for t in snap["tasks"]}
        self.assertEqual(cats[self.t_int.id], "internal")
        self.assertEqual(cats[self.t_new.id], "new")


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

    def test_snapshot_includes_execution_date(self):
        from datetime import date
        t = Task.objects.create(title="任务")
        ProgressUpdate.objects.create(task=t, content="进展", execution_date=date(2026, 5, 20))
        snap = outbound.build_snapshot()
        p = snap["tasks"][0]["progress_updates"][0]
        self.assertEqual(p["execution_date"], "2026-05-20")

    def test_cancelled_task_excluded(self):
        Task.objects.create(title="取消", is_cancelled=True)
        snap = outbound.build_snapshot()
        self.assertEqual(len(snap["tasks"]), 0)

    def test_meta_active_task_ids_lists_all_active_even_in_diff(self):
        # 課題まるごとの中止を差分でも Mac に伝えるための全件IDリスト。
        # since で tasks を空に絞っても active_task_ids には現存課題が全件入る。
        keep = Task.objects.create(title="存続")
        cancelled = Task.objects.create(title="中止", is_cancelled=True)
        from django.utils import timezone as _tz
        from datetime import timedelta
        future = _tz.now() + timedelta(hours=1)  # 差分で tasks は空になる since
        snap = outbound.build_snapshot(since=future)
        self.assertEqual(len(snap["tasks"]), 0)               # 差分: 詳細は空
        ids = snap["meta"]["active_task_ids"]
        self.assertIn(keep.id, ids)                           # 現存は全件入る
        self.assertNotIn(cancelled.id, ids)                   # 中止は入らない

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

    def test_hiragana_is_ja(self):
        self.assertEqual(_detect_lang("日本語のテキスト"), "ja")
        self.assertEqual(_detect_lang("これ"), "ja")
        self.assertEqual(_detect_lang("漢字とひらがな"), "ja")

    def test_katakana_is_ja(self):
        self.assertEqual(_detect_lang("カタカナ"), "ja")
        self.assertEqual(_detect_lang("ハロー"), "ja")
        self.assertEqual(_detect_lang("漢字とカタカナ"), "ja")

    def test_simplified_chinese_is_zh(self):
        # 簡体字専用の字を 1 つでも含めば中文
        self.assertEqual(_detect_lang("这是中文"), "zh")       # 这
        self.assertEqual(_detect_lang("维护质量"), "zh")       # 维护质
        self.assertEqual(_detect_lang("请确认进度"), "zh")     # 请/进

    def test_kanji_only_japanese_is_ja(self):
        # C-4 是正: かな無し・簡体字専用字なしの漢字のみ日本語は ja に判定する
        # （旧実装は zh と誤判定し、Mac 側で誤再翻訳していた）
        self.assertEqual(_detect_lang("漢字"), "ja")
        self.assertEqual(_detect_lang("文書管理"), "ja")
        self.assertEqual(_detect_lang("確認中"), "ja")
        self.assertEqual(_detect_lang("対応完了"), "ja")

    def test_ascii_and_empty_default_to_ja(self):
        # 共通漢字のみ・英数・空は社内入力前提で ja に倒す
        self.assertEqual(_detect_lang("Hello"), "ja")
        self.assertEqual(_detect_lang(""), "ja")
        self.assertEqual(_detect_lang(None), "ja")


class RouteTextTests(TestCase):
    """検出言語に応じて (zh, ja) タプルに振り分け、逆側を空にする。"""

    def test_zh_to_primary_clears_ja(self):
        zh, ja = _route_text("这是中文内容")   # 这 が簡体字専用 → zh
        self.assertEqual(zh, "这是中文内容")
        self.assertEqual(ja, "")

    def test_ja_to_ja_clears_primary(self):
        zh, ja = _route_text("日本語の内容")
        self.assertEqual(zh, "")
        self.assertEqual(ja, "日本語の内容")


class AutoDeployGuardTests(TestCase):
    """自動デプロイ: 安全装置(緊急停止フラグ・クールダウン・detached HEAD)。

    git 実行は伴わない(ガード段階で早期 return することを確認)。
    """

    def setUp(self):
        from cs_tasks import scheduler as sch
        sch._last_deploy_at = None

    def test_disable_flag_blocks_check(self):
        from unittest.mock import patch
        from cs_tasks import scheduler as sch
        with patch("cs_tasks.scheduler.os.path.exists", return_value=True), \
             patch("cs_tasks.scheduler._git") as mock_git:
            sch._auto_deploy_check()
            mock_git.assert_not_called()

    def test_cooldown_blocks_check(self):
        import time as _t
        from unittest.mock import patch
        from cs_tasks import scheduler as sch
        sch._last_deploy_at = _t.monotonic()
        with patch("cs_tasks.scheduler.os.path.exists", return_value=False), \
             patch("cs_tasks.scheduler._git") as mock_git:
            sch._auto_deploy_check()
            mock_git.assert_not_called()

    def test_no_change_no_exit(self):
        """local == upstream の場合は exit しない。"""
        from unittest.mock import patch
        from cs_tasks import scheduler as sch

        def fake_git(args, cwd, timeout=30):
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                return "feature/cs-tasks"
            if args[0] == "fetch":
                return ""
            if args == ["rev-parse", "HEAD"]:
                return "abc12345abc12345abc12345abc12345abc12345"
            if args == ["rev-parse", "@{u}"]:
                return "abc12345abc12345abc12345abc12345abc12345"
            return ""

        with patch("cs_tasks.scheduler.os.path.exists", return_value=False), \
             patch("cs_tasks.scheduler._git", side_effect=fake_git), \
             patch("cs_tasks.scheduler.os._exit") as mock_exit:
            sch._auto_deploy_check()
            self.assertIsNone(sch._last_deploy_at)
            mock_exit.assert_not_called()

    def test_detached_head_skips(self):
        from unittest.mock import patch
        from cs_tasks import scheduler as sch

        def fake_git(args, cwd, timeout=30):
            if args[:2] == ["rev-parse", "--abbrev-ref"]:
                return "HEAD"
            return ""

        with patch("cs_tasks.scheduler.os.path.exists", return_value=False), \
             patch("cs_tasks.scheduler._git", side_effect=fake_git) as mock_git:
            sch._auto_deploy_check()
            calls = [c.args[0] for c in mock_git.call_args_list]
            self.assertNotIn(["fetch", "--quiet"], calls)


API_TOKEN = "unit-test-api-token"


@override_settings(
    CS_BRIDGE_HMAC_SECRET=SECRET,
    CS_BRIDGE_API_TOKEN=API_TOKEN,
    CS_BRIDGE_AUTHOR_EMAIL="boss@ngls.sh.cn",
)
class BridgeApiTests(TestCase):
    """リアルタイム連携API(sync/writeback)。契約はメール経路と同一、transportのみHTTP。"""

    def setUp(self):
        import json
        self.json = json
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
        self.sync_url = reverse("cs_tasks:bridge_api_sync")
        self.wb_url = reverse("cs_tasks:bridge_api_writeback")

    def _auth(self, token=API_TOKEN):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _post_wb(self, ops, nonce="nonce-1", token=API_TOKEN, signer=signed):
        p = make_payload(ops, nonce=nonce)
        body = {"payload": p, "signature": signer(p)}
        return self.client.post(
            self.wb_url, data=self.json.dumps(body),
            content_type="application/json", **self._auth(token),
        )

    # --- sync(往路) ---
    def test_sync_requires_token(self):
        self.assertEqual(self.client.get(self.sync_url).status_code, 401)
        self.assertEqual(
            self.client.get(self.sync_url, **self._auth("wrong")).status_code, 401
        )

    def test_sync_returns_snapshot(self):
        r = self.client.get(self.sync_url, **self._auth())
        self.assertEqual(r.status_code, 200)
        data = r.json()
        self.assertEqual(data["type"], "snapshot")
        self.assertEqual(data["schema"], payload.SCHEMA_VERSION)
        self.assertIn(self.task.id, data["meta"]["active_task_ids"])
        self.assertEqual(data["tasks"][0]["title"], "任务A")

    # --- 週報 ---
    def test_weekly_requires_token(self):
        self.assertEqual(
            self.client.get(reverse("cs_tasks:bridge_api_weekly")).status_code, 401)

    def test_weekly_returns_report(self):
        r = self.client.get(reverse("cs_tasks:bridge_api_weekly"), **self._auth())
        self.assertEqual(r.status_code, 200)
        d = r.json()
        for k in ("week_start", "week_end", "new_tasks", "progressed_tasks",
                  "completed_tasks", "overdue_tasks", "due_soon_tasks", "summary"):
            self.assertIn(k, d)
        self.assertIn("in_progress", d["summary"])
        # setUp の task は今週作成 → 当週新規に出る
        self.assertTrue(any(t["title"] == "任务A" for t in d["new_tasks"]))

    # --- writeback(復路) ---
    def test_writeback_requires_token(self):
        p = make_payload([{"op_id": "z", "action": "add_comment"}])
        r = self.client.post(
            self.wb_url,
            data=self.json.dumps({"payload": p, "signature": signed(p)}),
            content_type="application/json",
        )
        self.assertEqual(r.status_code, 401)

    def test_writeback_applies_op(self):
        r = self._post_wb([{
            "op_id": "op-1", "action": "add_comment",
            "progress_id": self.progress.id,
            "content_zh": "请尽快处理", "content_ja": "至急対応してください",
        }])
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["applied"], ["op-1"])
        c = SupervisorComment.objects.get()
        self.assertEqual(c.content_ja, "至急対応してください")
        self.assertEqual(c.author, self.boss)  # CS_BRIDGE_AUTHOR_EMAIL

    def test_writeback_bad_signature_rejected(self):
        p = make_payload([{"op_id": "op-x", "action": "add_comment",
                           "progress_id": self.progress.id, "content_zh": "x"}])
        r = self.client.post(
            self.wb_url,
            data=self.json.dumps({"payload": p, "signature": "deadbeef"}),
            content_type="application/json", **self._auth(),
        )
        self.assertEqual(r.status_code, 400)
        self.assertIn("署名", r.json()["reason"])
        self.assertFalse(SupervisorComment.objects.exists())

    def test_writeback_idempotent_by_nonce(self):
        ops = [{"op_id": "op-i", "action": "add_comment",
                "progress_id": self.progress.id,
                "content_zh": "请处理", "content_ja": "対応して"}]
        r1 = self._post_wb(ops, nonce="dup")
        r2 = self._post_wb(ops, nonce="dup")
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(SupervisorComment.objects.count(), 1)  # 二重適用しない
