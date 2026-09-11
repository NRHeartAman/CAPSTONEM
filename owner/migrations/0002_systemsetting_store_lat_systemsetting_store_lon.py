# owner/migrations/0002_systemsetting_store_lat_systemsetting_store_lon.py

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        ('owner', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsetting',
            name='store_lat',
            field=models.FloatField(default=14.4667),
        ),
        migrations.AddField(
            model_name='systemsetting',
            name='store_lon',
            field=models.FloatField(default=121.1833),
        ),
    ]