from django.urls import path
from . import views, recipe_views

urlpatterns = [
    path('inventory/',                        views.inventory_view,            name='view-inventory'),
    path('inventory/staff-request/',          views.staff_inventory_request,   name='staff-inventory-request'),  # ← NEW

    path('inventory/recipes/',                recipe_views.recipe_list,             name='recipe_list'),
    path('inventory/recipes/add/',            recipe_views.recipe_add,              name='recipe_add'),
    path('inventory/recipes/<int:pk>/edit/',  recipe_views.recipe_edit,             name='recipe_edit'),
    path('inventory/recipes/<int:pk>/delete/',recipe_views.recipe_delete,           name='recipe_delete'),
    path('inventory/export/', views.export_sales_csv, name='export-sales-csv'),
]