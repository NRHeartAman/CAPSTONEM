# inventory/services.py
# ─────────────────────────────────────────────────────────────────────────────
# Call  deduct_inventory_for_sales(queryset)  after every CSV/sales upload.
# It reads each sale → finds the matching recipe → deducts ingredients.

from django.db import transaction
from .models import ProductRecipe


@transaction.atomic
def deduct_inventory_for_sales(sales_queryset):
    """
    Given a queryset (or list) of SalesRecord objects, deduct the
    corresponding raw-material quantities from Inventory.

    Returns a summary dict:
        {
          'deducted': [ {'product': str, 'qty_sold': int, 'ingredients': [...]} ],
          'no_recipe': [ product_name, ... ],
          'low_stock': [ {'item': str, 'remaining': float, 'unit': str} ],
        }
    """
    summary = {'deducted': [], 'no_recipe': [], 'low_stock': []}

    # Build a map: product_name (lower) → total qty sold
    totals = {}
    for sale in sales_queryset:
        key = sale.product_name.strip().lower()
        totals[key] = totals.get(key, 0) + sale.quantity

    # Cache all recipes into a dict keyed by lower-case name
    recipes = {r.product_name.strip().lower(): r
               for r in ProductRecipe.objects.prefetch_related(
                   'ingredients__inventory_item'
               )}

    for product_key, qty_sold in totals.items():
        recipe = recipes.get(product_key)
        if not recipe:
            # Try to find a recipe with partial match (e.g. "hokkaido" in "hokkaido milk tea")
            recipe = next(
                (r for k, r in recipes.items() if product_key in k or k in product_key),
                None
            )

        if not recipe:
            summary['no_recipe'].append(product_key.title())
            continue

        ingredient_log = []
        for line in recipe.ingredients.select_related('inventory_item').all():
            inv = line.inventory_item
            total_used = line.qty_per_serving * qty_sold

            # Deduct (floor at 0 so we never go negative in DB)
            inv.stock_qty = max(0.0, inv.stock_qty - total_used)
            inv.save(update_fields=['stock_qty'])

            ingredient_log.append({
                'item':       inv.item_name,
                'used':       round(total_used, 3),
                'unit':       inv.unit,
                'remaining':  round(inv.stock_qty, 3),
            })

            # Flag low stock against this item's own restock threshold
            if inv.stock_qty <= inv.restock_threshold:
                summary['low_stock'].append({
                    'item':      inv.item_name,
                    'remaining': round(inv.stock_qty, 3),
                    'unit':      inv.unit,
                })

        summary['deducted'].append({
            'product':     recipe.product_name,
            'qty_sold':    qty_sold,
            'ingredients': ingredient_log,
        })

    return summary


