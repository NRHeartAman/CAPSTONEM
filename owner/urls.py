from django.urls import path
from . import views

urlpatterns = [
    path('owner/',views.owner_dashboard_view, name = 'owner-dashboard'),
    path('owner/settings/',views.settings_view, name = 'view-settings'),
    path('owner/sales_trends',views.sales_analytics_view, name = 'view-sales-trends'),
    path('owner/upload_data',views.upload_data_view, name = 'view-upload-data'),
]
