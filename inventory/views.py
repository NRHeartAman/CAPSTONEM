from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from django.db.models.deletion import ProtectedError
from datetime import timedelta
from .models import Inventory, InventoryAuditLog
from owner.models import InventoryRequest
from owner.views import _get_dashboard_context
import csv
from django.http import HttpResponse


def default_restock_threshold():
    """Settings → Default low-stock limit, used when a new item is added
    without its own limit (instead of a number hard-coded in the code)."""
    from owner.models import SystemSetting
    config = SystemSetting.objects.first()
    return config.stock_threshold if config and config.stock_threshold is not None else 0


@login_required
def inventory_view(request):
    current_tab = request.GET.get('tab', 'Stock')

    # ── POST ────────────────────────────────────────────────────────────────
    # log_waste is allowed for OWNER and STAFF (staff are the ones on the
    # floor who actually see a spill happen); every other action here
    # stays OWNER-only, enforced server-side (not just hidden in the UI).
    if request.method == "POST":
        form_type = request.POST.get('form_type', '')
        is_owner  = request.user.role == 'OWNER'

        if form_type != 'log_waste' and not is_owner:
            messages.error(request, "Access Denied. Owner-only action.")
            return redirect(f'/inventory/?tab={current_tab}')

        # ① ADD NEW ENTRY
        if form_type == 'add_entry':
            item_name         = request.POST.get('item_name', '').strip()
            total_stock       = request.POST.get('total_stock')
            unit              = request.POST.get('unit', 'pcs')
            category          = request.POST.get('category', 'Stock')
            restock_threshold = request.POST.get('restock_threshold') or default_restock_threshold()
            expiry_date       = request.POST.get('expiry_date') or None
            unit_cost         = request.POST.get('unit_cost') or None
            package_size      = request.POST.get('package_size') or None
            package_unit      = request.POST.get('package_unit') or ''
            max_stock         = request.POST.get('max_stock') or None

            if not item_name or not total_stock:
                messages.error(request, "Item name and initial stock are required. Kailangan ang item name at starting stock.")
                return redirect(f'/inventory/?tab={category}')

            # The DB column is varchar(255) — reject early with a clear
            # message instead of letting an oversized name reach the
            # database and crash the request with a raw DataError.
            if len(item_name) > 255:
                messages.error(request, "Item name is too long (max 255 characters).")
                return redirect(f'/inventory/?tab={category}')

            # Unit cost and package size (with its package type) are required
            # for new items — every ingredient/supply should be entered with
            # real purchasing info from the start, not backfilled later.
            if not unit_cost or not package_size or not package_unit:
                messages.error(request, "Unit Cost, Package Size, and Package Type are required for a new item.")
                return redirect(f'/inventory/?tab={category}')

            if float(unit_cost) <= 0 or float(package_size) <= 0:
                messages.error(request, "Unit Cost and Package Size must be greater than 0.")
                return redirect(f'/inventory/?tab={category}')

            if max_stock and float(max_stock) <= 0:
                messages.error(request, "Max Stock must be greater than 0 if provided.")
                return redirect(f'/inventory/?tab={category}')

            total_stock = float(total_stock)

            item = Inventory.objects.create(
                item_name         = item_name,
                total_stock       = total_stock,
                stock_qty         = total_stock,   # current = initial on first entry
                unit              = unit,
                category          = category,
                restock_threshold = restock_threshold,
                expiry_date       = expiry_date if category == 'Stock' else None,
                unit_cost         = float(unit_cost),
                package_size      = float(package_size),
                package_unit      = package_unit,
                max_stock         = float(max_stock) if max_stock else None,
                stock_as_of       = timezone.localdate(),
            )

            InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'initial',
                qty_change   = item.total_stock,
                unit         = item.unit,
                performed_by = request.user,
                notes        = "Initial stock entry",
                unit_cost    = item.unit_cost,
                total_cost   = round(item.total_stock * item.unit_cost, 2) if item.unit_cost else None,
            )

            messages.success(request, f"{item_name} added to inventory. Naidagdag sa imbentaryo.")
            return redirect(f'/inventory/?tab={category}')

        # ② EDIT ENTRY
        elif form_type == 'edit_entry':
            item = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            new_name = request.POST.get('item_name', item.item_name).strip()

            if len(new_name) > 255:
                messages.error(request, "Item name is too long (max 255 characters).")
                return redirect(f'/inventory/?tab={item.category}')

            unit_cost    = request.POST.get('unit_cost')
            package_size = request.POST.get('package_size')
            package_unit = request.POST.get('package_unit')
            max_stock    = request.POST.get('max_stock')

            for field_label, value in [("Unit Cost", unit_cost), ("Package Size", package_size), ("Max Stock", max_stock)]:
                if value and float(value) <= 0:
                    messages.error(request, f"{field_label} must be greater than 0 if provided.")
                    return redirect(f'/inventory/?tab={item.category}')

            # Recipes store amounts in this item's unit (e.g. 0.018 kg) — changing
            # kg→g here without converting would make every recipe 1000× off.
            new_unit = request.POST.get('unit', item.unit).strip() or item.unit
            if new_unit != item.unit and item.used_in_recipes.exists():
                messages.error(request, f"Can't change the unit of {item.item_name} from {item.unit} to {new_unit} — "
                                        f"it's used in recipes. Update those recipes first, or add a new item.")
                return redirect(f'/inventory/?tab={item.category}')

            # Stock count: the owner corrects Current Stock after a physical count.
            current_stock = request.POST.get('current_stock', '').strip()
            count_change  = None
            if current_stock != '':
                try:
                    counted = float(current_stock)
                except ValueError:
                    counted = -1
                if counted < 0:
                    messages.error(request, "Current stock must be 0 or more.")
                    return redirect(f'/inventory/?tab={item.category}')
                if abs(counted - float(item.stock_qty)) > 1e-9:
                    count_change = counted - float(item.stock_qty)
                    item.stock_qty = counted
                    item.stock_as_of = timezone.localdate()

            item.item_name          = new_name
            item.total_stock       = request.POST.get('total_stock', item.total_stock)
            item.unit              = new_unit
            item.restock_threshold = request.POST.get('restock_threshold') or item.restock_threshold
            if item.category == 'Stock':
                item.expiry_date = request.POST.get('expiry_date') or None

            item.unit_cost    = float(unit_cost) if unit_cost else None
            item.package_size = float(package_size) if package_size else None
            item.package_unit = package_unit or ''
            item.max_stock    = float(max_stock) if max_stock else None

            item.save()

            if count_change is not None:
                InventoryAuditLog.objects.create(
                    inventory=item, item_name=item.item_name, action='count',
                    qty_change=round(count_change, 3), unit=item.unit,
                    performed_by=request.user,
                    notes=f"Stock count: set to {item.stock_qty:g} {item.unit}",
                )

            messages.success(request, f"{item.item_name} updated successfully. Na-update na.")
            return redirect(f'/inventory/?tab={item.category}')

        # ③ ADD STOCK (Restock) — supports both small top-ups and bulk
        # purchases (packages × package_size), and records the real
        # purchase cost so Inventory.unit_cost reflects the last price paid.
        elif form_type == 'add_stock':
            item      = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            packages_bought = request.POST.get('packages') or None
            qty_to_add = float(request.POST.get('qty_to_add', 0) or 0)
            supplier  = request.POST.get('supplier', '').strip()
            notes     = request.POST.get('notes', '').strip()
            date_recv = request.POST.get('date_received') or None
            unit_cost_input = request.POST.get('unit_cost') or None

            # Bulk path: N packages of a known package_size overrides a
            # manually-typed quantity, when both are available.
            if packages_bought and item.package_size:
                packages_bought = float(packages_bought)
                qty_to_add = packages_bought * item.package_size
            else:
                packages_bought = float(packages_bought) if packages_bought else None

            if qty_to_add <= 0:
                messages.error(request, "Quantity to add must be greater than 0. Dapat mas mataas sa 0 ang idadagdag na quantity.")
                return redirect(f'/inventory/?tab={item.category}')

            item.stock_qty    = float(item.stock_qty) + qty_to_add
            item.total_stock  = float(item.total_stock) + qty_to_add

            unit_cost  = None
            total_cost = None
            if unit_cost_input:
                unit_cost  = float(unit_cost_input)
                total_cost = round(qty_to_add * unit_cost, 2)
                item.unit_cost = unit_cost  # last-purchase-price rule

            item.save()

            note_parts = []
            if supplier:
                note_parts.append(f"Supplier: {supplier}")
            if notes:
                note_parts.append(notes)
            if date_recv:
                note_parts.append(f"Date received: {date_recv}")

            audit_log = InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'restock',
                qty_change   = qty_to_add,
                unit         = item.unit,
                performed_by = request.user,
                notes        = " | ".join(note_parts) if note_parts else None,
                supplier     = supplier,
                packages     = packages_bought,
                unit_cost    = unit_cost,
                total_cost   = total_cost,
            )

            from accounts.models import User
            from accounts.views import _raise_alert
            for staff_user in User.objects.filter(role='STAFF', is_active=True):
                _raise_alert(
                    staff_user, 'restock', f'restock:{audit_log.pk}',
                    f'Restocked: {item.item_name}',
                    f'{request.user.username} added {qty_to_add} {item.unit} to {item.item_name}. New total: {item.stock_qty} {item.unit}.',
                    link=f'/inventory/?tab={item.category}#inv-item-{item.pk}'
                )

            messages.success(request, f"Added {qty_to_add} {item.unit} to {item.item_name}. Naidagdag ang stock.")
            return redirect(f'/inventory/?tab={item.category}')

        # ④ LOG WASTE / SPOILAGE
        elif form_type == 'log_waste':
            item       = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            qty_wasted = float(request.POST.get('qty_wasted', 0) or 0)
            waste_type = request.POST.get('waste_type', 'other')
            notes      = request.POST.get('notes', '').strip()

            if qty_wasted <= 0:
                messages.error(request, "Waste quantity must be greater than 0. Dapat mas mataas sa 0 ang waste quantity.")
                return redirect(f'/inventory/?tab={item.category}')

            if qty_wasted > float(item.stock_qty):
                messages.error(request, f"Cannot waste more than current stock ({item.stock_qty} {item.unit}).")
                return redirect(f'/inventory/?tab={item.category}')

            item.stock_qty = float(item.stock_qty) - qty_wasted
            item.save()

            full_note = f"[{waste_type.upper()}] {notes}" if notes else f"[{waste_type.upper()}]"

            # Estimated loss at the item's last-known unit cost — only
            # computed (never shown to Staff) when a cost is on record.
            estimated_loss = round(qty_wasted * item.unit_cost, 2) if item.unit_cost else None

            InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'waste',
                qty_change   = qty_wasted,
                unit         = item.unit,
                performed_by = request.user,
                notes        = full_note,
                unit_cost    = item.unit_cost,
                total_cost   = estimated_loss,
            )

            messages.success(request, f"Logged {qty_wasted} {item.unit} waste for {item.item_name}. Naitala ang sayang na stock.")
            return redirect(f'/inventory/?tab={item.category}')

        # ⑤ LEGACY fallback (delete)
        elif form_type == 'delete':
            item = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            name = item.item_name
            try:
                item.delete()
                messages.success(request, f"{name} deleted.")
            except ProtectedError:
                messages.error(request, f"Cannot delete '{name}' — it's used in one or more recipes. Remove it from those recipes first.")
            return redirect(f'/inventory/?tab={current_tab}')

        else:
            messages.error(request, "Unknown form action.")
            return redirect(f'/inventory/?tab={current_tab}')

    # ── GET ─────────────────────────────────────────────────────────────────
    dash            = _get_dashboard_context()
    inventory_items = Inventory.objects.filter(category=current_tab).order_by('item_name')
    today           = timezone.localdate()

    # Annotate "expires within 7 days" (is_expired itself is a model property)
    for item in inventory_items:
        item.expiry_soon = bool(
            item.category == 'Stock' and item.expiry_date and not item.is_expired
            and item.expiry_date <= today + timedelta(days=7)
        )

    # Low-stock items across ALL categories for the banner
    all_items      = Inventory.objects.all()
    low_stock_items = [i for i in all_items if float(i.stock_qty) <= float(i.restock_threshold)]

    # Expiring soon for banner (Ingredients within 7 days)
    expiring_soon_items = [
        i for i in Inventory.objects.filter(category='Stock')
        if i.expiry_date and not i.expiry_date < today
        and i.expiry_date <= today + timedelta(days=7)
    ]

    # Recent audit logs (owner only, last 50)
    audit_logs = []
    if request.user.role == 'OWNER':
        audit_logs = InventoryAuditLog.objects.select_related('performed_by').order_by('-created_at')[:50]

    # Staff: my requests
    my_requests = []
    if request.user.role != 'OWNER':
        my_requests = InventoryRequest.objects.filter(
            requested_by=request.user
        ).order_by('-created_at')[:20]

    context = {
        'inventory':             inventory_items,
        'current_tab':           current_tab,
        'daily_sales':           dash['daily_sales'],
        'weekly_sales':          dash['weekly_sales'],
        'monthly_sales':         dash['monthly_sales'],
        'my_inventory_requests': my_requests,
        'low_stock_items':       low_stock_items,
        'expiring_soon_items':   expiring_soon_items,
        'audit_logs':            audit_logs,
    }
    return render(request, 'OWNER/inventory.html', context)


