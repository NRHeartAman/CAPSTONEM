# owner/migrations/0003_staffinvite.py

from django.db import migrations, models
import django.db.models.deletion  # ── Added this explicit import to stop the NameError
import uuid

class Migration(migrations.Migration):

    dependencies = [
        ('owner', '0002_systemsetting_store_lat_systemsetting_store_lon'),
    ]

    operations = [
        migrations.CreateModel(
            name='StaffInvite',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token', models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('approved', models.BooleanField(default=False)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='invite', to='accounts.user')),
            ],
        ),
    ]