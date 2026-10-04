from django.db import models


class ForecastLog(models.Model):
    """
    A locked-in snapshot of what the ML engine predicted for a product on
    a given date — written once per (product, date) the first time a
    prediction for that date is made (see forecast/views.py::get_prediction_api),
    so the Forecast Results page can later compare it to what actually
    sold without ever rewriting history after the fact.
    """
    product_name  = models.CharField(max_length=255)
    target_date   = models.DateField()
    predicted_qty = models.FloatField()
    created_at    = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('product_name', 'target_date')
        ordering = ['-target_date']

    def __str__(self):
        return f"{self.product_name} — {self.target_date}: predicted {self.predicted_qty}"
