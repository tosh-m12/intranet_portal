from django import forms
from django.contrib.auth.forms import AuthenticationForm

class EmailAuthenticationForm(AuthenticationForm):
    # 内部的なフィールド名は username のままだけど、
    # フォーム上は「メールアドレス」として扱う
    username = forms.EmailField(
        label='メールアドレス',
        widget=forms.EmailInput(attrs={'autofocus': True})
    )
    

class ResetPasswordForm(forms.Form):
    email = forms.EmailField(label="登録済みメールアドレス")

class UserCreateForm(forms.Form):
    last_name = forms.CharField(label="姓", max_length=150)
    first_name = forms.CharField(label="名", max_length=150)
    email = forms.EmailField(label="メールアドレス")