# staff/prep_utils.py

from django.utils import timezone
from django.db.models import Sum, F
from inventory.models import Inventory
from sales.models import SalesRecord
from staff.models import PrepTask        # <-- now correctly from staff


SEEDED_TASKS = [
    {
        'title':        'Tapioca Pearls Batch (Heavy Load)',
        'instructions': 'Cook minimum 3 full large batches for afternoon peak hours',
        'priority':     'high',
        'source':       'manual',
    },
    {
        'title':        'Classic Tea Base Emulsion',
        'instructions': 'Brew 25 Liters of Assam Black Tea base before opening',
        'priority':     'high',
        'source':       'manual',
    },
    {
        'title':        'Non-Dairy Creamer Stocks',
        'instructions': 'Thaw and decant 10 units into active workspace bins',
        'priority':     'normal',
        'source':       'manual',
    },
]

STOCK_TASK_MAP = [
    {
        'keyword':      'wintermelon',
        'title':        'Wintermelon Syrup Refill',
        'instructions': 'Replenish main line dispensers to maximum capacity',
        'priority':     'normal',
        'source':       'low_stock',
    },
    {
        'keyword':      'taro',
        'title':        'Taro Powder Restock',
        'instructions': 'Top up taro powder canister from storage room',
        'priority':     'normal',
        'source':       'low_stock',
    },
    {
        'keyword':      'coffee',
        'title':        'Coffee Beans Critical Restock',
        'instructions': 'Retrieve emergency stock from back storage — CRITICAL',
        'priority':     'high',
        'source':       'low_stock',
    },
    {
        'keyword':      'milk',
        'title':        'Milk Supply Check',
        'instructions': 'Check milk stock and request reorder if below 1L',
        'priority':     'normal',
        'source':       'low_stock',
    },
    {
        'keyword':      'sugar',
        'title':        'Sugar Refill',
        'instructions': 'Refill sugar dispensers from bulk storage',
        'priority':     'normal',
        'source':       'low_stock',
    },
]


def generate_daily_prep_tasks():
    today = timezone.localdate()

    # 1. Seeded tasks
    for task in SEEDED_TASKS:
        PrepTask.objects.get_or_create(
            title=task['title'],
            date=today,
            defaults={
                'instructions': task['instructions'],
                'priority':     task['priority'],
                'source':       task['source'],
            }
        )

    # 2. Auto-tasks from low stock inventory
    low_stock_items = Inventory.objects.filter(stock_qty__lte=F('restock_threshold'))
    for item in low_stock_items:
        item_name_lower = item.item_name.lower()
        for mapping in STOCK_TASK_MAP:
            if mapping['keyword'] in item_name_lower:
                PrepTask.objects.get_or_create(
                    title=mapping['title'],
                    date=today,
                    defaults={
                        'instructions': mapping['instructions'],
                        'priority':     'high' if item.stock_qty <= 5 else mapping['priority'],
                        'source':       mapping['source'],
                        'linked_item':  item.item_name,
                    }
                )
                break

    # 3. Best-seller prep reminders (top 2 this month)
    ref_month_start = today.replace(day=1)
    top_sellers = (
        SalesRecord.objects.filter(sale_date__gte=ref_month_start, sale_date__lte=today)
        .values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:2]
    )
    for product in top_sellers:
        title = f"Prep: {product['product_name']} (Top Seller)"
        PrepTask.objects.get_or_create(
            title=title,
            date=today,
            defaults={
                'instructions': f"Ensure sufficient batch prepared — {product['product_name']} is a top seller this month.",
                'priority':     'high',
                'source':       'best_seller',
                'linked_item':  product['product_name'],
            }
        )