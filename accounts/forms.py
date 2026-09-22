from django import forms
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

from .models import User


class EmailAuthenticationForm(AuthenticationForm):
    username = forms.EmailField(label="Correo")

    error_messages = {
        **AuthenticationForm.error_messages,
        "invalid_login": "Correo o contraseña incorrectos.",
    }


class SignupForm(UserCreationForm):
    class Meta:
        model = User
        fields = ("email",)
        labels = {"email": "Correo"}

    def clean_email(self):
        email = self.cleaned_data["email"].lower()
        if User.objects.filter(email__iexact=email).exists():
            raise forms.ValidationError("Ya existe una cuenta con este correo.")
        return email
