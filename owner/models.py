from django.db import models
import uuid


# =========================
# SYSTEM SETTINGS
# =========================

class SystemSetting(models.Model):
    store_name      = models.CharField(max_length=255, default="CraveCast Binangonan")
    contact_number  = models.CharField(max_length=20, default="09123456789")
    stock_threshold = models.IntegerField(default=20)
    weather_api_key = models.CharField(max_length=255, blank=True, null=True)

    FORECAST_MODES = [
        ('Standard',     'Standard'),
        ('Aggressive',   'Aggressive'),
        ('Conservative', 'Conservative'),
    ]
    forecast_mode = models.CharField(max_length=20, choices=FORECAST_MODES, default='Standard')

    store_lat = models.FloatField(default=14.4667)
    store_lon = models.FloatField(default=121.1833)

    class Meta:
        verbose_name        = "System Configuration"
        verbose_name_plural = "System Configuration"

    def __str__(self):
        return self.store_name
# =========================
# STAFF REQUESTS (pending approval)
# =========================

class InventoryRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]
    CATEGORY_CHOICES = [('Stock', 'Stock'), ('Supply', 'Supply')]

    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='inventory_requests'
    )
    item_name   = models.CharField(max_length=255)
    total_stock = models.IntegerField()
    stock_qty   = models.IntegerField()
    unit        = models.CharField(max_length=50)
    category    = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Stock')
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at  = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"[{self.status.upper()}] {self.item_name} by {self.requested_by.username}"


class EventRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='event_requests'
    )
    event_name  = models.CharField(max_length=255)
    event_date  = models.DateField()
    description = models.TextField(blank=True)
    status      = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at  = models.DateTimeField(auto_now_add=True)


# =========================
# STAFF INVITE (owner approval flow)
# =========================

class StaffInvite(models.Model):
    token      = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user       = models.OneToOneField(
                     'accounts.User',
                     on_delete=models.CASCADE,
                     related_name='invite'
                 )
    created_at = models.DateTimeField(auto_now_add=True)
    approved   = models.BooleanField(default=False)

    def __str__(self):
        return f"Invite for {self.user.username} ({'approved' if self.approved else 'pending'})"
    # =========================
# STAFF SALES UPLOAD REQUEST
# =========================

class SalesUploadRequest(models.Model):
    STATUS_CHOICES = [
        ('pending',  'Pending'),
        ('approved', 'Approved'),
        ('rejected', 'Rejected'),
    ]

    requested_by = models.ForeignKey(
        'accounts.User', on_delete=models.CASCADE, related_name='sales_upload_requests'
    )
    csv_file     = models.FileField(upload_to='sales_uploads/')
    original_filename = models.CharField(max_length=255, blank=True)
    status       = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    records_added = models.IntegerField(default=0)
    created_at   = models.DateTimeField(auto_now_add=True)
    reviewed_at  = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"[{self.status.upper()}] {self.original_filename} by {self.requested_by.username}"


# =========================
# OWNER EVENT MANAGER
# =========================

class OwnerEvent(models.Model):
    event_name  = models.CharField(max_length=255)
    event_date  = models.DateField()
    description = models.TextField(blank=True, null=True)
    is_archived = models.BooleanField(default=False)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name        = "Owner Event"
        verbose_name_plural = "Owner Events"
        ordering            = ['event_date']

    def __str__(self):
        return f"{self.event_name} — {self.event_date}"