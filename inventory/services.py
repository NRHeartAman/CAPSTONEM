# inventory/services.py
# ─────────────────────────────────────────────────────────────────────────────
# Call  deduct_inventory_for_sales(sales, user=...)  after every CSV/sales upload.
# It reads each sale → finds the matching recipe → deducts ingredients.

import math
from datetime import date

from django.db import transaction
from .models import ProductRecipe, servings_from


def normalize_product_name(name):
    """Case/whitespace-insensitive key for matching sales rows to recipes
    ("Taro  milk tea " == "Taro Milk Tea")."""
    return ' '.join((name or '').split()).lower()


def _fuzzy_match(name, options_by_lower):
    """Look up a product in a {normalized_name: value} dict.

    Exact (normalized) match only. A substring fallback used to live here,
    but it let e.g. "Latte" or "Milk Tea" silently match whichever recipe
    happened to contain those words — deducting the wrong ingredients.
    An unmatched product is reported instead, so the owner can add its recipe."""
    return options_by_lower.get(normalize_product_name(name))


@transaction.atomic
def deduct_inventory_for_sales(sales_queryset, user=None):
    """
    Given a queryset (or list) of SalesRecord objects, deduct the
    corresponding raw-material quantities from Inventory, and write one
    'deduct' audit-log entry per ingredient so every change is traceable.

    A sale only deducts from an ingredient when it is dated on/after that
    ingredient's stock_as_of (the day its stock was last counted) — older
    sales are already reflected in the count. This is what lets the owner
    upload months of history for the forecast without draining today's stock.

    Returns a summary dict:
        {
          'deducted':    [ {'product': str, 'qty_sold': int} ],
          'no_recipe':   [ product_name, ... ],
          'low_stock':   [ {'item': str, 'remaining': float, 'unit': str} ],
          'shortfall':   [ {'item': str, 'short_by': float, 'unit': str} ],
          'skipped_old': int,   # sales rows older than the stock count
        }
    """
    from .models import InventoryAuditLog

    summary = {'deducted': [], 'no_recipe': [], 'low_stock': [], 'shortfall': [], 'skipped_old': 0}

    recipes = {normalize_product_name(r.product_name): r
               for r in ProductRecipe.objects.prefetch_related('ingredients__inventory_item')}

    # product → its sales rows (keeps each row's date for the stock_as_of check)
    sales_by_product = {}
    display_name = {}
    for sale in sales_queryset:
        key = normalize_product_name(sale.product_name)
        sales_by_product.setdefault(key, []).append(sale)
        display_name.setdefault(key, sale.product_name.strip())

    # inventory pk → {'inv', 'used', 'products': {name: qty}, 'dates': [...]}
    usage = {}
    skipped_rows = set()

    for key, sales in sales_by_product.items():
        recipe = recipes.get(key)
        if not recipe:
            summary['no_recipe'].append(display_name[key])
            continue

        counted_any = 0
        for line in recipe.ingredients.all():
            inv = line.inventory_item
            for sale in sales:
                sale_date = sale.sale_date
                if isinstance(sale_date, str):
                    sale_date = date.fromisoformat(sale_date)
                if inv.stock_as_of and sale_date < inv.stock_as_of:
                    skipped_rows.add(sale.pk or id(sale))
                    continue
                acc = usage.setdefault(inv.pk, {'inv': inv, 'used': 0.0, 'products': {}, 'dates': []})
                acc['used'] += line.qty_per_serving * sale.quantity
                acc['products'][recipe.product_name] = acc['products'].get(recipe.product_name, 0) + sale.quantity
                acc['dates'].append(sale_date)
                counted_any += 1

        if counted_any:
            summary['deducted'].append({
                'product':  recipe.product_name,
                'qty_sold': sum(s.quantity for s in sales),
            })

    summary['skipped_old'] = len(skipped_rows)

    for acc in usage.values():
        inv = acc['inv']
        inv.refresh_from_db(fields=['stock_qty'])
        used = acc['used']
        if used <= 0:
            continue
        before = float(inv.stock_qty)
        # Floor at 0 — but report it: selling more than recorded stock means
        # the recorded count was off, which the owner should know about.
        if used > before:
            summary['shortfall'].append({'item': inv.item_name, 'short_by': round(used - before, 3), 'unit': inv.unit})
        inv.stock_qty = max(0.0, before - used)
        inv.save(update_fields=['stock_qty', 'updated_at'])

        d0, d1 = min(acc['dates']), max(acc['dates'])
        span = f"{d0:%b %d}" if d0 == d1 else f"{d0:%b %d}–{d1:%b %d}"
        sold = ', '.join(f"{name} ×{qty}" for name, qty in acc['products'].items())
        InventoryAuditLog.objects.create(
            inventory=inv, item_name=inv.item_name, action='deduct',
            qty_change=-round(before - inv.stock_qty, 3), unit=inv.unit,
            performed_by=user,
            notes=f"Sales upload ({span}): {sold}",
        )

        if inv.stock_qty <= inv.restock_threshold:
            summary['low_stock'].append({
                'item':      inv.item_name,
                'remaining': round(inv.stock_qty, 3),
                'unit':      inv.unit,
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
                possible = servings_from(inv.usable_qty, line.qty_per_serving)
            else:
                possible = 9999

            ingredient_data.append({
                'item':              inv.item_name,
                'qty_per_serving':   line.qty_per_serving,
                'current_stock':     round(inv.usable_qty, 2),
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

    # Advisory only — surfaces an overlapping event as context for the
    # owner/staff, never as a multiplier on the real ML prediction (that
    # would fabricate a number the model never actually produced).
    event_note = None
    try:
        from owner.models import OwnerEvent
        # end_date can be NULL (single-day events) so the overlap check is
        # done in Python against effective_end_date rather than in SQL.
        candidates = OwnerEvent.objects.filter(event_date__lte=target_date, is_archived=False)
        overlapping = next((e for e in candidates if e.effective_end_date >= target_date), None)
        if overlapping:
            event_note = f"{overlapping.event_name} is happening on this day — demand may run higher than shown."
    except Exception:
        pass

    predictions = predict_per_product(temp, day_of_week, month)
    if not predictions:
        return {
            'has_enough_data': False,
            'target_date':     target_date,
            'day_label':       target_date.strftime('%A'),
            'items':           [],
            'event_note':      event_note,
        }

    # Same name-matching as deduct_inventory_for_sales.
    recipes = {normalize_product_name(r.product_name): r
               for r in ProductRecipe.objects.prefetch_related('ingredients__inventory_item')}

    ingredient_use = {}  # inventory pk -> accumulator dict

    for pred in predictions:
        recipe = _fuzzy_match(pred['name'], recipes)
        if not recipe:
            continue

        for line in recipe.ingredients.all():
            inv = line.inventory_item
            use = line.qty_per_serving * pred['qty']
            acc = ingredient_use.setdefault(inv.pk, {
                'item': inv.item_name, 'unit': inv.unit,
                'use': 0.0, 'current': inv.usable_qty,
                'threshold': inv.restock_threshold,
                'inv': inv,
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

        # What to buy: enough to cover predicted use AND stay at the item's own
        # low-stock limit afterwards, rounded up to whole packages when the
        # package size is known. Never suggests going over max_stock unless
        # the predicted use alone needs it.
        inv    = data['inv']
        to_buy = max(0.0, data['use'] + data['threshold'] - data['current'])
        if inv.max_stock:
            cap    = max(0.0, inv.max_stock - data['current'])
            need   = max(0.0, data['use'] - data['current'])
            to_buy = max(need, min(to_buy, cap))
        packages = None
        if to_buy > 0 and inv.package_size:
            packages = math.ceil(to_buy / inv.package_size - 1e-9)
            to_buy   = packages * inv.package_size
        est_cost = round(to_buy * inv.unit_cost, 2) if to_buy > 0 and inv.unit_cost else None

        items.append({
            'item':              data['item'],
            'unit':              data['unit'],
            'current_stock':     round(data['current'], 2),
            'predicted_use':     round(data['use'], 2),
            'to_buy':            round(to_buy, 2),
            'to_buy_packages':   packages,
            'package_unit':      inv.get_package_unit_display() if inv.package_unit else '',
            'package_size':      inv.package_size,
            'est_cost':          est_cost,
            'is_expired':        inv.is_expired,
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
        'event_note':      event_note,
    }


def get_predicted_demand_report(days_ahead=1):
    """
    Per-PRODUCT view for the Forecast → Predicted Demand page: joins the ML
    engine's predicted demand (forecast.ml_engine.predict_per_product) with
    each product's current sellable stock (get_inventory_forecast's
    `can_make` — servings makeable from what's on hand right now) to get an
    actionable, overstocking-aware recommendation.

    recommended_order is 0 whenever current stock already covers predicted
    demand — never recommends buying when there's already enough on hand.

    Returns:
        {
            'has_enough_data': bool,
            'target_date': date,
            'day_label': str,
            'event_note': str | None,
            'products': [
                {
                    'product', 'predicted_demand', 'current_stock',
                    'stock_gap', 'recommended_order', 'limiting_item',
                    'has_pending_request',
                },
                ...
            ],  # worst stock-gap first
        }
    """
    from datetime import timedelta
    from django.utils import timezone as tz
    from forecast.ml_engine import predict_per_product, get_historical_avg_temp
    from owner.models import InventoryRequest, OwnerEvent

    target_date = tz.localdate() + timedelta(days=days_ahead)
    day_of_week = target_date.weekday()
    month       = target_date.month

    temp = get_historical_avg_temp(day_of_week)
    if temp is None:
        temp = 28.0

    # Same non-numeric event advisory as get_ingredient_demand_forecast —
    # never used to inflate the ML prediction itself.
    event_note = None
    candidates  = OwnerEvent.objects.filter(event_date__lte=target_date, is_archived=False)
    overlapping = next((e for e in candidates if e.effective_end_date >= target_date), None)
    if overlapping:
        event_note = f"{overlapping.event_name} is happening on this day — demand may run higher than shown."

    predictions = predict_per_product(temp, day_of_week, month)
    if not predictions:
        return {
            'has_enough_data': False,
            'target_date':     target_date,
            'day_label':       target_date.strftime('%A'),
            'event_note':      event_note,
            'products':        [],
        }

    stock_by_product = {normalize_product_name(f['product']): f for f in get_inventory_forecast()}
    pending_by_item  = {
        r.strip().lower()
        for r in InventoryRequest.objects.filter(status='pending').values_list('item_name', flat=True)
    }

    products = []
    for pred in predictions:
        match = _fuzzy_match(pred['name'], stock_by_product)
        if not match:
            continue  # no recipe defined yet — nothing to compare stock against

        predicted_demand  = pred['qty']
        current_stock     = match['can_make']
        limiting_item     = match['limiting_item']
        stock_gap         = max(0, predicted_demand - current_stock)

        products.append({
            'product':             match['product'],
            'predicted_demand':    predicted_demand,
            'current_stock':       current_stock,
            'stock_gap':           stock_gap,
            'recommended_order':   stock_gap,
            'limiting_item':       limiting_item,
            'has_pending_request': bool(limiting_item) and limiting_item.strip().lower() in pending_by_item,
        })

    products.sort(key=lambda p: p['stock_gap'], reverse=True)

    return {
        'has_enough_data': True,
        'target_date':     target_date,
        'day_label':       target_date.strftime('%A'),
        'event_note':      event_note,
        'products':        products,
    }


def get_predicted_vs_actual_trend(days=30):
    """
    Day-by-day totals for the Owner Dashboard's "Predicted vs. Actual"
    chart: for each of the last `days` days, sums forecast.ForecastLog's
    locked-in predictions (across all products) and compares to that same
    day's real SalesRecord total.

    A date is only included when BOTH a logged prediction and real sales
    data exist for it — ForecastLog only started being written once this
    feature shipped, so most historical dates have no prediction on file,
    and a day with a prediction but no uploaded sales yet isn't a real
    zero. Showing either as a comparable point would be misleading, the
    same rule already applied on the Forecast Results page.

    Returns:
        {
            'has_enough_data': bool,
            'points': [{'date': date, 'predicted': float, 'actual': int}, ...],
        }  # chronological order
    """
    from datetime import timedelta
    from django.db.models import Sum
    from django.utils import timezone as tz
    from forecast.models import ForecastLog
    from sales.models import SalesRecord

    start_date = tz.localdate() - timedelta(days=days)

    predicted_by_date = {}
    for row in (
        ForecastLog.objects.filter(target_date__gte=start_date)
        .values('target_date')
        .annotate(total=Sum('predicted_qty'))
    ):
        predicted_by_date[row['target_date']] = row['total']

    actual_by_date = {}
    for row in (
        SalesRecord.objects.filter(sale_date__gte=start_date, sale_date__in=predicted_by_date.keys())
        .values('sale_date')
        .annotate(total=Sum('quantity'))
    ):
        actual_by_date[row['sale_date']] = row['total']

    points = [
        {'date': d, 'predicted': predicted_by_date[d], 'actual': actual_by_date[d]}
        for d in sorted(predicted_by_date)
        if d in actual_by_date
    ]

    return {
        'has_enough_data': len(points) >= 3,
        'points': points,
    }
