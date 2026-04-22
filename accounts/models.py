from django.db import models
from django.contrib.auth.models import AbstractUser

class User(AbstractUser):
    ROLE_CHOICES = (
         ('OWNER','Owner'),
         ('STAFF','Staff')

    )
    role = models.CharField(max_length=20, choices = ROLE_CHOICES, default = 'user' )
    full_name = models.CharField(max_length=255, blank=True, null=True)


class ActivityLog(models.Model):
    username = models.CharField(max_length=150)  # Store the username of the user performing the action
    action = models.CharField(max_length=255)
    action_details = models.TextField(blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

