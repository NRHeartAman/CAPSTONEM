from django.db import models

class SalesRecord(models.Model):
    product_name = models.CharField(max_length=255)
    quantity = models.IntegerField()
    price = models.DecimalField(max_digits=10, decimal_places=2)
    sale_date = models.DateField()
    temp_c = models.FloatField(default=30.0) 
    uploaded_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.product_name} - {self.sale_date}"