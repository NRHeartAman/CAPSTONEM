# staff/prep_utils.py

import datetime

from django.utils import timezone
from django.db.models import F
from inventory.models import Inventory
from staff.models import PrepTask        # <-- now correctly from staff
from forecast.ml_engine import predict_per_product, get_historical_avg_temp


def generate_daily_prep_tasks():
    today = timezone.localdate()

    # 1. Auto-tasks from low stock inventory — built entirely from each
    # item's own fields (name, stock_qty, unit, restock_threshold). No
    # hardcoded per-item list: any Inventory row can trigger a task.
    low_stock_items = Inventory.objects.filter(stock_qty__lte=F('restock_threshold'))
    for item in low_stock_items:
        is_critical = item.stock_qty <= (item.restock_threshold / 2)

        PrepTask.objects.get_or_create(
            title=f"Restock {item.item_name}",
            date=today,
            defaults={
                'instructions': (
                    f"Current stock: {item.stock_qty:g} {item.unit} — "
                    f"at or below the restock threshold ({item.restock_threshold:g} {item.unit}). "
                    f"{'CRITICAL — ' if is_critical else ''}Restock before shift."
                ),
                'priority':     'high' if is_critical else 'normal',
                'source':       'low_stock',
                'linked_item':  item.item_name,
            }
        )

    # 2. Tomorrow's outlook — per-product demand forecast for the next
    # shift, driven by the same ML engine as the Forecast page. Falls back
    # to each weekday's historical average temperature since there's no
    # live weather call available outside a request context.
    tomorrow = today + datetime.timedelta(days=1)
    tomorrow_dow = tomorrow.weekday()
    outlook_temp = get_historical_avg_temp(tomorrow_dow)
    if outlook_temp is None:
        outlook_temp = 28.0

    forecast_products = predict_per_product(outlook_temp, tomorrow_dow, tomorrow.month)
    top_outlook = [p for p in forecast_products if p['qty'] > 0][:3]

    for product in top_outlook:
        title = f"Prep: {product['name']} (Tomorrow's Outlook)"
        PrepTask.objects.get_or_create(
            title=title,
            date=today,
            defaults={
                'instructions': (
                    f"Forecast expects ~{product['qty']} units of {product['name']} "
                    f"on {tomorrow.strftime('%a, %b %d')} — prep sufficient batch this shift."
                ),
                'priority':     'high' if product['trend'] == 'up' else 'normal',
                'source':       'forecast',
                'linked_item':  product['name'],
            }
        )