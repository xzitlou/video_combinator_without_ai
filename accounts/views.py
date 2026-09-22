from django.contrib.auth import login
from django.contrib.auth.views import LoginView
from django.shortcuts import redirect, render

from .forms import EmailAuthenticationForm, SignupForm


class EmailLoginView(LoginView):
    form_class = EmailAuthenticationForm
    redirect_authenticated_user = True


def signup(request):
    if request.user.is_authenticated:
        return redirect("combinator:project_list")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        login(request, form.save(), backend="django.contrib.auth.backends.ModelBackend")
        return redirect("combinator:project_list")
    return render(request, "registration/signup.html", {"form": form})
