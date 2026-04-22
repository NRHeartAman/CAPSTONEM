from django.db import models

class Inventory(models.Model):
    item_name = models.CharField(max_length=255)
    total_stock = models.IntegerField()  # Initial stock
    stock_qty = models.IntegerField()    # Current stock
    unit = models.CharField(max_length=50) # kg, pcs, ml
    category = models.CharField(max_length=50, choices=[('Stock', 'Stock'), ('Supply', 'Supply')])
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.item_name