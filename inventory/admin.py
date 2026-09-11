from django.contrib import admin
from .models import Inventory, ProductRecipe, RecipeIngredient


admin.site.register(Inventory)
admin.site.register(ProductRecipe)
admin.site.register(RecipeIngredient)