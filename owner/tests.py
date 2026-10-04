from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, Client

from accounts.models import User
from inventory.models import Inventory, ProductRecipe, RecipeIngredient
from owner.models import SalesUploadRequest
from sales.models import SalesRecord


class DashboardStatsTests(TestCase):
    """
    'This Month' stat cards used to be bound to an all-time daily average
    instead of the actual monthly total, and Low Stock Alerts used a
    hardcoded stock_qty <= 20 instead of each item's own threshold.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')

    def test_monthly_stat_cards_show_monthly_totals(self):
        SalesRecord.objects.create(product_name='Latte', sale_date='2026-03-01',
                                    quantity=5, price=100, temp_c=28)
        SalesRecord.objects.create(product_name='Latte', sale_date='2026-03-02',
                                    quantity=7, price=100, temp_c=28)
        SalesRecord.objects.create(product_name='Tea', sale_date='2026-03-03',
                                    quantity=3, price=80, temp_c=28)

        from owner.views import _get_dashboard_context
        ctx = _get_dashboard_context()
        # Latest day with sales = Mar 3: only the Tea row (3 × ₱80)
        self.assertEqual(ctx['daily_sales'], 240.0)
        self.assertEqual(ctx['daily_sold'], 3)
        # Last 7 / last 30 days both cover Mar 1–3: 500 + 700 + 240
        self.assertEqual(ctx['weekly_revenue'], 1440.0)
        self.assertEqual(ctx['monthly_revenue'], 1440.0)
        self.assertEqual(ctx['monthly_units'], 15)

        c = Client(); c.force_login(self.owner)
        html = c.get('/owner/').content.decode()
        self.assertIn('data-count-target="240.0"', html)
        self.assertIn('Sales · Mar 3', html)   # dated, not "Today's Sales"

    def test_low_stock_dashboard_uses_own_threshold(self):
        Inventory.objects.create(item_name='HighThreshold', unit='kg', total_stock=50,
                                  stock_qty=25, restock_threshold=30, category='Stock')
        Inventory.objects.create(item_name='BelowOldHardcode', unit='kg', total_stock=50,
                                  stock_qty=15, restock_threshold=10, category='Stock')

        c = Client(); c.force_login(self.owner)
        html = c.get('/owner/').content.decode()
        self.assertIn('HighThreshold', html)
        self.assertNotIn('BelowOldHardcode', html)


class CsvUploadTests(TestCase):
    """
    approve_sales_upload used to crash on every attempt: it opened the file
    in text mode ('r') then called .decode() on the resulting str. Also
    covers the header-validation gap that let malformed CSVs get marked
    'approved' with 0 records and no explanation.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')
        self.staff = User.objects.create_user(
            username='staff1', email='staff1@x.com', password='x', role='STAFF')

    def test_owner_direct_upload_rejects_missing_columns(self):
        c = Client(); c.force_login(self.owner)
        bad = SimpleUploadedFile('bad.csv', b'Date,Product Name,Quantity,Price\n', content_type='text/csv')
        c.post('/owner/upload/', {'csv_file': bad})
        self.assertEqual(SalesRecord.objects.count(), 0)

    def test_owner_direct_upload_creates_records(self):
        c = Client(); c.force_login(self.owner)
        good = SimpleUploadedFile(
            'good.csv',
            b'Date,Product Name,Quantity,Unit Price\n2026-03-01,Latte,4,100.00\n',
            content_type='text/csv')
        c.post('/owner/upload/', {'csv_file': good})
        self.assertEqual(SalesRecord.objects.filter(product_name='Latte').count(), 1)

    def test_reuploading_same_file_does_not_duplicate(self):
        c = Client(); c.force_login(self.owner)
        content = b'Date,Product Name,Quantity,Unit Price\n2026-03-01,Latte,4,100.00\n'
        c.post('/owner/upload/', {'csv_file': SimpleUploadedFile('a.csv', content, content_type='text/csv')})
        c.post('/owner/upload/', {'csv_file': SimpleUploadedFile('b.csv', content, content_type='text/csv')})
        self.assertEqual(SalesRecord.objects.filter(product_name='Latte').count(), 1)

    def test_owner_approval_actually_imports_records(self):
        """The core regression: approval used to crash with
        'str' object has no attribute 'decode' on every single attempt.
        Creates the pending request directly — the staff-facing submission
        page has been removed, but the owner-side approval path it feeds
        into is unchanged and still needs this coverage."""
        c_owner = Client(); c_owner.force_login(self.owner)

        f = SimpleUploadedFile(
            'sales.csv',
            b'Date,Product Name,Quantity,Unit Price\n2026-03-05,Tea,3,80.00\n',
            content_type='text/csv')
        req = SalesUploadRequest.objects.create(
            requested_by=self.staff, csv_file=f, original_filename='sales.csv')

        r = c_owner.post(f'/owner/approvals/csv/{req.pk}/', {'action': 'approve'}, follow=True)

        req.refresh_from_db()
        self.assertEqual(req.status, 'approved')
        self.assertEqual(req.records_added, 1)
        self.assertEqual(SalesRecord.objects.filter(product_name='Tea').count(), 1)
        self.assertNotIn('has no attribute', r.content.decode())

    def test_approval_with_bad_headers_stays_pending(self):
        c_owner = Client(); c_owner.force_login(self.owner)

        f = SimpleUploadedFile('bad.csv', b'Date,Product Name,Quantity,Price\n2026-03-05,Tea,3,80\n',
                                content_type='text/csv')
        req = SalesUploadRequest.objects.create(
            requested_by=self.staff, csv_file=f, original_filename='bad.csv')

        c_owner.post(f'/owner/approvals/csv/{req.pk}/', {'action': 'approve'})
        req.refresh_from_db()
        self.assertEqual(req.status, 'pending')
        self.assertEqual(req.records_added, 0)


