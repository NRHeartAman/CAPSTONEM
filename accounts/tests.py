import re
from datetime import timedelta

from django.test import TestCase, Client
from django.utils import timezone

from accounts.models import User, Notification, PasswordResetToken
from inventory.models import Inventory


def _csrf(html):
    m = re.search(r"name=[\"']csrfmiddlewaretoken[\"'] value=[\"']([^\"']+)[\"']", html)
    return m.group(1) if m else None


class NotificationGenerationTests(TestCase):
    """
    The low-stock condition used to be reimplemented with a hardcoded
    stock_qty <= 20 check in several places. It must use each item's own
    restock_threshold instead.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')

    def test_low_stock_uses_item_threshold_not_hardcoded_20(self):
        # Below the OLD hardcoded 20 but above its own threshold -> not low stock.
        Inventory.objects.create(item_name='AboveOwnThreshold', unit='kg',
                                  total_stock=100, stock_qty=15, restock_threshold=10,
                                  category='Stock')
        # Above the OLD hardcoded 20 but below its own threshold -> IS low stock.
        Inventory.objects.create(item_name='BelowOwnThreshold', unit='kg',
                                  total_stock=100, stock_qty=25, restock_threshold=30,
                                  category='Stock')

        c = Client()
        c.force_login(self.owner)
        data = c.get('/notifications/api/').json()
        titles = [n['title'] for n in data['notifications']]

        self.assertTrue(any('BelowOwnThreshold' in t for t in titles))
        self.assertFalse(any('AboveOwnThreshold' in t for t in titles))

    def test_out_of_stock_and_low_stock_are_distinct_alert_types(self):
        Inventory.objects.create(item_name='Depleted', unit='kg', total_stock=10,
                                  stock_qty=0, restock_threshold=5, category='Stock')
        c = Client()
        c.force_login(self.owner)
        data = c.get('/notifications/api/').json()
        types = {n['title']: n['type'] for n in data['notifications']}
        self.assertEqual(types.get('Out of Stock: Depleted'), 'out_of_stock')

    def test_generating_twice_does_not_duplicate_unread_alerts(self):
        Inventory.objects.create(item_name='Sugar', unit='kg', total_stock=10,
                                  stock_qty=1, restock_threshold=5, category='Stock')
        c = Client()
        c.force_login(self.owner)
        c.get('/notifications/api/')
        before = Notification.objects.filter(user=self.owner).count()
        c.get('/notifications/api/')
        after = Notification.objects.filter(user=self.owner).count()
        self.assertEqual(before, after)

    def test_mark_all_read_clears_unread_count(self):
        Inventory.objects.create(item_name='Coffee', unit='kg', total_stock=10,
                                  stock_qty=1, restock_threshold=5, category='Stock')
        c = Client()
        c.force_login(self.owner)
        c.get('/notifications/api/')
        self.assertGreater(c.get('/notifications/count/').json()['unread'], 0)
        c.post('/notifications/read-all/')
        self.assertEqual(c.get('/notifications/count/').json()['unread'], 0)

    def test_anonymous_user_gets_empty_payload_not_error(self):
        r = Client().get('/notifications/api/')
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {'notifications': [], 'unread': 0})


class NotificationLinkTests(TestCase):
    """
    Each notification carries a `link` set at creation time, so clicking it
    in the bell takes the user straight to what it's about instead of
    dropping them on a generic page.
    """

    def setUp(self):
        self.owner = User.objects.create_user(
            username='owner1', email='owner1@x.com', password='x', role='OWNER')

    def test_low_stock_link_points_to_that_items_row(self):
        item = Inventory.objects.create(item_name='Milk', unit='ml', total_stock=100,
                                         stock_qty=5, restock_threshold=20, category='Stock')
        c = Client(); c.force_login(self.owner)
        data = c.get('/notifications/api/').json()
        notif = next(n for n in data['notifications'] if 'Milk' in n['title'])
        self.assertEqual(notif['link'], f'/inventory/?tab=Stock#inv-item-{item.pk}')

    def test_approval_link_points_to_approvals_page(self):
        from owner.models import InventoryRequest
        InventoryRequest.objects.create(
            requested_by=self.owner, item_name='Sugar', total_stock=10,
            stock_qty=10, unit='kg', category='Stock', status='pending')
        c = Client(); c.force_login(self.owner)
        data = c.get('/notifications/api/').json()
        notif = next(n for n in data['notifications'] if n['type'] == 'approval')
        self.assertEqual(notif['link'], '/owner/approvals/')

    def test_clicking_marks_it_read_and_the_target_page_loads(self):
        item = Inventory.objects.create(item_name='Coffee', unit='kg', total_stock=50,
                                         stock_qty=2, restock_threshold=10, category='Stock')
        c = Client(); c.force_login(self.owner)
        data = c.get('/notifications/api/').json()
        notif = next(n for n in data['notifications'] if 'Coffee' in n['title'])

        # Simulates what the bell's JS does on click.
        c.post(f"/notifications/{notif['id']}/read/")
        self.assertTrue(Notification.objects.get(pk=notif['id']).is_read)

        target = notif['link'].split('#')[0]
        r = c.get(target)
        self.assertEqual(r.status_code, 200)
        self.assertIn(f"inv-item-{item.pk}", r.content.decode())


class ForgotPasswordFlowTests(TestCase):
    """
    Regression coverage for the three stacked bugs found in this flow:
    a missing template on the reset-link page, a URL ordering bug that hid
    the success page behind '/reset-password/<token>/' matching 'success'
    as a token, and messages never being rendered so errors were invisible.
    """

    def setUp(self):
        self.user = User.objects.create_user(
            username='resetme', email='resetme@x.com', password='OldPassw0rd!',
            role='STAFF')

    def _request_reset(self, client, email='resetme@x.com', role='staff'):
        html = client.get('/forgot-password/').content.decode()
        client.post('/forgot-password/', {
            'csrfmiddlewaretoken': _csrf(html), 'role': role, 'email': email,
        })
        return PasswordResetToken.objects.filter(user_id=self.user.pk, user_role=role).first()

    def test_wrong_email_shows_error_not_silence(self):
        c = Client(enforce_csrf_checks=True)
        html = c.get('/forgot-password/').content.decode()
        r = c.post('/forgot-password/', {
            'csrfmiddlewaretoken': _csrf(html), 'role': 'staff', 'email': 'nobody@x.com',
        })
        self.assertIn('No account found', r.content.decode())

    def test_full_reset_flow_changes_password(self):
        c = Client(enforce_csrf_checks=True)
        token = self._request_reset(c)
        self.assertIsNotNone(token)

        # The reset-link page must render the form, not 500 on a missing template.
        html = c.get(f'/reset-password/{token.token}/?role=staff').content.decode()
        self.assertIn('name="password1"', html)
        csrf2 = _csrf(html)

        r = c.post(f'/reset-password/{token.token}/?role=staff', {
            'csrfmiddlewaretoken': csrf2, 'token': token.token, 'role': 'staff',
            'password1': 'NewPassw0rd!', 'password2': 'NewPassw0rd!',
        }, follow=True)

        # /reset-password/success/ must not be swallowed by <str:token> matching "success".
        self.assertIn(('/reset-password/success/', 302), r.redirect_chain)
        self.assertIn('Password Reset', r.content.decode())
        self.assertNotIn('Invalid Link', r.content.decode())

        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('NewPassw0rd!'))
        self.assertFalse(self.user.check_password('OldPassw0rd!'))
        self.assertFalse(PasswordResetToken.objects.filter(pk=token.pk).exists())

    def test_weak_password_rejected_with_visible_error(self):
        c = Client(enforce_csrf_checks=True)
        token = self._request_reset(c)
        html = c.get(f'/reset-password/{token.token}/?role=staff').content.decode()

        r = c.post(f'/reset-password/{token.token}/?role=staff', {
            'csrfmiddlewaretoken': _csrf(html), 'token': token.token, 'role': 'staff',
            'password1': 'short', 'password2': 'short',
        })
        self.assertIn('at least 8 characters', r.content.decode())
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password('OldPassw0rd!'))

    def test_reused_token_shows_invalid_link(self):
        c = Client(enforce_csrf_checks=True)
        token_value = self._request_reset(c).token
        PasswordResetToken.objects.filter(token=token_value).delete()
        html = c.get(f'/reset-password/{token_value}/?role=staff').content.decode()
        self.assertIn('Invalid Link', html)

    def test_expired_token_is_rejected(self):
        c = Client(enforce_csrf_checks=True)
        token = self._request_reset(c)
        token.expires_at = timezone.now() - timedelta(hours=1)
        token.save()
        html = c.get(f'/reset-password/{token.token}/?role=staff').content.decode()
        self.assertIn('Invalid Link', html)
