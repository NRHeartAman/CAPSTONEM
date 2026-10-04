"""
URL configuration for cravecast project.
"""
from django.contrib import admin
from django.conf import settings
from django.conf.urls.static import static
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

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)