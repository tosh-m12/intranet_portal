from django import forms
from django.contrib.auth.forms import AuthenticationForm
from django.utils.translation import gettext_lazy as _

class EmailAuthenticationForm(AuthenticationForm):
    # 内部的なフィールド名は username のままだけど、
    # フォーム上は「メールアドレス」として扱う
    username = forms.EmailField(
        label=_('メールアドレス'),
        widget=forms.EmailInput(attrs={'autofocus': True})
    )


class ResetPasswordForm(forms.Form):
    email = forms.EmailField(label=_("登録済みメールアドレス"))

class UserCreateForm(forms.Form):
    last_name = forms.CharField(label=_("姓"), max_length=150)
    first_name = forms.CharField(label=_("名"), max_length=150)
    email = forms.EmailField(label=_("メールアドレス"))