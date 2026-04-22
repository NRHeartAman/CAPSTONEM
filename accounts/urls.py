from django.urls import path
from .views import *
from django.contrib.auth import views as auth_views
from . import views
urlpatterns = [
    path('login/', login_view, name='login'),
    path('logout/', auth_views.LogoutView.as_view(), name='logout'),
    path('', auth_page, name='auth'),
    path('admin_management/', admin_management_view, name='admin-management'),
]
