from django.urls import path
from django.contrib.auth import views
from . import views 
from .views import *


urlpatterns = [
    path('staff/',views.staff_dashboard_view, name ='staff-dashboard'),
    path('staff/events/',views.events_view, name='view-events'),
]
