from django import forms
from django.contrib.auth.forms import AuthenticationForm

class EmailAuthenticationForm(AuthenticationForm):
    # 内部的なフィールド名は username のままだけど、
    # フォーム上は「メールアドレス」として扱う
    username = forms.EmailField(
        label='メールアドレス',
        widget=forms.EmailInput(attrs={'autofocus': True})
    )
    