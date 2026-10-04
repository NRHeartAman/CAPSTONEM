from django.urls import path
from . import views

urlpatterns = [
    path('staff/',                      views.staff_dashboard_view,        name='staff-dashboard'),
    path('staff/api/dashboard-stats/',  views.staff_dashboard_stats_api,   name='staff-dashboard-stats-api'),
    path('staff/events/',               views.events_view,                 name='view-events'),
    path('staff/request/inventory/',    views.staff_inventory_request_view, name='staff-inventory-request'),
    path('staff/request/event/',        views.staff_event_request_view,    name='staff-event-request'),
    path('prep/toggle/<int:pk>/',       views.toggle_prep_task,             name='toggle-prep-task'),
]