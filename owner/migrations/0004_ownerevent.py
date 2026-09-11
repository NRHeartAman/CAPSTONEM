# owner/migrations/0004_ownerevent.py

from django.db import migrations, models

class Migration(migrations.Migration):

    dependencies = [
        # This fixes the NodeNotFoundError by pointing cleanly to file 0003
        ('owner', '0003_staffinvite'),  
    ]

    operations = [
        migrations.CreateModel(
            name='OwnerEvent',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_name', models.CharField(max_length=255)),
                ('event_date', models.DateField()),
                ('description', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
            options={
                'verbose_name': 'Owner Event',
                'verbose_name_plural': 'Owner Events',
                'ordering': ['event_date'],
            },
        ),
    ]