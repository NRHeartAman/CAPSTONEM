from datetime import date, timedelta

from django.test import TestCase, Client

from accounts.models import User
from forecast.ml_engine import train_and_predict, predict_per_product
from sales.models import SalesRecord


class ForecastAuthTests(TestCase):
    """The forecast page and its APIs had no @login_required at all,
    unlike every other business-data view in the app."""

    def test_anonymous_user_is_redirected_to_login(self):
        for url in ['/forecast/', '/forecast/predict-api/', '/forecast/monthly-api/']:
            r = Client().get(url)
            self.assertEqual(r.status_code, 302, url)
            self.assertIn('/login/', r.url, url)

    def test_logged_in_staff_can_access(self):
        staff = User.objects.create_user(
            username='staff1', email='staff1@x.com', password='x', role='STAFF')
        c = Client(); c.force_login(staff)
        self.assertEqual(c.get('/forecast/').status_code, 200)


class MLEngineTests(TestCase):
    def test_returns_none_with_insufficient_history(self):
        pred, accuracy, rows = train_and_predict(current_temp=30, day_of_week=1)
        self.assertIsNone(pred)
        self.assertEqual(rows, 0)

    def test_predicts_with_enough_history(self):
        today = date.today()
        for i in range(6):
            SalesRecord.objects.create(
                product_name='Latte', sale_date=today - timedelta(days=i + 1),
                quantity=10, price=100, temp_c=28)
        pred, accuracy, rows = train_and_predict(current_temp=28, day_of_week=1)
        self.assertIsNotNone(pred)
        self.assertGreaterEqual(rows, 5)

    def test_predict_per_product_needs_at_least_3_rows_per_product(self):
        today = date.today()
        for i in range(6):
            SalesRecord.objects.create(
                product_name='Latte', sale_date=today - timedelta(days=i + 1),
                quantity=10, price=100, temp_c=28)
        results = predict_per_product(current_temp=28, day_of_week=1)
        names = [r['name'] for r in results]
        self.assertIn('Latte', names)
