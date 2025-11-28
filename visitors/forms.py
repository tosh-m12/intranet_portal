from django import forms

TIME_CHOICES = [('', '---------')] + [
    (f'{h:02}:{m:02}', f'{h:02}:{m:02}') for h in range(0, 24) for m in (0, 15, 30, 45) ]

class VisitorForm(forms.Form):
    visit_date = forms.DateField(
        label='訪問日',
        required=True,
        widget=forms.DateInput(
            attrs={
                'type': 'date',
                'class': 'hidden-date-input',
                'style': 'display:none;',
            }
        )
    )
    visit_time = forms.TimeField(
        label='訪問時間',
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
        label='時間未定', required=False
    )
    company_name = forms.CharField(
        label='会社名',
        required=True,
        widget=forms.TextInput(
            attrs={
                'class': 'company-input',
            }
        )
    )
    last_name = forms.CharField(label='姓', required=True)
    first_name = forms.CharField(label='名', required=True)
    title = forms.CharField(label='役職', required=False)
    purpose = forms.CharField(label='目的', required=True)
    location = forms.CharField(label='場所', required=True)
    host_staff = forms.CharField(label='担当者', required=True)
    notes = forms.CharField(label='備考', required=False, widget=forms.TextInput())
