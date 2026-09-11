from django.contrib import admin
from .models import Inventory, ProductRecipe, RecipeIngredient, PrepTask


admin.site.register(Inventory)
admin.site.register(ProductRecipe)
admin.site.register(RecipeIngredient)


@admin.register(PrepTask)
class PrepTaskAdmin(admin.ModelAdmin):
    list_display   = ('title', 'priority', 'source', 'linked_item', 'date', 'is_done')
    list_filter    = ('priority', 'source', 'is_done', 'date')
    search_fields  = ('title', 'linked_item')
    ordering       = ('-date', '-priority')
    date_hierarchy = 'date'
    actions        = ['mark_done', 'mark_undone']

    @admin.action(description='Mark selected tasks as done')
    def mark_done(self, request, queryset):
        queryset.update(is_done=True)

    @admin.action(description='Mark selected tasks as not done')
    def mark_undone(self, request, queryset):
        queryset.update(is_done=False)