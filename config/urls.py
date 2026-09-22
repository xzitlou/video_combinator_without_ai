from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import include, path

from combinator.views import signup

urlpatterns = [
    path("admin/", admin.site.urls),
    path("django-rq/", include("django_rq.urls")),
    path("login/", auth_views.LoginView.as_view(redirect_authenticated_user=True), name="login"),
    path("logout/", auth_views.LogoutView.as_view(), name="logout"),
    path("signup/", signup, name="signup"),
    path("", include("combinator.urls")),
]
