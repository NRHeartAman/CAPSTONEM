from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User
from inventory.models import Inventory
from staff.models import PrepTask
from staff.prep_utils import generate_daily_prep_tasks


class StaffDashboardLowStockTests(TestCase):
    """The staff dashboard reimplemented the same hardcoded stock_qty <= 20
    check independently of the owner dashboard and the notification system —
    all three had to agree on each item's own restock_threshold."""

    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff1', email='staff1@x.com', password='x', role='STAFF')

    def test_critical_stock_table_uses_own_threshold(self):
        Inventory.objects.create(item_name='HighThreshold', unit='kg', total_stock=50,
                                  stock_qty=25, restock_threshold=30, category='Stock')
        Inventory.objects.create(item_name='BelowOldHardcode', unit='kg', total_stock=50,
                                  stock_qty=15, restock_threshold=10, category='Stock')

        c = Client(); c.force_login(self.staff)
        html = c.get('/staff/').content.decode()
        self.assertIn('HighThreshold', html)
        # Appears once only, in the full registry table — never in the
        # Critical Stock Warnings table.
        self.assertNotIn('BelowOldHardcode</strong></td><td style="color: var(--text-muted); font-weight: 600;">15.0', html)

    def test_stock_registry_restock_badge_uses_own_threshold(self):
        Inventory.objects.create(item_name='ShouldShowStable', unit='kg', total_stock=50,
                                  stock_qty=15, restock_threshold=10, category='Stock')
        c = Client(); c.force_login(self.staff)
        html = c.get('/staff/').content.decode()
        idx = html.rfind('ShouldShowStable')
        row = html[idx:idx + 500]
        self.assertIn('Stable', row.split('</tr>')[0])


class PrepTaskAutoGenerationTests(TestCase):
    """generate_daily_prep_tasks() decided which auto-restock chores to
    create using the same hardcoded stock_qty <= 20 threshold."""

    def test_auto_task_created_for_item_at_own_threshold_not_hardcoded_20(self):
        Inventory.objects.create(item_name='Coffee Beans', unit='kg', total_stock=50,
                                  stock_qty=25, restock_threshold=30, category='Stock')
        generate_daily_prep_tasks()
        today = timezone.localdate()
        self.assertTrue(
            PrepTask.objects.filter(date=today, linked_item='Coffee Beans').exists()
        )

    def test_no_auto_task_for_item_above_own_threshold(self):
        Inventory.objects.create(item_name='Coffee Beans', unit='kg', total_stock=50,
                                  stock_qty=25, restock_threshold=10, category='Stock')
        generate_daily_prep_tasks()
        today = timezone.localdate()
        self.assertFalse(
            PrepTask.objects.filter(date=today, linked_item='Coffee Beans').exists()
        )


class TogglePrepTaskTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user(
            username='staff1', email='staff1@x.com', password='x', role='STAFF')

    def test_toggle_flips_is_done(self):
        task = PrepTask.objects.create(title='Brew tea', date=timezone.localdate())
        c = Client(); c.force_login(self.staff)
        r = c.post(f'/prep/toggle/{task.pk}/')
        self.assertEqual(r.json()['is_done'], True)
        task.refresh_from_db()
        self.assertTrue(task.is_done)
