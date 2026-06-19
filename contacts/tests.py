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
        self.assertEqual(suzuki["kind_label"], "両方")
        self.assertEqual(suzuki["title"], "部長")        # 最新日の役職
        self.assertEqual(str(suzuki["last_date"]), "2026-01-15")

    def test_excludes_cancelled_and_nameless(self):
        r = self.client.get(reverse("contacts:index"))
        body = r.content.decode()
        self.assertNotIn("除外株式会社", body)
        self.assertNotIn("社名のみ株式会社", body)
