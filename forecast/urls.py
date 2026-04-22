from django.urls import path
from . import views
urlpatterns = [
    path('',views.forecast_view, name= 'view-forecast'),
    path('predict-api/', views.get_prediction_api, name='predict-api'),

]
