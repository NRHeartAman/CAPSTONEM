from django.db import models

class Event(models.Model):
    event_name = models.CharField(max_length=255)
    event_date = models.DateField()
    description = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.event_name

    class Meta:
        ordering = ['event_date']