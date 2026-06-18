from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from visitors.models import Visitor
from meetings.models import Meeting

User = get_user_model()


class AddMeetingCandidatesTests(TestCase):
    """訪問の新規登録フォームの入力候補が、訪問+来客のクリーニング済み
    データから組まれて埋め込まれることを検証する。"""

    def setUp(self):
        self.user = User.objects.create_user(email="m@ngls.sh.cn", password="x")
        self.client.force_login(self.user)
        Meeting.objects.create(
            visit_date="2026-06-19", company_name="朝日電器株式会社",
            last_name="鈴木", first_name="花子", title="課長",
            location="WEB", host_staff="me", cancelled=False)
        Visitor.objects.create(
            visit_date="2026-06-19", company_name="北陸（上海）国際貿易有限公司",
            last_name="山田", first_name="太郎", title="部長",
            location="本社", host_staff="me", cancelled=False)
        Meeting.objects.create(
            visit_date="2026-06-19", company_name="除外株式会社",
            last_name="影", first_name="無", location="x", host_staff="me",
            cancelled=True)

    def test_candidates_embedded(self):
        r = self.client.get(reverse("meetings:add_meeting"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        self.assertIn('id="dl-company"', body)
        self.assertIn("朝日電器株式会社", body)        # 訪問
        self.assertIn("北陸（上海）国際貿易有限公司", body)  # 来客も候補に含む
        self.assertIn("課長", body)
        self.assertIn("const CONTACTS", body)
        self.assertNotIn("除外株式会社", body)          # キャンセルは除外
