import json

from django.test import TestCase, override_settings
from django.urls import reverse

from cs_tasks.bridge import security
from visitors.models import Visitor
from opsbridge.models import OpsAuditLog, OpsProcessedMessage

API_TOKEN = "test-token"
SECRET = "test-secret"


@override_settings(
    CS_BRIDGE_API_TOKEN=API_TOKEN,
    CS_BRIDGE_HMAC_SECRET=SECRET,
    OPSBRIDGE_EXPORT_MODELS={"visitors.Visitor"},
    OPSBRIDGE_WRITEBACK_MODELS={
        "visitors.Visitor": {"company_name", "last_name", "first_name", "title"},
    },
)
class OpsExportApiTests(TestCase):
    def setUp(self):
        self.url = reverse("opsbridge:api_export")
        self.v = Visitor.objects.create(
            visit_date="2026-06-17", company_name="株式会社A",
            last_name="山", first_name="田", location="本社", host_staff="me")

    def _auth(self, token=API_TOKEN):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _post(self, body, token=API_TOKEN):
        return self.client.post(self.url, data=json.dumps(body),
                                content_type="application/json", **self._auth(token))

    def test_requires_token(self):
        self.assertEqual(self.client.post(self.url).status_code, 401)
        self.assertEqual(
            self._post({"model": "visitors.Visitor"}, token="x").status_code, 401)

    def test_export_returns_rows(self):
        r = self._post({"model": "visitors.Visitor",
                        "fields": ["id", "company_name", "last_name"]})
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["count"], 1)
        self.assertEqual(d["rows"][0]["company_name"], "株式会社A")

    def test_export_all_fields_when_unspecified(self):
        r = self._post({"model": "visitors.Visitor"})
        self.assertEqual(r.status_code, 200)
        self.assertIn("title", r.json()["schema"])

    def test_export_non_whitelisted_model_403(self):
        r = self._post({"model": "meetings.Meeting"})
        self.assertEqual(r.status_code, 403)

    def test_export_filters_equality(self):
        Visitor.objects.create(visit_date="2026-06-18", company_name="B社",
                               last_name="佐", first_name="藤", location="x", host_staff="me")
        r = self._post({"model": "visitors.Visitor", "fields": ["id"],
                        "filters": {"company_name": "B社"}})
        self.assertEqual(r.json()["count"], 1)

    def test_export_unknown_field_400(self):
        r = self._post({"model": "visitors.Visitor", "fields": ["nope"]})
        self.assertEqual(r.status_code, 400)

    def test_export_bad_filter_key_400(self):
        r = self._post({"model": "visitors.Visitor",
                        "filters": {"company_name__icontains": "A"}})
        self.assertEqual(r.status_code, 400)


@override_settings(
    CS_BRIDGE_API_TOKEN=API_TOKEN,
    CS_BRIDGE_HMAC_SECRET=SECRET,
    OPSBRIDGE_WRITEBACK_MODELS={
        "visitors.Visitor": {"company_name", "last_name", "first_name", "title"},
    },
)
class OpsWritebackApiTests(TestCase):
    def setUp(self):
        self.url = reverse("opsbridge:api_writeback")
        self.v = Visitor.objects.create(
            visit_date="2026-06-17", company_name="ｶﾌﾞｼｷｶﾞｲｼｬA",
            last_name="山", first_name="田", title="課長",
            location="本社", host_staff="me", cancelled=False)

    def _auth(self, token=API_TOKEN):
        return {"HTTP_AUTHORIZATION": f"Bearer {token}"}

    def _payload(self, updates, nonce="n1", dry_run=False):
        return {"nonce": nonce, "model": "visitors.Visitor",
                "dry_run": dry_run, "updates": updates}

    def _post(self, payload, signature=None, token=API_TOKEN):
        if signature is None:
            signature = security.sign(payload, secret=SECRET)
        body = {"payload": payload, "signature": signature}
        return self.client.post(self.url, data=json.dumps(body),
                                content_type="application/json", **self._auth(token))

    def test_requires_token(self):
        p = self._payload([{"pk": self.v.id, "fields": {"company_name": "株式会社A"}}])
        r = self._post(p, token="")
        self.assertEqual(r.status_code, 401)

    def test_bad_signature_400(self):
        p = self._payload([{"pk": self.v.id, "fields": {"company_name": "x"}}])
        r = self._post(p, signature="deadbeef")
        self.assertEqual(r.status_code, 400)
        self.v.refresh_from_db()
        self.assertEqual(self.v.company_name, "ｶﾌﾞｼｷｶﾞｲｼｬA")  # 不変

    def test_field_not_whitelisted_rejected(self):
        # cancelled は許可集合外 → 更新されない(errors に積まれる)
        r = self._post(self._payload([{"pk": self.v.id, "fields": {"cancelled": True}}]))
        self.assertEqual(r.status_code, 200)
        self.v.refresh_from_db()
        self.assertFalse(self.v.cancelled)
        self.assertTrue(r.json()["errors"])

    def test_dry_run_does_not_write(self):
        r = self._post(self._payload(
            [{"pk": self.v.id, "fields": {"company_name": "株式会社A"}}], dry_run=True))
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["dry_run"])
        self.assertTrue(r.json()["diffs"])
        self.v.refresh_from_db()
        self.assertEqual(self.v.company_name, "ｶﾌﾞｼｷｶﾞｲｼｬA")  # 書いていない
        self.assertEqual(OpsAuditLog.objects.count(), 0)
        self.assertEqual(OpsProcessedMessage.objects.count(), 0)

    def test_apply_writes_and_audits(self):
        r = self._post(self._payload(
            [{"pk": self.v.id, "fields": {"company_name": "株式会社A", "title": "部長"}}]))
        self.assertEqual(r.status_code, 200)
        self.v.refresh_from_db()
        self.assertEqual(self.v.company_name, "株式会社A")
        self.assertEqual(self.v.title, "部長")
        log = OpsAuditLog.objects.get()
        self.assertEqual(log.before_json["company_name"], "ｶﾌﾞｼｷｶﾞｲｼｬA")
        self.assertEqual(log.after_json["company_name"], "株式会社A")

    def test_no_change_is_skipped(self):
        r = self._post(self._payload(
            [{"pk": self.v.id, "fields": {"company_name": "ｶﾌﾞｼｷｶﾞｲｼｬA"}}]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(self.v.id, r.json()["skipped"])
        self.assertEqual(OpsAuditLog.objects.count(), 0)

    def test_idempotent_by_nonce(self):
        ups = [{"pk": self.v.id, "fields": {"company_name": "株式会社A"}}]
        r1 = self._post(self._payload(ups, nonce="dup"))
        r2 = self._post(self._payload(ups, nonce="dup"))  # 同 nonce 再送
        self.assertEqual(r1.status_code, 200)
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(OpsAuditLog.objects.count(), 1)  # 二重適用しない
