"""
URL configuration for cravecast project.
"""
from django.contrib import admin
from django.urls import path, include
from django.contrib.auth import views as auth_views

urlpatterns = [
    path('admin/',    admin.site.urls),
    path('logout/',   auth_views.LogoutView.as_view(), name='logout'),
    path('',          include('accounts.urls')),
    path('',          include('staff.urls')),
    path('',          include('inventory.urls')),
    path('forecast/', include('forecast.urls')),
    path('',          include('owner.urls')),
]