from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from datetime import date
from dateutil.relativedelta import relativedelta


class User(AbstractUser):
    ROLE_CHOICES = (
        ('OWNER', 'Owner'),
        ('STAFF', 'Staff'),
    )
    email       = models.EmailField(unique=True)
    role        = models.CharField(max_length=20, choices=ROLE_CHOICES, default='STAFF')
    full_name   = models.CharField(max_length=255, blank=True, null=True)
    can_export  = models.BooleanField(default=False)  # ← add this

    def __str__(self):
        return f"{self.username} ({self.role})"


class ActivityLog(models.Model):
    username       = models.CharField(max_length=150)
    action         = models.CharField(max_length=255)
    action_details = models.TextField(blank=True, null=True)
    timestamp      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.username} - {self.action} [{self.timestamp}]"


class EmployeeProfile(models.Model):
    CONTRACT_CHOICES = [
        ('6months', '6 Months'),
        ('1year',   '1 Year'),
        ('2years',  '2 Years'),
        ('regular', 'Regular'),
    ]
    user          = models.OneToOneField(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='profile')
    photo         = models.ImageField(upload_to='employee_photos/', blank=True, null=True)
    phone         = models.CharField(max_length=20, blank=True)
    address       = models.TextField(blank=True)
    date_hired    = models.DateField(null=True, blank=True)
    contract_type = models.CharField(max_length=20, choices=CONTRACT_CHOICES, default='6months')
    contract_end  = models.DateField(null=True, blank=True)

    def save(self, *args, **kwargs):
        if self.date_hired and self.contract_type != 'regular':
            mapping = {'6months': 6, '1year': 12, '2years': 24}
            months  = mapping.get(self.contract_type, 6)
            self.contract_end = self.date_hired + relativedelta(months=months)
        elif self.contract_type == 'regular':
            self.contract_end = None
        super().save(*args, **kwargs)

    @property
    def days_until_expiry(self):
        if not self.contract_end:
            return None
        return (self.contract_end - date.today()).days

    @property
    def contract_status(self):
        days = self.days_until_expiry
        if days is None:
            return 'regular'
        if days < 0:
            return 'expired'
        if days <= 30:
            return 'expiring_soon'
        return 'active'

    def __str__(self):
        return f"Profile of {self.user.username}"


class PasswordResetToken(models.Model):
    token      = models.CharField(max_length=200, unique=True)
    user_role  = models.CharField(max_length=20)
    user_id    = models.IntegerField()
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user_role}:{self.user_id} — {self.token[:12]}..."


class Notification(models.Model):
    TYPES = (
        ('out_of_stock', 'Out of Stock'),
        ('low_stock',    'Low Stock / Restock'),
        ('expired',      'Expired Stock'),
        ('expiring',     'Expiring Soon'),
        ('predicted_shortage', 'Predicted Shortage'),
        ('approval',     'Approval'),
        ('contract',     'Contract Expiring'),
        ('upload',       'CSV Upload'),
        ('general',      'General'),
    )
    user       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    notif_type = models.CharField(max_length=20, choices=TYPES, default='general')
    title      = models.CharField(max_length=200)
    message    = models.TextField()
    # Stable identifier for an auto-generated alert (e.g. "low_stock:14"), so the
    # generator can tell "already raised this" from "raise a new one" without
    # doing fuzzy title matching.
    key        = models.CharField(max_length=200, blank=True, db_index=True)
    # Where clicking this notification should take the user — set once at
    # creation time, when the generator actually knows what the alert is
    # about (which item, which request). Blank means "not clickable".
    link       = models.CharField(max_length=300, blank=True)
    is_read    = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.user.username} — {self.title}"