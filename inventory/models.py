from django.db import models
from django.conf import settings


class Inventory(models.Model):
    CATEGORY_CHOICES = [
        ('Stock',  'Ingredient (Stock)'),
        ('Supply', 'Supply'),
    ]

    item_name         = models.CharField(max_length=255)
    total_stock       = models.FloatField(default=0)
    stock_qty         = models.FloatField(default=0)
    unit              = models.CharField(max_length=50)
    category          = models.CharField(max_length=50, choices=CATEGORY_CHOICES, default='Stock')
    restock_threshold = models.FloatField(default=20)
    expiry_date       = models.DateField(null=True, blank=True)
    updated_at        = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.item_name


class InventoryAuditLog(models.Model):
    ACTION_CHOICES = [
        ('initial', 'Initial Entry'),
        ('restock', 'Restock'),
        ('deduct',  'Auto-Deduct'),
        ('waste',   'Waste / Spoilage'),
    ]

    inventory    = models.ForeignKey(
        Inventory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='audit_logs',
    )
    item_name    = models.CharField(max_length=255)
    action       = models.CharField(max_length=20, choices=ACTION_CHOICES)
    qty_change   = models.FloatField()
    unit         = models.CharField(max_length=50)
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    notes        = models.TextField(blank=True, null=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"[{self.action}] {self.item_name} — {self.qty_change} {self.unit}"


class ProductRecipe(models.Model):
    product_name = models.CharField(max_length=200, unique=True)
    notes        = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.product_name

    def can_make(self):
        lines = self.ingredients.select_related('inventory_item').all()
        if not lines.exists():
            return None
        possible = []
        for line in lines:
            inv = line.inventory_item
            if line.qty_per_serving <= 0:
                continue
            possible.append(int(inv.stock_qty // line.qty_per_serving))
        return min(possible) if possible else 0


class RecipeIngredient(models.Model):
    recipe          = models.ForeignKey(
        ProductRecipe, on_delete=models.CASCADE, related_name='ingredients'
    )
    inventory_item  = models.ForeignKey(
        Inventory, on_delete=models.PROTECT, related_name='used_in_recipes',
        limit_choices_to={'category': 'Stock'}
    )
    qty_per_serving = models.FloatField(help_text="Amount used per 1 cup/serving")

    class Meta:
        unique_together = ('recipe', 'inventory_item')

    def __str__(self):
        return (
            f"{self.recipe.product_name} → "
            f"{self.qty_per_serving} {self.inventory_item.unit} "
            f"{self.inventory_item.item_name}"
        )

