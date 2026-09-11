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
  8. FIXED: weekly_sales now uses actual calendar week (Mon–Sun) of ref_date
     → Prevents weekly == monthly when all data falls in the same month
"""

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash, get_user_model
from django.contrib.auth.decorators import login_required
from django.core.mail import send_mail
from .models import SystemSetting, StaffInvite, InventoryRequest, EventRequest, SalesUploadRequest
from accounts.models import ActivityLog, EmployeeProfile
from sales.models import SalesRecord
from django.db.models import Sum, F, Min, Max
from django.utils import timezone
from django.utils import timezone as tz
from datetime import timedelta, datetime, date
from inventory.models import Inventory
from inventory.services import deduct_inventory_for_sales
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

    # ── DAILY: Average revenue per day = Total Revenue ÷ Days with data ──
    total_revenue_all = float(
        SalesRecord.objects.aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or 0
    )
    total_units_all  = SalesRecord.objects.aggregate(total=Sum('quantity'))['total'] or 0
    total_orders_all = SalesRecord.objects.count()
    days_with_data   = max(SalesRecord.objects.values('sale_date').distinct().count(), 1)

    revenue_daily = round(total_revenue_all / days_with_data, 2)
    units_daily   = int(total_units_all / days_with_data)
    orders_daily  = int(total_orders_all / days_with_data)

    # ── WEEKLY: Sum of sales in calendar week (Mon–Sun) of ref_date ──
    week_start = ref_date - timedelta(days=ref_date.weekday())  # Monday
    week_end   = week_start + timedelta(days=6)                  # Sunday

    revenue_7d = float(
        SalesRecord.objects.filter(
            sale_date__gte=week_start,
            sale_date__lte=week_end
        ).aggregate(total=Sum(F('quantity') * F('price')))['total'] or 0
    )
    units_7d  = SalesRecord.objects.filter(
        sale_date__gte=week_start,
        sale_date__lte=week_end
    ).aggregate(total=Sum('quantity'))['total'] or 0
    orders_7d = SalesRecord.objects.filter(
        sale_date__gte=week_start,
        sale_date__lte=week_end
    ).count()

    # ── MONTHLY: Sum of sales in calendar month of ref_date ──
    ref_month_start = ref_date.replace(day=1)
    if ref_date.month == 12:
        ref_month_end = ref_date.replace(year=ref_date.year + 1, month=1, day=1) - timedelta(days=1)
    else:
        ref_month_end = ref_date.replace(month=ref_date.month + 1, day=1) - timedelta(days=1)

    revenue_monthly = float(
        SalesRecord.objects.filter(
            sale_date__gte=ref_month_start,
            sale_date__lte=ref_month_end
        ).aggregate(total=Sum(F('quantity') * F('price')))['total'] or 0
    )
    units_monthly  = SalesRecord.objects.filter(
        sale_date__gte=ref_month_start,
        sale_date__lte=ref_month_end
    ).aggregate(total=Sum('quantity'))['total'] or 0
    orders_monthly = SalesRecord.objects.filter(
        sale_date__gte=ref_month_start,
        sale_date__lte=ref_month_end
    ).count()

    revenue_all = float(
        SalesRecord.objects.aggregate(
            total=Sum(F('quantity') * F('price')))['total'] or 0
    )

    low_stock_qs    = Inventory.objects.filter(stock_qty__lte=F('restock_threshold')).order_by('stock_qty')
    low_stock_count = low_stock_qs.count()

    top_products = (
        SalesRecord.objects
        .values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:3]
    )
    top_product_labels = [p['product_name'] for p in top_products]
    top_product_values = [p['total_qty']    for p in top_products]

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
        'ref_date':            ref_date,
        'earliest_date':       earliest_date,
        'has_data':            has_data,
        'revenue_all':         revenue_all,
    }


# ─────────────────────────────────────────────────────────────
# OWNER DASHBOARD
# ─────────────────────────────────────────────────────────────

@login_required
def owner_dashboard_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')
    context = _get_dashboard_context()
    return render(request, 'OWNER/owner.html', context)


# ─────────────────────────────────────────────────────────────
# INVENTORY
# ─────────────────────────────────────────────────────────────

@login_required
def inventory_view(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        return redirect('staff-dashboard')

    if request.method == 'POST':
        item_name   = request.POST.get('item_name', '').strip()
        total_stock = request.POST.get('total_stock')
        stock_qty   = request.POST.get('stock_qty')
        unit        = request.POST.get('unit', '').strip()
        category    = request.POST.get('category', 'Stock')

        if item_name and total_stock and stock_qty:
            Inventory.objects.create(
                item_name=item_name,
                total_stock=total_stock,
                stock_qty=stock_qty,
                unit=unit,
                category=category,
            )
            messages.success(request, f'"{item_name}" added to inventory.')
        else:
            messages.error(request, 'Please fill in all required fields.')
        return redirect('view-inventory')

    current_tab = request.GET.get('tab', 'Stock')
    inventory   = Inventory.objects.filter(category=current_tab).order_by('item_name')
    dash        = _get_dashboard_context()

    context = {
        'inventory':     inventory,
        'current_tab':   current_tab,
        'daily_sales':   dash['daily_sales'],
        'weekly_sales':  dash['weekly_sales'],
        'monthly_sales': dash['monthly_sales'],
    }
    return render(request, 'OWNER/inventory.html', context)


# ─────────────────────────────────────────────────────────────
# UPLOAD DATA — with deduplication + batched temp fetch
# ─────────────────────────────────────────────────────────────

def _process_csv_rows(rows_list):
    """
    Shared CSV processing logic used by both upload_view and approve_sales_upload.
    Returns (created_count, skipped_count, error_list, created_records) — the
    last one is the list of newly-inserted SalesRecord objects, used to drive
    automatic ingredient deduction for this batch only (not re-deducting for
    rows that were already in the database).
    """
    # ── 1. Pre-parse all rows ──────────────────────────────────
    parsed = []
    parse_errors = []

    for row in rows_list:
        try:
            date_val  = row.get('Date', '').strip()
            prod_name = row.get('Product Name', '').strip()
            qty_raw   = row.get('Quantity', '').strip()
            price_raw = row.get('Unit Price', '').strip()

            if not date_val or not prod_name:
                continue

            qty   = int(qty_raw)
            price = float(price_raw)
            sale_date = datetime.strptime(date_val, '%Y-%m-%d').date()
            parsed.append((date_val, prod_name, qty, price, sale_date))

        except Exception as e:
            parse_errors.append(f"Parse error: {e} | row={row}")
            continue

    # ── 2. Batch-fetch temperatures (1 API call per unique date) ──
    unique_dates = {p[0] for p in parsed}
    temp_cache = {}
    for d in unique_dates:
        temp_cache[d] = get_historical_temp(d) or 30.0

    # ── 3. Insert with deduplication ──────────────────────────
    created = 0
    skipped = 0
    created_records = []

    for date_val, prod_name, qty, price, sale_date in parsed:
        try:
            record, was_created = SalesRecord.objects.get_or_create(
                # Unique key: product + date only
                product_name=prod_name,
                sale_date=sale_date,
                defaults={
                    'quantity': qty,
                    'price':    price,
                    'temp_c':   temp_cache.get(date_val, 30.0),
                }
            )
            if was_created:
                created += 1
                created_records.append(record)
            else:
                skipped += 1
        except Exception as e:
            parse_errors.append(f"DB error: {e} | product={prod_name} date={date_val}")
            continue

    return created, skipped, parse_errors, created_records


def _apply_recipe_deduction(request, created_records):
    """
    Auto-deducts ingredient stock for a freshly-inserted batch of SalesRecords,
    based on each product's recipe (ProductRecipe/RecipeIngredient). Runs
    immediately as part of import — no separate owner approval step for the
    deduction itself. Surfaces a short summary via messages.
    """
    if not created_records:
        return

    summary = deduct_inventory_for_sales(created_records)

    if summary['deducted']:
        messages.success(
            request,
            f"Inventory deducted for {len(summary['deducted'])} "
            f"product{'s' if len(summary['deducted']) != 1 else ''} based on their recipes. "
            f"Nabawasan ang stock base sa recipe."
        )
    if summary['no_recipe']:
        messages.warning(
            request,
            f"No recipe defined for: {', '.join(summary['no_recipe'])}. "
            f"Inventory was not deducted for these. Walang recipe kaya hindi nabawasan ang stock."
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

        created, skipped, errors, created_records = _process_csv_rows(rows_list)

        msg = f'Upload complete. {created} new record(s) added. Kumpleto ang pag-upload.'
        if skipped:
            msg += f' {skipped} duplicate(s) skipped.'
        if errors:
            msg += f' {len(errors)} row(s) had errors and were skipped.'
        messages.success(request, msg)

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
        Inventory.objects.create(
            item_name=req.item_name,
            total_stock=req.total_stock,
            stock_qty=req.stock_qty,
            unit=req.unit,
            category=req.category,
        )
        req.status = 'approved'
        req.save()
        messages.success(request, f'"{req.item_name}" approved and added to inventory.')
    elif action == 'reject':
        req.status = 'rejected'
        req.save()
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
        )
        req.status = 'approved'
        req.save()
        messages.success(request, f'Event "{req.event_name}" approved and published.')
    elif action == 'reject':
        req.status = 'rejected'
        req.save()
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

            created, skipped, errors, created_records = _process_csv_rows(rows_list)

            req.status        = 'approved'
            req.records_added = created
            req.reviewed_at   = tz.now()
            req.save()

            msg = f'CSV approved. {created} new record(s) imported. Na-approve na.'
            if skipped:
                msg += f' {skipped} duplicate(s) skipped.'
            messages.success(request, msg)

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

def get_historical_temp(date_str, lat=14.4667, lon=121.1833):
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
        activate_url = request.build_absolute_uri(f'/owner/approve-staff/{invite.token}/')

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
                from_email='CraveCast Security <cravecast26@gmail.com>',
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
        activate_url = request.build_absolute_uri(f'/owner/approve-staff/{invite.token}/')
        try:
            send_mail(
                subject='[CraveCast] Activate Your Account',
                message=(
                    f'Hi {target.username},\n\n'
                    f'Here is your activation link:\n{activate_url}\n\n'
                    f'— CraveCast Team'
                ),
                from_email='CraveCast Security <cravecast26@gmail.com>',
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
            config.store_name      = request.POST.get('store_name')
            config.contact_number  = request.POST.get('contact_number')
            config.stock_threshold = request.POST.get('stock_threshold')
            config.weather_api_key = request.POST.get('weather_api_key')
            config.forecast_mode   = request.POST.get('forecast_mode')
            config.store_lat       = request.POST.get('store_lat')
            config.store_lon       = request.POST.get('store_lon')
            config.save()
            messages.success(request, 'Configuration updated.')

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