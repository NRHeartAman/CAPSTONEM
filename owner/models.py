from django.db import models

class SystemSetting(models.Model):
    store_name = models.CharField(max_length=255, default="CraveCast Binangonan")
    contact_number = models.CharField(max_length=20, default="09123456789")
    stock_threshold = models.IntegerField(default=20)
    weather_api_key = models.CharField(max_length=255, blank=True, null=True)
    
    FORECAST_MODES = [
        ('Standard', 'Standard'),
        ('Aggressive', 'Aggressive'),
        ('Conservative', 'Conservative'),
    ]
    forecast_mode = models.CharField(max_length=20, choices=FORECAST_MODES, default='Standard')

    # Add these
    store_lat = models.FloatField(default=14.4667)
    store_lon = models.FloatField(default=121.1833)

    class Meta:
        verbose_name = "System Configuration"
        verbose_name_plural = "System Configuration"

    def __str__(self):
        return self.store_name