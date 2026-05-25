# cs_tasks/forms.py
from django import forms
from django.contrib.auth import get_user_model

from .models import Task

User = get_user_model()


class TaskForm(forms.ModelForm):
    class Meta:
        model = Task
        fields = ["title", "client_name", "assignee", "due_date", "description"]
        widgets = {
            "title": forms.TextInput(attrs={"class": "form-input", "maxlength": 28}),
            "client_name": forms.TextInput(attrs={"class": "form-input"}),
            "assignee": forms.Select(attrs={"class": "form-input"}),
            "due_date": forms.DateInput(
                attrs={"type": "date", "class": "form-input"}
            ),
            "description": forms.Textarea(attrs={"class": "form-input", "rows": 5}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["assignee"].queryset = User.objects.order_by(
            "last_name", "first_name", "email"
        )
        self.fields["assignee"].required = False
        self.fields["title"].required = True
