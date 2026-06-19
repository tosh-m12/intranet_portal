from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from visitors.models import Visitor
from meetings.models import Meeting

User = get_user_model()


class AddVisitorCandidatesTests(TestCase):
    """新規登録フォームの入力候補(オートコンプリート)が、来客+訪問の
    クリーニング済みデータから組まれて埋め込まれることを検証する。"""

    def setUp(self):
        self.user = User.objects.create_user(email="u@ngls.sh.cn", password="x")
        self.client.force_login(self.user)
        Visitor.objects.create(
            visit_date="2026-06-19", company_name="北陸（上海）国際貿易有限公司",
            last_name="山田", first_name="太郎", title="部長",
            location="本社", host_staff="me", cancelled=False)
        Visitor.objects.create(  # キャンセルは候補に出さない
            visit_date="2026-06-19", company_name="除外株式会社",
            last_name="影", first_name="無", location="x", host_staff="me",
            cancelled=True)
        Meeting.objects.create(
            visit_date="2026-06-19", company_name="朝日電器株式会社",
            last_name="鈴木", first_name="花子", title="課長",
            location="WEB", host_staff="me", cancelled=False)

    def test_candidates_embedded(self):
        r = self.client.get(reverse("visitors:add_visitor"))
        self.assertEqual(r.status_code, 200)
        body = r.content.decode()
        # datalist は廃止し、自前サジェスト用の埋め込みJSON + ac-field 方式
        self.assertNotIn("datalist", body)
        self.assertIn('id="contacts-data"', body)
        self.assertIn('class="company-input ac-field"', body)  # 入力欄に ac-field
        # 来客・訪問の会社名・役職・姓が候補データ(JSON)に含まれる
        self.assertIn("北陸（上海）国際貿易有限公司", body)
        self.assertIn("朝日電器株式会社", body)
        self.assertIn("部長", body)
        self.assertIn("課長", body)
        self.assertIn("山田", body)

    def test_cancelled_excluded(self):
        r = self.client.get(reverse("visitors:add_visitor"))
        body = r.content.decode()
        self.assertNotIn("除外株式会社", body)
