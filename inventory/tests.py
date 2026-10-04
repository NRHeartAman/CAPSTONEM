from django.test import TestCase, Client

from accounts.models import User
from inventory.models import Inventory, ProductRecipe, RecipeIngredient
from inventory.services import (
    deduct_inventory_for_sales, get_inventory_forecast, get_ingredient_demand_forecast,
)
from sales.models import SalesRecord


class InventoryToastTests(TestCase):
    """The bell icon and success/error icon were both being added — the
    view hardcoded a '✓ ' prefix on top of the toast template's own icon,
    rendering as a doubled checkmark."""

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')

    def test_add_item_message_has_no_hardcoded_icon(self):
        c = Client(); c.force_login(self.owner)
        r = c.post('/inventory/', {
            'form_type': 'add_entry', 'item_name': 'Milk', 'total_stock': '20',
            'unit': 'pcs', 'category': 'Stock', 'restock_threshold': '5',
            'unit_cost': '10', 'package_size': '1', 'package_unit': 'piece',
        }, follow=True)
        html = r.content.decode()
        self.assertNotIn('✓ ✓', html)
        self.assertIn('Milk added to inventory.', html)


class RecipeForecastTests(TestCase):
    """'Can we still make X right now' — the direction that was already
    working before this session's changes; kept as a regression check."""

    def setUp(self):
        self.milk = Inventory.objects.create(item_name='Milk', unit='ml', total_stock=100,
                                              stock_qty=100, restock_threshold=20, category='Stock')
        self.recipe = ProductRecipe.objects.create(product_name='Milk Tea')
        RecipeIngredient.objects.create(recipe=self.recipe, inventory_item=self.milk, qty_per_serving=25)

    def test_can_make_is_limited_by_lowest_ingredient(self):
        results = get_inventory_forecast()
        entry = next(r for r in results if r['product'] == 'Milk Tea')
        self.assertEqual(entry['can_make'], 4)  # 100 // 25
        self.assertEqual(entry['limiting_item'], 'Milk')


class IngredientDemandForecastTests(TestCase):
    """
    The new connection between Forecast (predicted units sold) and
    Recipes/Inventory (what those units would consume) — previously these
    were three unconnected systems.
    """

    def setUp(self):
        self.milk = Inventory.objects.create(item_name='Milk', unit='ml', total_stock=1000,
                                              stock_qty=100, restock_threshold=200, category='Stock')
        self.recipe = ProductRecipe.objects.create(product_name='Latte')
        RecipeIngredient.objects.create(recipe=self.recipe, inventory_item=self.milk, qty_per_serving=50)

    def test_not_enough_sales_history_degrades_gracefully(self):
        result = get_ingredient_demand_forecast(days_ahead=1)
        self.assertFalse(result['has_enough_data'])
        self.assertEqual(result['items'], [])

    def test_predicted_shortage_is_detected_and_never_shows_negative_remaining(self):
        from datetime import date, timedelta
        today = date.today()
        for i in range(6):
            SalesRecord.objects.create(
                product_name='Latte', sale_date=today - timedelta(days=i + 1),
                quantity=10, price=100, temp_c=28)

        result = get_ingredient_demand_forecast(days_ahead=1)
        self.assertTrue(result['has_enough_data'])
        milk_entry = next(i for i in result['items'] if i['item'] == 'Milk')
        self.assertEqual(milk_entry['status'], 'out')
        self.assertGreaterEqual(milk_entry['remaining_after'], 0)  # never negative for display


class DeductInventoryForSalesTests(TestCase):
    def setUp(self):
        self.milk = Inventory.objects.create(item_name='Milk', unit='ml', total_stock=100,
                                              stock_qty=100, restock_threshold=20, category='Stock')
        self.recipe = ProductRecipe.objects.create(product_name='Latte')
        RecipeIngredient.objects.create(recipe=self.recipe, inventory_item=self.milk, qty_per_serving=60)

    def test_deduction_floors_at_zero_never_goes_negative(self):
        sale = SalesRecord.objects.create(product_name='Latte', sale_date='2026-03-01',
                                           quantity=5, price=100, temp_c=28)  # needs 300ml, only 100 in stock
        deduct_inventory_for_sales([sale])
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 0.0)

    def test_low_stock_flag_uses_items_own_threshold(self):
        sale = SalesRecord.objects.create(product_name='Latte', sale_date='2026-03-01',
                                           quantity=1, price=100, temp_c=28)  # uses 60ml, leaves 40ml (below 20? no)
        summary = deduct_inventory_for_sales([sale])
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 40.0)
        self.assertEqual(len(summary['low_stock']), 0)  # 40 > threshold 20, not flagged

        sale2 = SalesRecord.objects.create(product_name='Latte', sale_date='2026-03-02',
                                            quantity=1, price=100, temp_c=28)  # -> 40-60 -> floors at 0
        summary2 = deduct_inventory_for_sales([sale2])
        self.assertEqual(len(summary2['low_stock']), 1)
