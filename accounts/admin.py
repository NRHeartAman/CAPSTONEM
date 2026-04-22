from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User

class CustomUserAdmin(UserAdmin):
    model = User
    list_display = ['username', 'role', 'is_staff', 'full_name']
    
    fieldsets = UserAdmin.fieldsets + (
        ('Role Management', {'fields': ('role',)}),
    )

    add_fieldsets = UserAdmin.add_fieldsets + (
        ('Role Management', {'fields': ('role',)}),
    )

admin.site.register(User, CustomUserAdmin)