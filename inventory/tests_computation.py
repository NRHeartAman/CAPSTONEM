"""
Exact-number checks for the sales and inventory math the owner relies on:
what an upload adds, what it deducts, and what the Restock Plan says to buy.
Every expected value here is worked out by hand in the comment beside it.
"""
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from inventory.models import Inventory, InventoryAuditLog, ProductRecipe, RecipeIngredient
from inventory.services import deduct_inventory_for_sales
from owner.models import InventoryRequest, SystemSetting
from sales.models import SalesRecord


def _csv(text):
    return SimpleUploadedFile('sales.csv', text.encode(), content_type='text/csv')


class SalesUploadComputationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o', email='o@x.com', password='x', role='OWNER')
        self.c = Client(); self.c.force_login(self.owner)

    def upload(self, text):
        return self.c.post('/owner/upload/', {'csv_file': _csv(text)}, follow=True)

    def test_rows_for_same_product_and_day_are_added_not_dropped(self):
        # Two transactions of Taro on Mar 1: 3 @ ₱110 + 2 @ ₱100
        self.upload('Date,Product Name,Quantity,Unit Price\n'
                    '2026-03-01,Taro Milk Tea,3,110\n'
                    '2026-03-01,Taro Milk Tea,2,100\n')
        rec = SalesRecord.objects.get()
        self.assertEqual(rec.quantity, 5)                        # 3 + 2
        self.assertEqual(float(rec.quantity * rec.price), 530)   # 330 + 200, revenue preserved

    def test_reupload_of_same_file_does_not_double_count(self):
        text = 'Date,Product Name,Quantity,Unit Price\n2026-03-01,Taro Milk Tea,3,110\n'
        self.upload(text)
        self.upload(text)
        self.assertEqual(SalesRecord.objects.get().quantity, 3)

    def test_product_names_match_ignoring_case_and_spaces(self):
        self.upload('Date,Product Name,Quantity,Unit Price\n2026-03-01,Taro Milk Tea,3,110\n')
        self.upload('Date,Product Name,Quantity,Unit Price\n2026-03-02,taro  milk tea ,4,110\n')
        self.assertEqual(set(SalesRecord.objects.values_list('product_name', flat=True)), {'Taro Milk Tea'})

    def test_invalid_rows_are_rejected_with_reason(self):
        future = (timezone.localdate() + timedelta(days=3)).isoformat()
        r = self.upload('Date,Product Name,Quantity,Unit Price\n'
                        '2026-03-01,Taro Milk Tea,-2,110\n'      # negative qty
                        '2026-03-01,Okinawa Milk Tea,0,110\n'    # zero qty
                        f'{future},Taro Milk Tea,2,110\n'        # future date
                        '2026-03-02,Taro Milk Tea,2,110\n')      # the only valid row
        self.assertEqual(SalesRecord.objects.count(), 1)
        html = r.content.decode()
        self.assertIn('3 row(s) were not imported', html)
        self.assertIn('in the future', html)

    def test_excel_style_dates_are_accepted(self):
        self.upload('Date,Product Name,Quantity,Unit Price\n3/5/2026,Taro Milk Tea,2,110\n')
        self.assertEqual(SalesRecord.objects.get().sale_date.isoformat(), '2026-03-05')


class DeductionComputationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o', email='o@x.com', password='x', role='OWNER')
        self.today = timezone.localdate()
        self.milk = Inventory.objects.create(item_name='Milk', unit='mL', total_stock=10000, stock_qty=10000,
                                             restock_threshold=1000, category='Stock', stock_as_of=self.today)
        self.pearls = Inventory.objects.create(item_name='Tapioca Pearls', unit='g', total_stock=3000, stock_qty=3000,
                                               restock_threshold=500, category='Stock', stock_as_of=self.today)
        taro = ProductRecipe.objects.create(product_name='Taro Milk Tea')
        RecipeIngredient.objects.create(recipe=taro, inventory_item=self.milk, qty_per_serving=120)
        RecipeIngredient.objects.create(recipe=taro, inventory_item=self.pearls, qty_per_serving=30)
        okinawa = ProductRecipe.objects.create(product_name='Okinawa Milk Tea')
        RecipeIngredient.objects.create(recipe=okinawa, inventory_item=self.milk, qty_per_serving=100)

    def sale(self, product, qty, days_ago=0):
        return SalesRecord.objects.create(product_name=product, quantity=qty, price=100,
                                          sale_date=self.today - timedelta(days=days_ago), temp_c=30)

    def test_shared_ingredient_totals_across_products(self):
        # Milk: 10 Taro × 120 + 5 Okinawa × 100 = 1200 + 500 = 1700 mL
        deduct_inventory_for_sales([self.sale('Taro Milk Tea', 10), self.sale('Okinawa Milk Tea', 5)])
        self.milk.refresh_from_db(); self.pearls.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 10000 - 1700)
        self.assertEqual(self.pearls.stock_qty, 3000 - 300)   # 10 × 30 g

    def test_sales_before_last_stock_count_do_not_deduct(self):
        # Old history (uploaded to train the forecast) must not touch today's stock
        summary = deduct_inventory_for_sales([self.sale('Taro Milk Tea', 50, days_ago=200)])
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 10000)
        self.assertEqual(summary['skipped_old'], 1)

    def test_every_deduction_is_written_to_the_audit_log(self):
        deduct_inventory_for_sales([self.sale('Taro Milk Tea', 10)], user=self.owner)
        log = InventoryAuditLog.objects.get(inventory=self.milk, action='deduct')
        self.assertEqual(log.qty_change, -1200)
        self.assertIn('Taro Milk Tea ×10', log.notes)
        self.assertEqual(log.performed_by, self.owner)

    def test_similar_names_do_not_match_the_wrong_recipe(self):
        # "Milk Tea" is not "Taro Milk Tea" — report it, don't guess
        summary = deduct_inventory_for_sales([self.sale('Milk Tea', 10)])
        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 10000)
        self.assertEqual(summary['no_recipe'], ['Milk Tea'])

    def test_selling_more_than_recorded_stock_is_reported(self):
        # 101 Taro need 3030 g pearls, only 3000 on hand → short by 30 g
        # (and 12,120 mL milk vs 10,000 → short by 2,120 mL)
        summary = deduct_inventory_for_sales([self.sale('Taro Milk Tea', 101)])
        self.pearls.refresh_from_db()
        self.assertEqual(self.pearls.stock_qty, 0)
        short = {s['item']: s['short_by'] for s in summary['shortfall']}
        self.assertAlmostEqual(short['Tapioca Pearls'], 30)
        self.assertAlmostEqual(short['Milk'], 2120)


class InventoryEditComputationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o', email='o@x.com', password='x', role='OWNER')
        self.c = Client(); self.c.force_login(self.owner)
        self.beans = Inventory.objects.create(item_name='Coffee Beans', unit='kg', total_stock=5, stock_qty=5,
                                              restock_threshold=1, category='Stock')

    def edit(self, **extra):
        data = {'form_type': 'edit_entry', 'item_id': self.beans.pk, 'item_name': 'Coffee Beans',
                'total_stock': 5, 'unit': 'kg', 'restock_threshold': 1}
        data.update(extra)
        return self.c.post('/inventory/', data, follow=True)

    def test_stock_count_sets_current_stock_and_logs_the_difference(self):
        self.edit(current_stock='3.5')
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.stock_qty, 3.5)
        self.assertEqual(self.beans.stock_as_of, timezone.localdate())
        self.assertEqual(InventoryAuditLog.objects.get(action='count').qty_change, -1.5)

    def test_unit_cannot_change_while_used_in_a_recipe(self):
        recipe = ProductRecipe.objects.create(product_name='Spanish Latte')
        RecipeIngredient.objects.create(recipe=recipe, inventory_item=self.beans, qty_per_serving=0.018)
        self.edit(unit='g')
        self.beans.refresh_from_db()
        self.assertEqual(self.beans.unit, 'kg')

    def test_new_item_uses_default_limit_from_settings(self):
        SystemSetting.objects.create(store_name='Shop', stock_threshold=7)
        self.c.post('/inventory/', {'form_type': 'add_entry', 'item_name': 'Cups', 'total_stock': 100,
                                    'unit': 'pcs', 'category': 'Supply', 'unit_cost': 2,
                                    'package_size': 50, 'package_unit': 'pack'})
        self.assertEqual(Inventory.objects.get(item_name='Cups').restock_threshold, 7)

    def test_expired_stock_cannot_make_drinks(self):
        recipe = ProductRecipe.objects.create(product_name='Spanish Latte')
        RecipeIngredient.objects.create(recipe=recipe, inventory_item=self.beans, qty_per_serving=0.02)
        self.assertEqual(recipe.can_make(), 250)                 # 5 kg / 0.02 kg
        self.beans.expiry_date = timezone.localdate() - timedelta(days=1)
        self.beans.save()
        self.assertEqual(recipe.can_make(), 0)


