import math

from django.db import models
from django.conf import settings


def servings_from(stock, per_serving):
    """Whole servings makeable from `stock`. Plain `stock // per_serving`
    is off by one for decimal recipes (5 // 0.02 == 249.0 in floating
    point), so divide, allow a tiny rounding tolerance, then floor."""
    if per_serving <= 0:
        return 0
    return max(0, math.floor(stock / per_serving + 1e-9))


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
    # The date stock_qty was last set from a real count (new item, or the
    # owner correcting "Current stock"). Sales dated BEFORE this are already
    # reflected in the count, so uploads only auto-deduct sales on/after it —
    # uploading old history (to train the forecast) never touches today's stock.
    stock_as_of       = models.DateField(null=True, blank=True)
    updated_at        = models.DateTimeField(auto_now=True)

    PACKAGE_UNIT_CHOICES = [
        ('box',    'Box'),
        ('pack',   'Pack'),
        ('sachet', 'Sachet'),
        ('bottle', 'Bottle'),
        ('case',   'Case'),
        ('roll',   'Roll'),
        ('bundle', 'Bundle'),
        ('piece',  'Piece'),
    ]

    # ── Real purchasing / bulk-buying support ──
    # unit_cost: cost per base unit (e.g. per kg/L/pc) from the most recent
    # purchase — "last cost", not a blended average. Updated by add_stock().
    unit_cost    = models.FloatField(null=True, blank=True)
    # package_size: base units per purchasing package (e.g. 100 for a
    # 100mL box). Paired with package_unit so it reads as "100 mL per Box".
    package_size = models.FloatField(null=True, blank=True)
    package_unit = models.CharField(max_length=20, choices=PACKAGE_UNIT_CHOICES, blank=True, default='')
    # max_stock: optional ceiling used to avoid recommending overstocking.
    max_stock    = models.FloatField(null=True, blank=True)

    def __str__(self):
        return self.item_name

    @property
    def is_expired(self):
        from django.utils import timezone
        return bool(self.expiry_date and self.expiry_date < timezone.localdate())

    @property
    def usable_qty(self):
        """Stock that can actually go into drinks — expired stock can't."""
        return 0.0 if self.is_expired else float(self.stock_qty)

    @property
    def reorder_in_packages(self):
        """Restock threshold expressed in packages, when package_size is set."""
        if not self.package_size:
            return None
        return round(self.restock_threshold / self.package_size, 1)

    @property
    def stock_in_packages(self):
        """Current stock expressed in packages, when package_size is set."""
        if not self.package_size:
            return None
        return round(self.stock_qty / self.package_size, 1)


class InventoryAuditLog(models.Model):
    ACTION_CHOICES = [
        ('initial', 'Initial Entry'),
        ('restock', 'Restock'),
        ('deduct',  'Auto-Deduct'),
        ('waste',   'Waste / Spoilage'),
        ('count',   'Stock Count Correction'),
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

    # ── Purchase / cost ledger fields ──────────────────────────
    # For action='restock': supplier/packages/unit_cost describe the
    # purchase; total_cost = qty_change * unit_cost (what was paid).
    # For action='waste': total_cost is the *estimated loss* at the
    # item's unit_cost when the waste was logged. Owner-only to display.
    supplier   = models.CharField(max_length=200, blank=True, default='')
    packages   = models.FloatField(null=True, blank=True)
    unit_cost  = models.FloatField(null=True, blank=True)
    total_cost = models.FloatField(null=True, blank=True)

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
            possible.append(servings_from(inv.usable_qty, line.qty_per_serving))
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

