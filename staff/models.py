from django.db import models
from django.utils import timezone


class PrepTask(models.Model):
    PRIORITY_CHOICES = [
        ('high',   'High Priority'),
        ('normal', 'Normal'),
    ]
    SOURCE_CHOICES = [
        ('manual',      'Manual / Seeded'),
        ('low_stock',   'Auto – Low Stock'),
        ('best_seller', 'Auto – Best Seller'),
    ]

    title        = models.CharField(max_length=200)
    instructions = models.TextField(blank=True)
    priority     = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='normal')
    source       = models.CharField(max_length=20, choices=SOURCE_CHOICES, default='manual')
    linked_item  = models.CharField(max_length=200, blank=True, null=True)
    date         = models.DateField(default=timezone.localdate)
    is_done      = models.BooleanField(default=False)

    class Meta:
        ordering = ['-priority', 'title']
        unique_together = [('title', 'date')]

    def __str__(self):
        return f"[{self.date}] {self.title} ({self.get_priority_display()})"