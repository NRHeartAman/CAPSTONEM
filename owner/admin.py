from django.contrib import admin
from .models import SystemSetting, InventoryRequest, EventRequest, StaffInvite, OwnerEvent

admin.site.register(SystemSetting)
admin.site.register(InventoryRequest)
admin.site.register(EventRequest)
admin.site.register(StaffInvite)
admin.site.register(OwnerEvent)