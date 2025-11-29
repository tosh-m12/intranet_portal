from django import forms
from django.contrib.auth.forms import AuthenticationForm

class EmailAuthenticationForm(AuthenticationForm):
    # 内部的なフィールド名は username のままだけど、
    # フォーム上は「メールアドレス」として扱う
    username = forms.EmailField(
        label='メールアドレス',
        widget=forms.EmailInput(attrs={'autofocus': True})
    )
    
class InviteUserForm(forms.Form):
    full_name = forms.CharField(label="氏名", max_length=100)
    email = forms.EmailField(label="社内メールアドレス")

class ResetPasswordForm(forms.Form):
    email = forms.EmailField(label="登録済みメールアドレス")