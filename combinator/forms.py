from django import forms

from .models import Project


class ProjectForm(forms.ModelForm):
    class Meta:
        model = Project
        fields = ("name",)
        labels = {"name": "Nombre del lote"}
        widgets = {"name": forms.TextInput(attrs={"placeholder": "Nombre del lote, p. ej. Septiembre", "autofocus": True})}