class RestockPlanComputationTests(TestCase):
    def test_buy_amount_covers_expected_use_plus_limit_in_whole_packages(self):
        from unittest.mock import patch
        from inventory.services import get_ingredient_demand_forecast

        milk = Inventory.objects.create(item_name='Milk', unit='mL', total_stock=1000, stock_qty=1000,
                                        restock_threshold=500, category='Stock',
                                        package_size=1000, package_unit='box', unit_cost=0.1)
        taro = ProductRecipe.objects.create(product_name='Taro Milk Tea')
        RecipeIngredient.objects.create(recipe=taro, inventory_item=milk, qty_per_serving=120)

        with patch('forecast.ml_engine.predict_per_product', return_value=[{'name': 'Taro Milk Tea', 'qty': 20}]), \
             patch('forecast.ml_engine.get_historical_avg_temp', return_value=30.0):
            item = get_ingredient_demand_forecast(days_ahead=1)['items'][0]

        # use = 20 × 120 = 2,400 mL; need 2,400 + 500 limit − 1,000 on hand = 1,900 mL
        # → rounded up to whole 1,000 mL boxes = 2 boxes = 2,000 mL, cost 2,000 × ₱0.10
        self.assertEqual(item['predicted_use'], 2400)
        self.assertEqual(item['to_buy_packages'], 2)
        self.assertEqual(item['to_buy'], 2000)
        self.assertEqual(item['est_cost'], 200)


class StaffRequestApprovalTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o', email='o@x.com', password='x', role='OWNER')
        self.staff = User.objects.create_user(username='s', email='s@x.com', password='x', role='STAFF')
        self.c = Client(); self.c.force_login(self.owner)

    def request(self, name, qty, unit):
        return InventoryRequest.objects.create(item_name=name, total_stock=qty, stock_qty=qty, unit=unit,
                                               category='Stock', requested_by=self.staff, status='pending')

    def test_request_for_existing_item_restocks_it_instead_of_duplicating(self):
        milk = Inventory.objects.create(item_name='Milk', unit='mL', total_stock=1000, stock_qty=400,
                                        restock_threshold=500, category='Stock')
        req = self.request('milk', 2000, 'mL')
        self.c.post(f'/owner/approvals/inventory/{req.pk}/', {'action': 'approve'})
        self.assertEqual(Inventory.objects.filter(item_name__iexact='milk').count(), 1)
        milk.refresh_from_db()
        self.assertEqual(milk.stock_qty, 2400)      # 400 + 2000
        self.assertTrue(InventoryAuditLog.objects.filter(action='restock', qty_change=2000).exists())


class SettingsTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='o', email='o@x.com', password='x', role='OWNER')
        self.c = Client(); self.c.force_login(self.owner)
        SystemSetting.objects.create(id=1, store_name='Shop', weather_api_key='SECRETKEY123',
                                     store_lat=14.4, store_lon=121.1)

    def test_saving_settings_keeps_the_weather_key_and_never_shows_it(self):
        self.c.post('/owner/settings/', {'update_config': '1', 'store_name': 'iBaked',
                                         'stock_threshold': '15', 'weather_api_key': ''})
        config = SystemSetting.objects.get()
        self.assertEqual(config.weather_api_key, 'SECRETKEY123')
        self.assertEqual(config.stock_threshold, 15)
        self.assertNotIn('SECRETKEY123', self.c.get('/owner/settings/').content.decode())
