from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('staff', '0004_delete_event'),
    ]

    operations = [
        migrations.AlterField(
            model_name='preptask',
            name='source',
            field=models.CharField(
                choices=[
                    ('manual', 'Manual / Seeded'),
                    ('low_stock', 'Auto – Low Stock'),
                    ('best_seller', 'Auto – Best Seller'),
                    ('forecast', "Auto – Tomorrow's Outlook"),
                ],
                default='manual',
                max_length=20,
            ),
        ),
    ]