def get_inventory_forecast():
    """
    For every product that has a recipe, calculate how many more servings
    can be made with current stock.
 
    Returns a list of dicts sorted by can_make (ascending — lowest stock first):
        [
          {
            'product':        'Hokkaido Milk Tea',
            'can_make':       12,
            'limiting_item':  'Milk',          # ingredient that runs out first
            'ingredients':    [ {item, qty_per_serving, current_stock, unit, can_make_from_this} ]
          },
          ...
        ]
    """
    results = []
 
    for recipe in ProductRecipe.objects.prefetch_related('ingredients__inventory_item'):
        lines = recipe.ingredients.select_related('inventory_item').all()
        if not lines.exists():
            continue
 
        ingredient_data = []
        min_servings    = None
        limiting_item   = None
 
        for line in lines:
            inv = line.inventory_item
            if line.qty_per_serving > 0:
                possible = int(inv.stock_qty // line.qty_per_serving)
            else:
                possible = 9999
 
            ingredient_data.append({
                'item':              inv.item_name,
                'qty_per_serving':   line.qty_per_serving,
                'current_stock':     round(inv.stock_qty, 2),
                'unit':              inv.unit,
                'can_make_from_this': possible,
            })
 
            if min_servings is None or possible < min_servings:
                min_servings  = possible
                limiting_item = inv.item_name
 
        results.append({
            'product':      recipe.product_name,
            'can_make':     min_servings if min_servings is not None else 0,
            'limiting_item': limiting_item,
            'ingredients':  ingredient_data,
        })
 
    results.sort(key=lambda x: x['can_make'])
    return results


def get_ingredient_demand_forecast(days_ahead=1):
    """
    Projects ingredient consumption for a future day using the ML demand
    forecast (forecast.ml_engine), matched against each product's recipe,
    and compares that to current stock.

    This is what connects three previously-separate parts of the system —
    Forecast (predicted units sold), Recipes (what each product consumes),
    and Inventory (what's on hand) — so a predicted sales spike shows up as
    a predicted ingredient shortage *before* it happens, not after.

    Returns:
        {
            'has_enough_data': bool,
            'target_date':     date,
            'day_label':       str,   # e.g. "Thursday"
            'items': [
                {
                    'item', 'unit', 'current_stock', 'predicted_use',
                    'remaining_after', 'restock_threshold',
                    'status': 'ok' | 'low' | 'out',
                },
                ...
            ],  # worst-first
        }
    """
    from datetime import timedelta
    from django.utils import timezone as tz
    from forecast.ml_engine import predict_per_product, get_historical_avg_temp

    target_date = tz.localdate() + timedelta(days=days_ahead)
    day_of_week = target_date.weekday()
    month       = target_date.month

    temp = get_historical_avg_temp(day_of_week)
    if temp is None:
        temp = 28.0

    predictions = predict_per_product(temp, day_of_week, month)
    if not predictions:
        return {
            'has_enough_data': False,
            'target_date':     target_date,
            'day_label':       target_date.strftime('%A'),
            'items':           [],
        }

    # Same name-matching as deduct_inventory_for_sales, so a predicted
    # "Hokkaido" still matches a recipe named "Hokkaido Milk Tea".
    recipes = {r.product_name.strip().lower(): r
               for r in ProductRecipe.objects.prefetch_related('ingredients__inventory_item')}

    ingredient_use = {}  # inventory pk -> accumulator dict

    for pred in predictions:
        key = pred['name'].strip().lower()
        recipe = recipes.get(key)
        if not recipe:
            recipe = next(
                (r for k, r in recipes.items() if key in k or k in key),
                None
            )
        if not recipe:
            continue

        for line in recipe.ingredients.all():
            inv = line.inventory_item
            use = line.qty_per_serving * pred['qty']
            acc = ingredient_use.setdefault(inv.pk, {
                'item': inv.item_name, 'unit': inv.unit,
                'use': 0.0, 'current': inv.stock_qty,
                'threshold': inv.restock_threshold,
            })
            acc['use'] += use

    items = []
    for data in ingredient_use.values():
        remaining = data['current'] - data['use']
        if remaining <= 0:
            status = 'out'
        elif remaining <= data['threshold']:
            status = 'low'
        else:
            status = 'ok'
        items.append({
            'item':              data['item'],
            'unit':              data['unit'],
            'current_stock':     round(data['current'], 2),
            'predicted_use':     round(data['use'], 2),
            # Clamped to 0 for display — "predicted use" already exceeding
            # stock is what 'out' status conveys; a negative number here
            # would just read as a data error rather than "you'll run out".
            'remaining_after':   round(max(0.0, remaining), 2),
            'restock_threshold': data['threshold'],
            'status':            status,
            '_sort_key':         remaining,
        })

    order = {'out': 0, 'low': 1, 'ok': 2}
    items.sort(key=lambda x: (order[x['status']], x['_sort_key']))
    for item in items:
        del item['_sort_key']

    return {
        'has_enough_data': True,
        'target_date':     target_date,
        'day_label':       target_date.strftime('%A'),
        'items':           items,
    }
