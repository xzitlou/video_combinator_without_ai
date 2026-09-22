from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name",)
        labels = {"name": "Nombre del proyecto"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Ej. Lanzamiento de septiembre", "autocomplete": "off"})}