@login_required
def staff_inventory_request(request):
    if request.user.role == 'OWNER':
        return redirect('view-inventory')

    if request.method == 'POST':
        InventoryRequest.objects.create(
            item_name    = request.POST.get('item_name'),
            total_stock  = request.POST.get('total_stock'),
            stock_qty    = request.POST.get('total_stock'),  # same as total on request
            unit         = request.POST.get('unit'),
            category     = request.POST.get('category', 'Stock'),
            requested_by = request.user,
            status       = 'pending',
        )
        messages.success(request, "Request submitted! Waiting for owner approval.")

    return redirect(f"/inventory/?tab={request.POST.get('category', 'Stock')}")


@login_required
def export_sales_csv(request):
    is_owner   = getattr(request.user, 'role', '') == 'OWNER'
    can_export = getattr(request.user, 'can_export', False)

    if not is_owner and not can_export:
        messages.error(request, 'You do not have permission to export sales data.')
        return redirect('view-inventory')

    from sales.models import SalesRecord

    month = request.GET.get('month', '')
    year  = request.GET.get('year', '')

    qs = SalesRecord.objects.all().order_by('-sale_date', 'product_name')
    if month:
        qs = qs.filter(sale_date__month=month)
    if year:
        qs = qs.filter(sale_date__year=year)

    response = HttpResponse(content_type='text/csv')
    response['Content-Disposition'] = 'attachment; filename="cravecast_sales_export.csv"'

    writer = csv.writer(response)
    writer.writerow(['Date', 'Product Name', 'Quantity', 'Unit Price', 'Total Revenue', 'Temp (°C)'])

    for r in qs:
        writer.writerow([
            r.sale_date,
            r.product_name,
            r.quantity,
            f'{r.price:.2f}',
            f'{r.quantity * r.price:.2f}',
            getattr(r, 'temp_c', ''),
        ])

    return response