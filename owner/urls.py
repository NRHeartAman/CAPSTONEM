from django.urls import path
from . import views

urlpatterns = [
    # Dashboard
    path('owner/',                               views.owner_dashboard_view,      name='owner-dashboard'),
    path('owner/api/dashboard-stats/',           views.owner_dashboard_stats_api, name='owner-dashboard-stats-api'),

    # Inventory
    path('owner/inventory/',                     views.inventory_view,            name='owner-inventory'),

    # Upload Data (combined with Sales Trends)
    path('owner/upload/',                        views.upload_view,               name='view-upload-data'),

    # Settings
    path('owner/settings/',                      views.settings_view,             name='view-settings'),

    # Admin / user management
    path('owner/admin/',                         views.admin_management_view,     name='admin-management'),

    # Staff approval email link
    path('owner/approve-staff/<uuid:token>/',    views.approve_staff_account,     name='approve-staff'),
    path('set-password/<uuid:token>/', views.set_initial_password, name='set_initial_password'),

    # Approvals
    path('owner/approvals/',                     views.approvals_view,            name='view-approvals'),
    path('owner/approvals/inventory/<int:pk>/',  views.approve_inventory_request, name='approve-inventory-request'),
    path('owner/approvals/event/<int:pk>/',      views.approve_event_request,     name='approve-event-request'),

    # Employees
    path('owner/employees/',                     views.employees_view,            name='view-employees'),
    path('owner/employees/<int:pk>/',            views.employee_edit,             name='employee-edit'),
    path('owner/approvals/csv/<int:pk>/', views.approve_sales_upload, name='approve-sales-upload'),
    path('owner/events/archive/<int:pk>/', views.toggle_archive_event, name='toggle-archive-event'),
]