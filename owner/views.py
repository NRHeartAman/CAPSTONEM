"""
owner/views.py — CraveCast FINAL
=====================================
KEY FIXES:
  1. All stats use the DATE RANGE of uploaded data, not today's date
     → Upload March 2026 data → stats show March 2026 numbers
  2. Revenue passed as raw floats (not pre-formatted strings)
     → Fixes ₱0.00 bug on sales analytics page
  3. Deduplication on upload
     → Re-uploading same CSV won't double the data
  4. Inventory page receives sales stats from shared helper
  5. FIXED: daily_sales now uses single ref_date only (not 7-day window)
     → daily and weekly are now different values
  6. FIXED: upload_view get_or_create uses only product+date as lookup keys
     → Prevents float comparison issues causing false "invalid" uploads
  7. FIXED: temperature API calls are now batched per unique date
     → Prevents per-row HTTP timeout killing the upload
  8. Daily / weekly / monthly are the latest day / last 7 / last 30 days,
     all ending on the latest sales date — so monthly ≥ weekly ≥ daily
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from django.conf import settings
from .models import SystemSetting, StaffInvite, InventoryRequest, EventRequest, SalesUploadRequest
from accounts.models import ActivityLog, EmployeeProfile, User
from sales.models import SalesRecord
from django.db.models import Sum, F, Min, Max, Avg
from django.utils import timezone
from django.utils import timezone as tz
from datetime import timedelta, datetime, date
from inventory.models import Inventory
from inventory.services import deduct_inventory_for_sales
from accounts.decorators import owner_required_json
import json
import io
import csv
import requests


# ─────────────────────────────────────────────────────────────
# SHARED HELPER
# ─────────────────────────────────────────────────────────────

def _get_dashboard_context():
    today = timezone.now().date()

    bounds = SalesRecord.objects.aggregate(
        latest=Max('sale_date'),
        earliest=Min('sale_date'),
    )
    ref_date      = bounds['latest'] or today
    earliest_date = bounds['earliest'] or today
    has_data      = bounds['latest'] is not None

    # All windows end on ref_date (the latest day that has sales), and each
    # longer window contains the shorter one — so 30-day ≥ 7-day ≥ 1-day always.
    def _window(start):
        qs = SalesRecord.objects.filter(sale_date__gte=start, sale_date__lte=ref_date)
        agg = qs.aggregate(revenue=Sum(F('quantity') * F('price')), units=Sum('quantity'))
        return float(agg['revenue'] or 0), agg['units'] or 0, qs.count()

    # ── DAILY: sales on the latest day with data ──
    revenue_daily, units_daily, orders_daily = _window(ref_date)

    # ── WEEKLY: last 7 days, ending ref_date ──
    week_start = ref_date - timedelta(days=6)
    revenue_7d, units_7d, orders_7d = _window(week_start)

    # ── MONTHLY: last 30 days, ending ref_date ──
    month_start = ref_date - timedelta(days=29)
    revenue_monthly, units_monthly, orders_monthly = _window(month_start)

    revenue_all = float(
        SalesRecord.objects.aggregate(
            total=Sum(F('quantity') * F('price')))['total'] or 0
    )

    low_stock_qs    = Inventory.objects.filter(stock_qty__lte=F('restock_threshold')).order_by('stock_qty')
    low_stock_count = low_stock_qs.count()

    top_products = list(
        SalesRecord.objects
        .values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:5]
    )
    top_product_labels = [p['product_name'] for p in top_products]
    top_product_values = [p['total_qty']    for p in top_products]

    max_top_qty = max((p['total_qty'] for p in top_products), default=1) or 1
    top_products_ranked = [
        {
            'name':       p['product_name'],
            'qty':        p['total_qty'],
            'percentage': int((p['total_qty'] / max_top_qty) * 100),
        }
        for p in top_products
    ]

    return {
        'daily_sold':          units_daily,
        'daily_orders':        orders_daily,
        'daily_sales':         revenue_daily,
        'weekly_sales':        revenue_7d,
        'weekly_sold':         units_7d,
        'weekly_orders':       orders_7d,
        'monthly_units':       units_monthly,
        'monthly_orders':      orders_monthly,
        'monthly_sales':       revenue_monthly,
        'units_sold_today':    units_daily,
        'total_orders_today':  orders_daily,
        'weekly_revenue':      revenue_7d,
        'monthly_revenue':     revenue_monthly,
        'low_stock_count':     low_stock_count,
        'low_stock_items':     low_stock_qs[:5],
        'top_product_labels':  json.dumps(top_product_labels),
        'top_product_values':  json.dumps(top_product_values),
        'top_products_ranked': top_products_ranked,
        'ref_date':            ref_date,
        'week_start':          week_start,
        'month_start':         month_start,
        'data_is_current':     ref_date >= today,
        'earliest_date':       earliest_date,
        'has_data':            has_data,
        'revenue_all':         revenue_all,
    }


# ─────────────────────────────────────────────────────────────
# OWNER DASHBOARD
# ─────────────────────────────────────────────────────────────

def _get_owner_dashboard_extras():
    """
    Dashboard-only aggregations — deliberately kept separate from
    _get_dashboard_context() (which inventory_view also calls) so the
    Inventory page never pays for these extra queries.
    """
    from inventory.models import InventoryAuditLog
    from inventory.services import get_predicted_demand_report, get_predicted_vs_actual_trend
    from .models import OwnerEvent

    week_ago = tz.now() - timedelta(days=7)
    waste_cost_week = InventoryAuditLog.objects.filter(
        action='waste', created_at__gte=week_ago
    ).aggregate(total=Sum('total_cost'))['total'] or 0

    demand_report = get_predicted_demand_report(days_ahead=1)
    if demand_report['has_enough_data']:
        predicted_demand_tomorrow = sum(p['predicted_demand'] for p in demand_report['products'])
        restock_needed_count = sum(1 for p in demand_report['products'] if p['recommended_order'] > 0)
    else:
        predicted_demand_tomorrow = None
        restock_needed_count = None

    today = tz.localdate()
    upcoming_event = next(
        (
            e for e in OwnerEvent.objects.filter(is_archived=False).order_by('event_date')
            if e.effective_end_date >= today
        ),
        None,
    )

    recent_activity = list(
        InventoryAuditLog.objects.select_related('performed_by').order_by('-created_at')[:6]
    )

    trend = get_predicted_vs_actual_trend(days=30)
    trend_labels    = [p['date'].strftime('%b %d') for p in trend['points']]
    trend_predicted = [p['predicted'] for p in trend['points']]
    trend_actual    = [p['actual']    for p in trend['points']]

    return {
        'waste_cost_week':           waste_cost_week,
        'predicted_demand_tomorrow': predicted_demand_tomorrow,
        'restock_needed_count':      restock_needed_count,
        'forecast_has_enough_data':  demand_report['has_enough_data'],
        'upcoming_event':            upcoming_event,
        'recent_activity':           recent_activity,
        'trend_has_enough_data':     trend['has_enough_data'],
        # Only days with BOTH a prediction and real sales are plotted, so show
        # the actual span rather than implying a full 30 days.
        'trend_range': (
            f"{trend['points'][0]['date']:%b %d} – {trend['points'][-1]['date']:%b %d}"
            if trend['points'] else ''
        ),
        'trend_labels_json':         json.dumps(trend_labels),
        'trend_predicted_json':      json.dumps(trend_predicted),
        'trend_actual_json':         json.dumps(trend_actual),
    }


@login_required
def owner_dashboard_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')
    context = _get_dashboard_context()
    context.update(_get_owner_dashboard_extras())
    # "Today's checklist": how stale the sales data is drives step 1.
    context['days_since_upload'] = (
        (tz.localdate() - context['ref_date']).days if context['has_data'] else None
    )
    return render(request, 'OWNER/owner.html', context)


@owner_required_json
def owner_dashboard_stats_api(request):
    """20-second dashboard poll — same helpers the page itself renders
    from, so the numbers can never drift apart."""
    from django.http import JsonResponse
    ctx = _get_dashboard_context()
    extras = _get_owner_dashboard_extras()
    return JsonResponse({
        'daily_sales':               ctx['daily_sales'],
        'low_stock_count':           ctx['low_stock_count'],
        'predicted_demand_tomorrow': extras['predicted_demand_tomorrow'],
        'waste_cost_week':           extras['waste_cost_week'],
    })


# ─────────────────────────────────────────────────────────────
# INVENTORY
# ─────────────────────────────────────────────────────────────

@login_required
def inventory_view(request):
    """Old duplicate of the Inventory page. Its add-item path skipped cost,
    validation and the audit log, so it now just forwards to the real page
    (inventory.views.inventory_view) — one place where stock can change."""
    tab = request.GET.get('tab', 'Stock')
    return redirect(f"/inventory/?tab={tab}")


# ─────────────────────────────────────────────────────────────
# UPLOAD DATA — with deduplication + batched temp fetch
# ─────────────────────────────────────────────────────────────

CSV_DATE_FORMATS = ('%Y-%m-%d', '%Y/%m/%d', '%m/%d/%Y')   # ISO, plus Excel's usual export


def _parse_sale_date(raw):
    for fmt in CSV_DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    raise ValueError(f"unrecognised date '{raw}' (use YYYY-MM-DD)")


def _process_csv_rows(rows_list):
    """
    Shared CSV processing logic used by both upload_view and approve_sales_upload.
    Returns (created_count, skipped_count, error_list, created_records, notes).

    One SalesRecord = one product on one day. Rules, so revenue and cups are
    exactly what the file says:
      • Several rows for the same product + day IN THE SAME FILE (e.g. one per
        transaction) are ADDED together — not dropped as duplicates. The stored
        price is the revenue-weighted average, so quantity × price still equals
        the file's total for that day.
      • A product + day that is ALREADY in the database is skipped, so
        re-uploading the same file never double-counts.
      • Product names are matched ignoring case/extra spaces, reusing the
        spelling already on record.
      • Quantity must be a whole number > 0, price ≥ 0, date not in the future.
    created_records drive ingredient deduction for this batch only.
    """
    from inventory.services import normalize_product_name

    errors = []
    notes  = []
    today  = tz.localdate()

    # Canonical spelling for names we already know (sales history + recipes)
    from inventory.models import ProductRecipe
    known_names = {}
    for name in list(SalesRecord.objects.values_list('product_name', flat=True).distinct()) + \
                list(ProductRecipe.objects.values_list('product_name', flat=True)):
        known_names.setdefault(normalize_product_name(name), name)

    # ── 1. Parse + validate every row, summing same product/day ──
    totals = {}   # (normalized name, date) → {'name', 'qty', 'revenue'}
    for line_no, row in enumerate(rows_list, start=2):   # row 1 is the header
        date_val  = (row.get('Date') or '').strip()
        prod_name = ' '.join((row.get('Product Name') or '').split())
        qty_raw   = (row.get('Quantity') or '').strip()
        price_raw = (row.get('Unit Price') or '').replace('₱', '').replace(',', '').strip()

        if not any([date_val, prod_name, qty_raw, price_raw]):
            continue   # fully blank line
        try:
            if not date_val or not prod_name:
                raise ValueError("missing date or product name")
            sale_date = _parse_sale_date(date_val)
            if sale_date > today:
                raise ValueError(f"date {sale_date} is in the future")
            qty_f = float(qty_raw)
            if qty_f != int(qty_f) or qty_f <= 0:
                raise ValueError(f"quantity must be a whole number above 0 (got '{qty_raw}')")
            price = float(price_raw)
            if price < 0:
                raise ValueError(f"unit price can't be negative (got '{price_raw}')")
        except ValueError as e:
            errors.append(f"Row {line_no}: {e}")
            continue

        key = (normalize_product_name(prod_name), sale_date)
        acc = totals.setdefault(key, {
            'name': known_names.get(key[0], prod_name), 'qty': 0, 'revenue': 0.0,
        })
        acc['qty']     += int(qty_f)
        acc['revenue'] += int(qty_f) * price

    # ── 2. Temperatures (1 API call per unique date) ──
    temp_cache, estimated = {}, []
    for d in {k[1] for k in totals}:
        t = get_historical_temp(d.isoformat())
        if t is None:
            # Weather archive unreachable: use the average of REAL recorded
            # temperatures for that month rather than inventing a number.
            t = SalesRecord.objects.filter(sale_date__month=d.month).aggregate(a=Avg('temp_c'))['a'] \
                or SalesRecord.objects.aggregate(a=Avg('temp_c'))['a'] or 30.0
            estimated.append(d)
        temp_cache[d] = round(float(t), 1)
    if estimated:
        notes.append(f"Weather history was unavailable for {len(estimated)} date(s); "
                     f"used the average recorded temperature for that month.")

    # ── 3. Insert, skipping product/days already on record ──
    created, skipped, created_records = 0, 0, []
    for (norm, sale_date), acc in sorted(totals.items(), key=lambda kv: kv[0][1]):
        if SalesRecord.objects.filter(product_name__iexact=acc['name'], sale_date=sale_date).exists():
            skipped += 1
            continue
        try:
            record = SalesRecord.objects.create(
                product_name=acc['name'],
                sale_date=sale_date,
                quantity=acc['qty'],
                price=round(acc['revenue'] / acc['qty'], 2),
                temp_c=temp_cache[sale_date],
            )
        except Exception as e:
            errors.append(f"{acc['name']} on {sale_date}: could not save ({e})")
            continue
        created += 1
        created_records.append(record)

    return created, skipped, errors, created_records, notes


def _report_csv_problems(request, errors, notes):
    """Tell the owner exactly which rows were rejected (first few) instead of
    only a count — so they can fix the file and re-upload."""
    for note in notes:
        messages.info(request, note)
    if errors:
        shown = '; '.join(errors[:5])
        more  = f' (+{len(errors) - 5} more)' if len(errors) > 5 else ''
        messages.error(request, f'{len(errors)} row(s) were not imported: {shown}{more}. '
                                f'Ayusin ang mga row na ito at i-upload muli.')


def _apply_recipe_deduction(request, created_records):
    """
    Auto-deducts ingredient stock for a freshly-inserted batch of SalesRecords,
    based on each product's recipe (ProductRecipe/RecipeIngredient). Runs
    immediately as part of import — no separate owner approval step for the
    deduction itself. Surfaces a short summary via messages.
    """
    if not created_records:
        return

    summary = deduct_inventory_for_sales(created_records, user=request.user)

    if summary['deducted']:
        messages.success(
            request,
            f"Inventory deducted for {len(summary['deducted'])} "
            f"product{'s' if len(summary['deducted']) != 1 else ''} based on their recipes. "
            f"Nabawasan ang stock base sa recipe."
        )
    if summary['skipped_old']:
        messages.info(
            request,
            f"{summary['skipped_old']} sale(s) are dated before your last stock count, so they "
            f"did not reduce current stock (they're still used for the forecast). "
            f"Hindi na ibinawas dahil luma na ang petsa."
        )
    if summary['no_recipe']:
        messages.warning(
            request,
            f"No recipe defined for: {', '.join(summary['no_recipe'])}. "
            f"Inventory was not deducted for these. Walang recipe kaya hindi nabawasan ang stock."
        )
    if summary['shortfall']:
        short = ', '.join(f"{s['item']} (short {s['short_by']:g} {s['unit']})" for s in summary['shortfall'])
        messages.warning(
            request,
            f"Sales used more than the recorded stock for: {short}. Stock was set to 0 — "
            f"please do a stock count and correct it in Inventory → Edit."
        )


@login_required
def upload_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    if request.method == 'POST':
        csv_file = request.FILES.get('csv_file')

        if not csv_file:
            messages.error(request, 'No file selected. Please choose a CSV file. Pumili ng CSV file.')
            return redirect('view-upload-data')

        if not csv_file.name.lower().endswith('.csv'):
            messages.error(request, 'Invalid file type. Please upload a .csv file. Dapat .csv file lang.')
            return redirect('view-upload-data')

        try:
            decoded = csv_file.read().decode('utf-8-sig')
        except UnicodeDecodeError:
            messages.error(request, 'Could not read file. Please save your CSV as UTF-8 encoding. I-save bilang UTF-8.')
            return redirect('view-upload-data')

        # Fix rows wrapped in quotes (e.g. exported from Google Sheets)
        lines = []
        for line in decoded.splitlines():
            line = line.strip()
            if line.startswith('"') and line.endswith('"'):
                line = line[1:-1]
            lines.append(line)
        decoded = '\n'.join(lines)

        reader    = csv.DictReader(io.StringIO(decoded))
        rows_list = list(reader)

        if not rows_list:
            messages.error(request, 'The uploaded CSV is empty or has no readable rows. Walang laman ang CSV.')
            return redirect('view-upload-data')

        # Validate headers
        required_cols = {'Date', 'Product Name', 'Quantity', 'Unit Price'}
        actual_cols   = set(reader.fieldnames or [])
        missing       = required_cols - actual_cols
        if missing:
            messages.error(request, f'Missing required columns: {", ".join(missing)}. Check your file format. May kulang na column.')
            return redirect('view-upload-data')

        created, skipped, errors, created_records, notes = _process_csv_rows(rows_list)

        msg = f'Upload complete. {created} new record(s) added. Kumpleto ang pag-upload.'
        if created_records:
            # Say WHICH dates were added — older dates don't move the "latest
            # day / last 7 / last 30 days" cards, which otherwise looks like
            # nothing happened.
            dates = sorted(r.sale_date for r in created_records)
            msg += f' Dates: {dates[0]:%b %d, %Y}' + (f' – {dates[-1]:%b %d, %Y}.' if dates[-1] != dates[0] else '.')
            latest = SalesRecord.objects.aggregate(m=Max('sale_date'))['m']
            if latest and dates[-1] < latest:
                msg += (f' These are older than your latest sales ({latest:%b %d}), so the dashboard cards '
                        f"won't change, but the forecast now learns from them. "
                        f'Makikita sa Historical Data Log.')
        if skipped:
            msg += f' {skipped} product-day(s) were already uploaded and were skipped.'
        messages.success(request, msg)
        _report_csv_problems(request, errors, notes)

        _apply_recipe_deduction(request, created_records)

        return redirect('view-upload-data')

    # ── GET: Filter logic ─────────────────────────────────────
   # ── GET: Filter logic ─────────────────────────────────────
    selected_month = request.GET.get('month', '')
    selected_year  = request.GET.get('year', '')

    all_sales = SalesRecord.objects.all().order_by('-sale_date', 'product_name')
    sales     = all_sales

    if selected_month:
        sales = sales.filter(sale_date__month=selected_month)
    if selected_year:
        sales = sales.filter(sale_date__year=selected_year)

    dash = _get_dashboard_context()

    top_prod_query = (
        SalesRecord.objects
        .values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')
        .first()
    )
    top_product = top_prod_query['product_name'] if top_prod_query else 'No Data'

    current_year    = date.today().year
    available_years = range(2020, current_year + 1)

    return render(request, 'OWNER/upload_data.html', {
        'sales':              sales,
        'all_sales':          all_sales,
        'available_years':    available_years,
        'selected_month':     selected_month,
        'selected_year':      selected_year,
        'units_sold_today':   dash['units_sold_today'],
        'total_orders_today': dash['total_orders_today'],
        'weekly_revenue':     dash['weekly_revenue'],
        'monthly_revenue':    dash['monthly_revenue'],
        'top_product':        top_product,
        'ref_date':           dash['ref_date'],
        'week_start':         dash['week_start'],
        'month_start':        dash['month_start'],
        'data_is_current':    dash['data_is_current'],
        'has_data':           dash['has_data'],
    })

# ─────────────────────────────────────────────────────────────
# STAFF REQUEST APPROVALS
# ─────────────────────────────────────────────────────────────

@login_required
def approvals_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    pending_inventory = InventoryRequest.objects.filter(status='pending').order_by('-created_at')
    pending_events    = EventRequest.objects.filter(status='pending').order_by('-created_at')
    pending_csv       = SalesUploadRequest.objects.filter(status='pending').order_by('-created_at')

    all_inventory = InventoryRequest.objects.exclude(status='pending').order_by('-created_at')[:20]
    all_events    = EventRequest.objects.exclude(status='pending').order_by('-created_at')[:20]
    all_csv       = SalesUploadRequest.objects.exclude(status='pending').order_by('-created_at')[:20]

    pending_count = pending_inventory.count() + pending_events.count() + pending_csv.count()

    return render(request, 'OWNER/approvals.html', {
        'pending_inventory': pending_inventory,
        'pending_events':    pending_events,
        'pending_csv':       pending_csv,
        'all_inventory':     all_inventory,
        'all_events':        all_events,
        'all_csv':           all_csv,
        'pending_count':     pending_count,
    })


@login_required
def approve_inventory_request(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    req    = get_object_or_404(InventoryRequest, pk=pk)
    action = request.POST.get('action')

    if action == 'approve':
        from inventory.models import InventoryAuditLog
        from inventory.views import default_restock_threshold

        qty      = float(req.stock_qty or 0)
        existing = Inventory.objects.filter(item_name__iexact=req.item_name.strip(), category=req.category).first()

        if existing and existing.unit != req.unit:
            messages.error(request, f'"{req.item_name}" already exists in {existing.unit}, but the request is in '
                                    f'{req.unit}. Adjust the stock manually in Inventory instead.')
            return redirect('view-approvals')

        if existing:
            # A request for an item we already stock is a restock, not a new row.
            existing.stock_qty   = float(existing.stock_qty) + qty
            existing.total_stock = float(existing.total_stock) + qty
            existing.save()
            item, action_code, note = existing, 'restock', f"Approved staff request from {req.requested_by}"
            msg = f'"{req.item_name}" approved — added {qty:g} {req.unit} to existing stock.'
        else:
            item = Inventory.objects.create(
                item_name=req.item_name.strip(),
                total_stock=qty,
                stock_qty=qty,
                unit=req.unit,
                category=req.category,
                restock_threshold=default_restock_threshold(),
                stock_as_of=tz.localdate(),
            )
            action_code, note = 'initial', f"New item from staff request by {req.requested_by}"
            msg = (f'"{req.item_name}" approved and added to inventory. '
                   f'Set its unit cost and package size in Inventory → Edit.')

        InventoryAuditLog.objects.create(
            inventory=item, item_name=item.item_name, action=action_code,
            qty_change=qty, unit=item.unit, performed_by=request.user, notes=note,
        )
        req.status = 'approved'
        req.save()
        messages.success(request, msg)
    elif action == 'reject':
        reason = request.POST.get('reject_reason', '').strip()
        if not reason:
            messages.error(request, 'Please state a reason for rejecting this request.')
            return redirect('view-approvals')

        req.status = 'rejected'
        req.rejection_reason = reason
        req.save()

        from accounts.views import _raise_alert
        _raise_alert(
            req.requested_by, 'rejected', f'inv_rejected:{req.pk}',
            f'Inventory Request Rejected: {req.item_name}',
            f'Your request for "{req.item_name}" ({req.stock_qty} {req.unit}) was rejected. Reason: {reason}',
            link='/staff/'
        )
        messages.warning(request, f'"{req.item_name}" request rejected.')

    return redirect('view-approvals')


@login_required
def approve_event_request(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    req    = get_object_or_404(EventRequest, pk=pk)
    action = request.POST.get('action')

    if action == 'approve':
        from .models import OwnerEvent
        OwnerEvent.objects.create(
            event_name=req.event_name,
            event_date=req.event_date,
            description=req.description,
            location=req.location,
            start_time=req.start_time,
            end_date=req.end_date,
            end_time=req.end_time,
        )
        req.status = 'approved'
        req.save()
        messages.success(request, f'Event "{req.event_name}" approved and published.')
    elif action == 'reject':
        reason = request.POST.get('reject_reason', '').strip()
        if not reason:
            messages.error(request, 'Please state a reason for rejecting this request.')
            return redirect('view-approvals')

        req.status = 'rejected'
        req.rejection_reason = reason
        req.save()

        from accounts.views import _raise_alert
        _raise_alert(
            req.requested_by, 'rejected', f'event_rejected:{req.pk}',
            f'Event Request Rejected: {req.event_name}',
            f'Your event request "{req.event_name}" was rejected. Reason: {reason}',
            link='/staff/'
        )
        messages.warning(request, f'Event "{req.event_name}" request rejected.')

    return redirect('view-approvals')


@login_required
def approve_sales_upload(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    req    = get_object_or_404(SalesUploadRequest, pk=pk)
    action = request.POST.get('action')

    if action == 'approve':
        try:
            req.csv_file.open('rb')
            decoded = req.csv_file.read().decode('utf-8-sig')
            req.csv_file.close()

            reader    = csv.DictReader(io.StringIO(decoded))
            rows_list = list(reader)

            required_cols = {'Date', 'Product Name', 'Quantity', 'Unit Price'}
            actual_cols   = set(reader.fieldnames or [])
            missing       = required_cols - actual_cols
            if missing:
                messages.error(
                    request,
                    f'"{req.original_filename}" is missing required columns: '
                    f'{", ".join(missing)}. Reject it and ask for a corrected file.'
                )
                return redirect('view-approvals')

            created, skipped, errors, created_records, notes = _process_csv_rows(rows_list)

            req.status        = 'approved'
            req.records_added = created
            req.reviewed_at   = tz.now()
            req.save()

            msg = f'CSV approved. {created} new record(s) imported. Na-approve na.'
            if skipped:
                msg += f' {skipped} product-day(s) were already uploaded and were skipped.'
            messages.success(request, msg)
            _report_csv_problems(request, errors, notes)

            _apply_recipe_deduction(request, created_records)

        except Exception as e:
            messages.error(request, f'Failed to process CSV: {e}')

    elif action == 'reject':
        req.status      = 'rejected'
        req.reviewed_at = tz.now()
        req.save()
        messages.warning(request, f'"{req.original_filename}" upload rejected.')

    return redirect('view-approvals')


# ─────────────────────────────────────────────────────────────
# WEATHER HELPER
# ─────────────────────────────────────────────────────────────

def get_historical_temp(date_str, lat=None, lon=None):
    if lat is None or lon is None:
        config = SystemSetting.objects.first()
        lat = config.store_lat if config else 14.4667
        lon = config.store_lon if config else 121.1833

    url = (
        f'https://archive-api.open-meteo.com/v1/archive'
        f'?latitude={lat}&longitude={lon}'
        f'&start_date={date_str}&end_date={date_str}'
        f'&daily=temperature_2m_mean&timezone=Asia%2FManila'
    )
    try:
        res  = requests.get(url, timeout=5)
        data = res.json()
        return data['daily']['temperature_2m_mean'][0]
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────
# ADMIN MANAGEMENT
# ─────────────────────────────────────────────────────────────

# ─────────────────────────────────────────────────────────────
# ADMIN MANAGEMENT
# ─────────────────────────────────────────────────────────────

@login_required
def admin_management_view(request):
    User = get_user_model()

    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('owner-dashboard')

    if request.method == 'POST' and 'add_user' in request.POST:
        uname  = request.POST.get('username', '').strip()
        email  = request.POST.get('email', '').strip()
        passw  = request.POST.get('password', '')
        cpassw = request.POST.get('confirm_password', '')
        role   = request.POST.get('role', 'Staff')

        if passw != cpassw:
            messages.error(request, 'Passwords do not match.')
            return redirect('admin-management')
        if User.objects.filter(username=uname).exists():
            messages.error(request, f'Username "{uname}" already exists.')
            return redirect('admin-management')
        if User.objects.filter(email=email).exists():
            messages.error(request, f'Email "{email}" already in use.')
            return redirect('admin-management')

        new_user           = User.objects.create_user(username=uname, email=email, password=passw)
        new_user.role      = role.upper()
        new_user.is_active = False
        new_user.save()

        invite       = StaffInvite.objects.create(user=new_user)
        activate_url = f'{settings.SITE_BASE_URL}/owner/approve-staff/{invite.token}/'

        try:
            send_mail(
                subject='[CraveCast] Your Account & Activation Link',
                message=(
                    f'Hi {uname},\n\n'
                    f'An account has been created for you on CraveCast.\n\n'
                    f'Username: {uname}\n'
                    f'Temporary Password: {passw}\n\n'
                    f'Click the link below to activate your account:\n'
                    f'{activate_url}\n\n'
                    f'For security, please change your password after your first login.\n\n'
                    f'— CraveCast Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[email],
                fail_silently=False,
            )
            ActivityLog.objects.create(
                username=request.user.username,
                action='ADD_USER',
                action_details=f'Created {uname} ({role.upper()})',
            )
            messages.success(request, f'Account "{uname}" created! Activation email sent to {email}.')
        except Exception as e:
            new_user.delete()
            messages.error(request, f'Failed to send activation email: {e}')

        return redirect('admin-management')

    if 'delete_user' in request.GET:
        user_to_delete = get_object_or_404(User, id=request.GET.get('delete_user'))
        if user_to_delete != request.user:
            uname = user_to_delete.username
            user_to_delete.delete()
            ActivityLog.objects.create(
                username=request.user.username,
                action='DELETE_USER',
                action_details=f'Deleted user: {uname}',
            )
            messages.success(request, f'User "{uname}" deleted.')
        return redirect('admin-management')

    if 'resend_approval' in request.GET:
        target       = get_object_or_404(User, pk=request.GET['resend_approval'])
        invite       = get_object_or_404(StaffInvite, user=target, approved=False)
        activate_url = f'{settings.SITE_BASE_URL}/owner/approve-staff/{invite.token}/'
        try:
            send_mail(
                subject='[CraveCast] Activate Your Account',
                message=(
                    f'Hi {target.username},\n\n'
                    f'Here is your activation link:\n{activate_url}\n\n'
                    f'— CraveCast Team'
                ),
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[target.email],
                fail_silently=False,
            )
            messages.success(request, f'Activation email re-sent to {target.email}.')
        except Exception as e:
            messages.error(request, f'Failed to resend: {e}')
        return redirect('admin-management')

    users = User.objects.all()
    logs  = ActivityLog.objects.all().order_by('-timestamp')[:10]
    return render(request, 'OWNER/admin_login.html', {'users': users, 'logs': logs})


# ─────────────────────────────────────────────────────────────
# STAFF ACTIVATION FLOW
# ─────────────────────────────────────────────────────────────

def approve_staff_account(request, token):
    # Try to find the invite — already approved or invalid token
    try:
        invite = StaffInvite.objects.get(token=token)
    except StaffInvite.DoesNotExist:
        return render(request, 'OWNER/approve_staff_result.html', {
            'success': False,
            'reason': 'invalid',
            'message': 'This activation link is invalid or does not exist.',
        })

    if invite.approved:
        return render(request, 'OWNER/approve_staff_result.html', {
            'success': False,
            'reason': 'already_used',
            'message': f'This link has already been used. "{invite.user.username}" is already active.',
        })

    # Don't activate yet — let the employee set their own password first
    return redirect('set_initial_password', token=token)


def set_initial_password(request, token):
    try:
        invite = StaffInvite.objects.get(token=token, approved=False)
    except StaffInvite.DoesNotExist:
        return render(request, 'OWNER/approve_staff_result.html', {
            'success': False,
            'reason': 'invalid',
            'message': 'This activation link is invalid or has already been used.',
        })

    user = invite.user

    if request.method == 'POST':
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')

        if len(password1) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
        elif password1 != password2:
            messages.error(request, 'Passwords do not match.')
        else:
            user.set_password(password1)
            user.is_active = True
            user.save()

            invite.approved = True
            invite.save()

            ActivityLog.objects.create(
                username=user.username,
                action='ACTIVATED',
                action_details=f'{user.username} set their password and activated their account.',
            )

            return render(request, 'OWNER/approve_staff_result.html', {
                'success': True,
                'username': user.username,
                'message': f'✓ {user.username}, your account is now active! You can log in with your new password.',
            })

    return render(request, 'OWNER/set_password.html', {'token': token, 'username': user.username})


# ─────────────────────────────────────────────────────────────
# SETTINGS
# ─────────────────────────────────────────────────────────────

@login_required
def settings_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    config, _ = SystemSetting.objects.get_or_create(id=1)

    if request.method == 'POST':
        if 'update_config' in request.POST:
            def _num(name, cast, current):
                raw = (request.POST.get(name) or '').strip()
                if raw == '':
                    return current          # left blank → keep what's saved
                try:
                    return cast(raw)
                except ValueError:
                    raise ValueError(name)

            try:
                threshold = _num('stock_threshold', lambda v: int(float(v)), config.stock_threshold)
                lat       = _num('store_lat', float, config.store_lat)
                lon       = _num('store_lon', float, config.store_lon)
            except ValueError as bad:
                messages.error(request, f'Please enter a valid number for {str(bad).replace("_", " ")}.')
                return redirect('view-settings')
            if threshold is not None and threshold < 0:
                messages.error(request, 'Default low-stock limit must be 0 or more.')
                return redirect('view-settings')

            config.store_name      = (request.POST.get('store_name') or '').strip() or config.store_name
            config.contact_number  = request.POST.get('contact_number', config.contact_number) or ''
            config.stock_threshold = threshold
            config.store_lat       = lat
            config.store_lon       = lon
            # The key box is never pre-filled (it's a secret), so blank means
            # "keep the saved key" — not "delete it".
            new_key = request.POST.get('weather_api_key', '').strip()
            if new_key:
                config.weather_api_key = new_key
            config.save()
            messages.success(request, 'Configuration updated.')

        elif 'update_email' in request.POST:
            new_email     = request.POST.get('new_email', '').strip()
            current_pass  = request.POST.get('current_password_for_email')

            if not new_email:
                messages.error(request, 'Please enter an email address. Maglagay ng email address.')
            elif not request.user.check_password(current_pass):
                messages.error(request, 'Incorrect current password. Mali ang kasalukuyang password.')
            elif User.objects.filter(email=new_email).exclude(pk=request.user.pk).exists():
                messages.error(request, 'That email is already in use by another account. Nagamit na ang email na iyan.')
            else:
                request.user.email = new_email
                request.user.save(update_fields=['email'])
                messages.success(request, f'Account email updated to {new_email}. Na-update na ang email.')

        elif 'update_password' in request.POST:
            current_pass = request.POST.get('current_password')
            new_pass     = request.POST.get('new_password')
            confirm_pass = request.POST.get('confirm_password')

            if request.user.check_password(current_pass):
                if new_pass == confirm_pass:
                    request.user.set_password(new_pass)
                    request.user.save()
                    update_session_auth_hash(request, request.user)
                    messages.success(request, 'Password changed.')
                else:
                    messages.error(request, 'New passwords do not match.')
            else:
                messages.error(request, 'Incorrect current password.')

        return redirect('view-settings')

    return render(request, 'OWNER/settings.html', {'config': config})


# ─────────────────────────────────────────────────────────────
# EMPLOYEES
# ─────────────────────────────────────────────────────────────

@login_required
def employees_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    User      = get_user_model()
    employees = User.objects.filter(role='STAFF', is_active=True).select_related('profile')

    for emp in employees:
        if not hasattr(emp, 'profile'):
            EmployeeProfile.objects.create(user=emp)

    employees = User.objects.filter(role='STAFF', is_active=True).select_related('profile')
    expiring  = [emp for emp in employees if hasattr(emp, 'profile') and emp.profile.contract_status == 'expiring_soon']

    return render(request, 'OWNER/employees.html', {
        'employees': employees,
        'expiring':  expiring,
    })


@login_required
def employee_edit(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    User       = get_user_model()
    emp        = get_object_or_404(User, pk=pk, role='STAFF')
    profile, _ = EmployeeProfile.objects.get_or_create(user=emp)

    if request.method == 'POST':
        emp.full_name = request.POST.get('full_name', '').strip()
        emp.email     = request.POST.get('email', '').strip()
        emp.save()

        profile.phone         = request.POST.get('phone', '').strip()
        profile.address       = request.POST.get('address', '').strip()
        profile.contract_type = request.POST.get('contract_type', '6months')

        date_hired_str = request.POST.get('date_hired', '')
        if date_hired_str:
            profile.date_hired = datetime.strptime(date_hired_str, '%Y-%m-%d').date()

        wage_amount_str = request.POST.get('wage_amount', '').strip()
        profile.wage_amount   = wage_amount_str or None
        profile.wage_schedule = request.POST.get('wage_schedule', 'biweekly')

        last_payment_str = request.POST.get('last_payment_date', '')
        if last_payment_str:
            profile.last_payment_date = datetime.strptime(last_payment_str, '%Y-%m-%d').date()

        if 'photo' in request.FILES:
            profile.photo = request.FILES['photo']

        profile.save()
        messages.success(request, f"{emp.full_name or emp.username}'s profile updated!")
        return redirect('view-employees')

    return render(request, 'OWNER/employee_edit.html', {'emp': emp, 'profile': profile})


@login_required
def toggle_archive_event(request, pk):
    if getattr(request.user, 'role', '').strip().upper() != 'OWNER':
        messages.error(request, 'Access Denied.')
        return redirect('view-events')

    try:
        from owner.models import OwnerEvent
        event = OwnerEvent.objects.get(pk=pk)
        event.is_archived = not event.is_archived
        event.save()
        action = 'archived' if event.is_archived else 'restored'
        messages.success(request, f'"{event.event_name}" has been {action}.')
    except OwnerEvent.DoesNotExist:
        messages.error(request, 'Event not found.')

    return redirect('view-events')