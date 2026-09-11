from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.utils import timezone
from datetime import timedelta
from .models import Inventory, InventoryAuditLog
from owner.models import InventoryRequest
from owner.views import _get_dashboard_context
import csv
from django.http import HttpResponse


@login_required
def inventory_view(request):
    current_tab = request.GET.get('tab', 'Stock')

    # ── OWNER POST ──────────────────────────────────────────────────────────
    if request.method == "POST" and request.user.role == 'OWNER':
        form_type = request.POST.get('form_type', '')
        tab       = request.POST.get('category', current_tab)

        # ① ADD NEW ENTRY
        if form_type == 'add_entry':
            item_name         = request.POST.get('item_name', '').strip()
            total_stock       = request.POST.get('total_stock')
            unit              = request.POST.get('unit', 'pcs')
            category          = request.POST.get('category', 'Stock')
            restock_threshold = request.POST.get('restock_threshold') or 20
            expiry_date       = request.POST.get('expiry_date') or None

            if not item_name or not total_stock:
                messages.error(request, "Item name and initial stock are required. Kailangan ang item name at starting stock.")
                return redirect(f'/inventory/?tab={category}')

            item = Inventory.objects.create(
                item_name         = item_name,
                total_stock       = total_stock,
                stock_qty         = total_stock,   # current = initial on first entry
                unit              = unit,
                category          = category,
                restock_threshold = restock_threshold,
                expiry_date       = expiry_date if category == 'Stock' else None,
            )

            InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'initial',
                qty_change   = item.total_stock,
                unit         = item.unit,
                performed_by = request.user,
                notes        = "Initial stock entry",
            )

            messages.success(request, f"{item_name} added to inventory. Naidagdag sa imbentaryo.")
            return redirect(f'/inventory/?tab={category}')

        # ② EDIT ENTRY
        elif form_type == 'edit_entry':
            item = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            item.item_name         = request.POST.get('item_name', item.item_name).strip()
            item.total_stock       = request.POST.get('total_stock', item.total_stock)
            item.unit              = request.POST.get('unit', item.unit)
            item.restock_threshold = request.POST.get('restock_threshold') or item.restock_threshold
            if item.category == 'Stock':
                item.expiry_date = request.POST.get('expiry_date') or None
            item.save()

            messages.success(request, f"{item.item_name} updated successfully. Na-update na.")
            return redirect(f'/inventory/?tab={item.category}')

        # ③ ADD STOCK (Restock)
        elif form_type == 'add_stock':
            item      = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            qty_to_add = float(request.POST.get('qty_to_add', 0) or 0)
            supplier  = request.POST.get('supplier', '').strip()
            notes     = request.POST.get('notes', '').strip()
            date_recv = request.POST.get('date_received') or None

            if qty_to_add <= 0:
                messages.error(request, "Quantity to add must be greater than 0. Dapat mas mataas sa 0 ang idadagdag na quantity.")
                return redirect(f'/inventory/?tab={item.category}')

            item.stock_qty    = float(item.stock_qty) + qty_to_add
            item.total_stock  = float(item.total_stock) + qty_to_add
            item.save()

            note_parts = []
            if supplier:
                note_parts.append(f"Supplier: {supplier}")
            if notes:
                note_parts.append(notes)
            if date_recv:
                note_parts.append(f"Date received: {date_recv}")

            InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'restock',
                qty_change   = qty_to_add,
                unit         = item.unit,
                performed_by = request.user,
                notes        = " | ".join(note_parts) if note_parts else None,
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

            InventoryAuditLog.objects.create(
                inventory    = item,
                item_name    = item.item_name,
                action       = 'waste',
                qty_change   = qty_wasted,
                unit         = item.unit,
                performed_by = request.user,
                notes        = full_note,
            )

            messages.success(request, f"Logged {qty_wasted} {item.unit} waste for {item.item_name}. Naitala ang sayang na stock.")
            return redirect(f'/inventory/?tab={item.category}')

        # ⑤ LEGACY fallback (delete)
        elif form_type == 'delete':
            item = get_object_or_404(Inventory, pk=request.POST.get('item_id'))
            name = item.item_name
            item.delete()
            messages.success(request, f"{name} deleted.")
            return redirect(f'/inventory/?tab={current_tab}')

        else:
            messages.error(request, "Unknown form action.")
            return redirect(f'/inventory/?tab={current_tab}')

    # ── GET ─────────────────────────────────────────────────────────────────
    dash            = _get_dashboard_context()
    inventory_items = Inventory.objects.filter(category=current_tab).order_by('item_name')
    today           = timezone.now().date()

    # Annotate expiry flags on each item (Ingredients only)
    for item in inventory_items:
        if item.category == 'Stock' and item.expiry_date:
            item.is_expired  = item.expiry_date < today
            item.expiry_soon = (not item.is_expired) and (item.expiry_date <= today + timedelta(days=7))
        else:
            item.is_expired  = False
            item.expiry_soon = False

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