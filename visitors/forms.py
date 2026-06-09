from django import forms
from django.utils.translation import gettext_lazy as _l

TIME_CHOICES = [('', '---------')] + [
    (f'{h:02}:{m:02}', f'{h:02}:{m:02}') for h in range(0, 24) for m in (0, 15, 30, 45) ]

class VisitorForm(forms.Form):
    visit_date = forms.DateField(
        label=_l('訪問日'),
        required=False,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'hidden-date-input',
                'style': 'display:none;',
            }
        )
    )
    visit_time = forms.TimeField(
        label=_l('訪問時間'),
        required=False,
        widget=forms.Select(
            choices=TIME_CHOICES,
            attrs={
                'class': 'hidden-time-input',
                'style': 'display:none',
            }
        )
    )
    time_undecided = forms.BooleanField(
        label=_l('時間未定'), required=False
    )
    company_name = forms.CharField(
        label=_l('会社名'),
        required=False,
        widget=forms.TextInput(
            attrs={
                'class': 'company-input',
            }
        )
    )
    last_name = forms.CharField(label=_l('姓'), required=False)
    first_name = forms.CharField(label=_l('名'), required=False)
    title = forms.CharField(label=_l('役職'), required=False)
    purpose = forms.CharField(label=_l('目的'), required=False)
    location = forms.CharField(label=_l('場所'), required=False)
    host_staff = forms.CharField(label=_l('担当者'), required=False)

    def clean(self):
        cleaned = super().clean()

        # -------------------------
        # 1) 完全に空の行かどうかを判定
        # -------------------------
        fields_for_empty_check = [
            "visit_date",
            "visit_time",
            "time_undecided",
            "company_name",
            "last_name",
            "first_name",
            "title",
            "purpose",
            "location",
            "host_staff",
        ]

        is_empty_row = True
        for name in fields_for_empty_check:
            val = cleaned.get(name)
            # bool は True だけ「入力あり」とみなす
            if isinstance(val, bool):
                if val:            # True のときは入力あり
                    is_empty_row = False
                    break
            else:
                if val not in (None, ""):
                    is_empty_row = False
                    break

        # → 何も入力していない完全な空行は「無視対象」
        #   （time_undecided だけ True の行はここで空行にならない → 必須チェック対象になる）
        if is_empty_row:
            cleaned["__ignore_row__"] = True
            return cleaned

        # -------------------------
        # 2) 必須項目チェック
        # -------------------------
        errors = {}

        # 必須にしたい項目だけ列挙（役職・備考は除外）
        required_fields = [
            "visit_date",
            "company_name",
            "last_name",
            "first_name",
            "purpose",
            "location",
        ]

        for field in required_fields:
            if not cleaned.get(field):
                # メッセージ自体は何でも良い（テンプレートでは表示しない）
                errors[field] = "required"

        # 時間：未定でない場合は visit_time 必須
        time_undecided = cleaned.get("time_undecided")
        if not time_undecided and not cleaned.get("visit_time"):
            errors["visit_time"] = "required"

        # ※「未定だけチェックして他が空」の行は is_empty_row=False になるので、
        #    上の必須チェックで company_name 等にエラーが付く → 赤枠対象になる。

        if errors:
            # 各フィールドにエラーを紐付け（メッセージは使わない）
            for field, msg in errors.items():
                self.add_error(field, msg)
            # non-field error を追加して、is_valid() を False にする
            raise forms.ValidationError("invalid row")

        return cleaned
