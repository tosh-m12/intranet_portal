from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from visitors.models import Visitor
from meetings.models import Meeting

User = get_user_model()


class ContactsDirectoryTests(TestCase):
    """相手先名簿: 来客+訪問を横断し、人で名寄せして一覧化することを検証。"""

    def setUp(self):
        self.user = User.objects.create_user(email="c@ngls.sh.cn", password="x")
        self.client.force_login(self.user)
        # 同一人物が来客1回・訪問1回 → 名寄せ1行・回数2・種別「両方」
        Visitor.objects.create(
            visit_date="2025-12-01", company_name="朝日電器株式会社",
            last_name="鈴木", first_name="花子", title="課長",
            location="本社", host_staff="me", cancelled=False)
        Meeting.objects.create(
            visit_date="2026-01-15", company_name="朝日電器株式会社",
            last_name="鈴木", first_name="花子", title="部長",  # 役職は最新(2026)を採用
            location="WEB", host_staff="me", cancelled=False)
        # 来客のみの別人
        Visitor.objects.create(
            visit_date="2025-11-10", company_name="北陸（上海）国際貿易有限公司",
            last_name="山田", first_name="太郎", title="社長",
            location="本社", host_staff="me", cancelled=False)
        # キャンセルは除外
        Meeting.objects.create(
            visit_date="2026-02-01", company_name="除外株式会社",
            last_name="影", first_name="無", location="x", host_staff="me",
            cancelled=True)
        # 名前の無い行は人一覧に出さない
        Visitor.objects.create(
            visit_date="2026-02-02", company_name="社名のみ株式会社",
            last_name="", first_name="", location="x", host_staff="me", cancelled=False)

    def test_aggregation(self):
        r = self.client.get(reverse("contacts:index"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        # 名寄せされた人が出る
        self.assertIn("鈴木", body)
        self.assertIn("山田", body)
        self.assertIn("北陸（上海）国際貿易有限公司", body)
        # 同一人物は1行(total=2)。鈴木+山田 のみ
        self.assertEqual(r.context["total"], 2)
        suzuki = [c for c in r.context["contacts"] if c["last"] == "鈴木"][0]
        self.assertEqual(suzuki["count"], 2)            # 来客+訪問=2回
        self.assertEqual(suzuki["title"], "部長")        # 最新日の役職
        # 会社名の昇順に並ぶ(朝日電器 < 北陸…)
        companies = [c["company"] for c in r.context["contacts"]]
        self.assertEqual(companies, sorted(companies))
        # 削除した列は表に出ない
        self.assertNotIn("種別", body)
        self.assertNotIn("最終面会日", body)

    def test_excludes_cancelled_and_nameless(self):
        r = self.client.get(reverse("contacts:index"))
        body = r.content.decode()
        self.assertNotIn("除外株式会社", body)
        self.assertNotIn("社名のみ株式会社", body)


class ContactsEditTests(TestCase):
    """相手先名簿のインライン編集(staffのみ・裏レコードへ一括反映)と同名フラグの検証。"""

    def setUp(self):
        self.url = reverse("contacts:inline_update")
        # 同一人物(山田 太郎)が英社名と日本語社名で別々に登録 → 2行・同名でなく同社別表記
        self.v = Visitor.objects.create(
            visit_date="2026-01-10", company_name="HORIBA",
            last_name="山田", first_name="太郎", title="部長",
            location="本社", host_staff="me", cancelled=False)
        self.m = Meeting.objects.create(
            visit_date="2026-01-12", company_name="HORIBA",
            last_name="山田", first_name="太郎", title="部長",
            location="WEB", host_staff="me", cancelled=False)
        # 別会社に同名(山田 太郎) → dup フラグ対象
        Meeting.objects.create(
            visit_date="2026-01-20", company_name="堀场（中国）贸易有限公司",
            last_name="山田", first_name="太郎", title="経理",
            location="WEB", host_staff="me", cancelled=False)

    def _body(self, **kw):
        import json
        base = {"company": "HORIBA", "last": "山田", "first": "太郎"}
        base.update(kw)
        return json.dumps(base)

    def test_non_staff_forbidden(self):
        u = User.objects.create_user(email="user@ngls.sh.cn", password="x")
        self.client.force_login(u)
        r = self.client.post(self.url, self._body(field="company_name", value="堀场（中国）贸易有限公司"),
                             content_type="application/json")
        self.assertEqual(r.status_code, 403)
        self.v.refresh_from_db()
        self.assertEqual(self.v.company_name, "HORIBA")  # 不変

    def test_staff_edit_propagates(self):
        staff = User.objects.create_user(email="staff@ngls.sh.cn", password="x", is_staff=True)
        self.client.force_login(staff)
        r = self.client.post(self.url, self._body(field="company_name", value="堀场（中国）贸易有限公司"),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertTrue(d["ok"])
        self.assertEqual(d["updated"], 2)               # Visitor+Meeting の HORIBA 2件
        self.v.refresh_from_db(); self.m.refresh_from_db()
        self.assertEqual(self.v.company_name, "堀场（中国）贸易有限公司")
        self.assertEqual(self.m.company_name, "堀场（中国）贸易有限公司")

    def test_field_whitelist(self):
        staff = User.objects.create_user(email="s2@ngls.sh.cn", password="x", is_staff=True)
        self.client.force_login(staff)
        r = self.client.post(self.url, self._body(field="cancelled", value="True"),
                             content_type="application/json")
        self.assertEqual(r.status_code, 400)

    def test_dup_flag_for_same_name(self):
        staff = User.objects.create_user(email="s3@ngls.sh.cn", password="x", is_staff=True)
        self.client.force_login(staff)
        r = self.client.get(reverse("contacts:index"))
        # 山田 太郎 が2社に出る → どちらの行も dup=True
        yamadas = [c for c in r.context["contacts"] if c["last"] == "山田"]
        self.assertTrue(len(yamadas) >= 2)
        self.assertTrue(all(c["dup"] for c in yamadas))
        self.assertTrue(r.context["can_edit"])          # staff は編集可