class RecipeAutoDeductionTests(TestCase):
    """
    deduct_inventory_for_sales() existed but was only ever called from an
    unused example file — selling a product never actually reduced its
    ingredients' stock. Now wired into both live upload paths.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')
        self.milk = Inventory.objects.create(item_name='Milk', unit='ml', total_stock=1000,
                                              stock_qty=1000, restock_threshold=200, category='Stock')
        self.recipe = ProductRecipe.objects.create(product_name='Milk Tea')
        RecipeIngredient.objects.create(recipe=self.recipe, inventory_item=self.milk, qty_per_serving=50)

    def test_upload_deducts_ingredients_immediately_no_extra_approval(self):
        c = Client(); c.force_login(self.owner)
        f = SimpleUploadedFile(
            'x.csv', b'Date,Product Name,Quantity,Unit Price\n2026-03-01,Milk Tea,4,85.00\n',
            content_type='text/csv')
        c.post('/owner/upload/', {'csv_file': f})

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 800.0)  # 1000 - 4*50

    def test_reupload_does_not_double_deduct(self):
        c = Client(); c.force_login(self.owner)
        content = b'Date,Product Name,Quantity,Unit Price\n2026-03-01,Milk Tea,4,85.00\n'
        c.post('/owner/upload/', {'csv_file': SimpleUploadedFile('a.csv', content, content_type='text/csv')})
        c.post('/owner/upload/', {'csv_file': SimpleUploadedFile('b.csv', content, content_type='text/csv')})

        self.milk.refresh_from_db()
        self.assertEqual(self.milk.stock_qty, 800.0)

    def test_product_without_recipe_is_not_deducted_and_is_reported(self):
        c = Client(); c.force_login(self.owner)
        f = SimpleUploadedFile(
            'x.csv',
            b'Date,Product Name,Quantity,Unit Price\n2026-03-01,Mystery Drink,2,50.00\n',
            content_type='text/csv')
        r = c.post('/owner/upload/', {'csv_file': f}, follow=True)
        self.assertIn('No recipe defined', r.content.decode())
