from django.urls import path
from . import views
urlpatterns = [
    path('',views.forecast_view, name= 'view-forecast'),
    path('predicted/', views.predicted_demand_view, name='view-forecast-predicted'),
    path('results/', views.forecast_results_view, name='view-forecast-results'),
    path('predict-api/', views.get_prediction_api, name='predict-api'),
    path('monthly-api/', views.get_monthly_forecast_api, name='monthly-forecast-api'),
    path('weather-api/', views.weather_forecast_proxy, name='weather-forecast-proxy'),
    path('weather-geo-api/', views.weather_geo_reverse_proxy, name='weather-geo-proxy'),

]
