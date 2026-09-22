from django import forms
from django.contrib.auth.forms import UserCreationForm
from django.contrib.auth.models import User

from .models import Project


class SignupForm(UserCreationForm):
    class Meta(UserCreationForm.Meta):
        model = User
        fields = ("username", "email")


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name",)
        labels = {"name": "Nombre de la campaña"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Campaña de septiembre", "autofocus": True})}
