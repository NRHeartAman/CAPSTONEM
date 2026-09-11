from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F
from django.utils import timezone
from datetime import timedelta, datetime
from django.http import JsonResponse
from django.views.decorators.http import require_POST

from sales.models import SalesRecord
from owner.models import OwnerEvent, InventoryRequest, EventRequest
from inventory.models import Inventory
from staff.models import PrepTask
from staff.prep_utils import generate_daily_prep_tasks
import json
from django.contrib import messages
from django.core.mail import send_mail
from django.conf import settings


# ── DASHBOARD ─────────────────────────────────────────────────────────────────

@login_required
def staff_dashboard_view(request):
    current_role = getattr(request.user, 'role', '').strip().upper()

    if current_role == 'OWNER':
        return redirect('owner-dashboard')

    if current_role == 'STAFF' and not request.user.is_active:
        subject = f"[CraveCast] Access Alert: Staff '{request.user.username}' is attempting to log in"
        message = (
            f"Hi Owner,\n\n"
            f"Your staff member '{request.user.username}' ({request.user.email}) "
            f"just tried to access the CraveCast Dashboard.\n\n"
            f"They have been restricted. Go to Owner Accounts Management to activate them.\n\n"
            f"Security System Control,\nCraveCast Monitoring"
        )
        try:
            send_mail(subject, message, settings.DEFAULT_FROM_EMAIL,
                      ['cravecast26@gmail.com'], fail_silently=False)
        except Exception:
            pass
        return render(request, 'STAFF/awaiting_approval.html',
                      {'staff_username': request.user.username})

    today   = timezone.now().date()
    latest  = SalesRecord.objects.order_by('-sale_date').values_list('sale_date', flat=True).first()
    ref_date        = latest if latest else today
    ref_month_start = ref_date.replace(day=1)
    seven_days_ago  = ref_date - timedelta(days=6)

    daily_sold = SalesRecord.objects.filter(
        sale_date__gte=ref_month_start, sale_date__lte=ref_date
    ).aggregate(total=Sum('quantity'))['total'] or 0

    daily_orders = SalesRecord.objects.filter(
        sale_date__gte=ref_month_start, sale_date__lte=ref_date
    ).count()

    weekly_revenue = SalesRecord.objects.filter(
        sale_date__gte=seven_days_ago, sale_date__lte=ref_date
    ).aggregate(total=Sum(F('quantity') * F('price')))['total'] or 0

    inventory_queryset = Inventory.objects.all()
    total_items        = inventory_queryset.count()
    ingredient_count   = inventory_queryset.filter(category='Stock').count()
    supply_count       = inventory_queryset.filter(category='Supply').count()
    low_stock_query    = inventory_queryset.filter(stock_qty__lte=F('restock_threshold')).order_by('stock_qty')
    low_stock_count    = low_stock_query.count()
    low_stock_items    = low_stock_query[:5]

    top_sales = (
        SalesRecord.objects.filter(
            sale_date__gte=ref_month_start, sale_date__lte=ref_date
        )
        .values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:4]
    )
    max_qty = max([p['total_qty'] for p in top_sales], default=1)
    dynamic_best_sellers = [
        {
            'name':       item['product_name'],
            'qty':        item['total_qty'],
            'percentage': int((item['total_qty'] / max_qty) * 100),
        }
        for item in top_sales
    ]

    chart_labels = []
    chart_values = []
    cur_year  = ref_month_start.year
    cur_month = ref_month_start.month
    for _ in range(7):
        m_start = datetime(cur_year, cur_month, 1).date()
        m_end   = (datetime(cur_year, cur_month + 1, 1).date() - timedelta(days=1)
                   if cur_month != 12
                   else datetime(cur_year + 1, 1, 1).date() - timedelta(days=1))
        chart_labels.insert(0, m_start.strftime('%b %Y'))
        rev = SalesRecord.objects.filter(
            sale_date__gte=m_start, sale_date__lte=m_end
        ).aggregate(total=Sum(F('quantity') * F('price')))['total'] or 0
        chart_values.insert(0, float(rev))
        cur_month -= 1
        if cur_month == 0:
            cur_month = 12
            cur_year -= 1

    top_products = (
        SalesRecord.objects.values('product_name')
        .annotate(total_qty=Sum('quantity'))
        .order_by('-total_qty')[:3]
    )
    top_product_labels = [p['product_name'] for p in top_products]
    top_product_values = [p['total_qty']    for p in top_products]

    my_inventory_requests = InventoryRequest.objects.filter(
        requested_by=request.user
    ).order_by('-created_at')[:5]
    my_event_requests = EventRequest.objects.filter(
        requested_by=request.user
    ).order_by('-created_at')[:5]

    generate_daily_prep_tasks()
    prep_tasks       = PrepTask.objects.filter(date=today).order_by('-priority', 'title')
    prep_done_count  = prep_tasks.filter(is_done=True).count()
    prep_total_count = prep_tasks.count()

    context = {
        'daily_sold':            daily_sold,
        'daily_orders':          daily_orders,
        'weekly_sales':          weekly_revenue,
        'total_items':           total_items,
        'ingredient_count':      ingredient_count,
        'supply_count':          supply_count,
        'low_stock_count':       low_stock_count,
        'low_stock_items':       low_stock_items,
        'inventory':             inventory_queryset[:10],
        'best_sellers':          dynamic_best_sellers,
        'chart_labels':          json.dumps(chart_labels),
        'chart_values':          json.dumps(chart_values),
        'top_product_labels':    json.dumps(top_product_labels),
        'top_product_values':    json.dumps(top_product_values),
        'my_inventory_requests': my_inventory_requests,
        'my_event_requests':     my_event_requests,
        'prep_tasks':            prep_tasks,
        'prep_done_count':       prep_done_count,
        'prep_total_count':      prep_total_count,
    }
    return render(request, 'STAFF/staff.html', context)


@login_required
@require_POST
def toggle_prep_task(request, pk):
    task = get_object_or_404(PrepTask, pk=pk)
    task.is_done = not task.is_done
    task.save(update_fields=['is_done'])
    return JsonResponse({'is_done': task.is_done, 'pk': task.pk})


@login_required
def staff_inventory_request_view(request):
    if request.method == 'POST':
        item_name   = request.POST.get('item_name', '').strip()
        total_stock = request.POST.get('total_stock')
        stock_qty   = request.POST.get('stock_qty')
        unit        = request.POST.get('unit', '').strip()
        category    = request.POST.get('category', 'Stock')

        if item_name and total_stock and stock_qty:
            InventoryRequest.objects.create(
                requested_by=request.user,
                item_name=item_name,
                total_stock=total_stock,
                stock_qty=stock_qty,
                unit=unit,
                category=category,
            )
            messages.success(request, f'Request for "{item_name}" submitted. Awaiting owner approval. Naipadala na, hintayin ang approval ng owner.')
        else:
            messages.error(request, 'Please fill in all required fields. Kumpletuhin ang lahat ng kailangang field.')

    return redirect('view-inventory')


@login_required
def staff_event_request_view(request):
    if request.method == 'POST':
        event_name  = request.POST.get('event_name', '').strip()
        event_date  = request.POST.get('event_date', '').strip()
        description = request.POST.get('description', '').strip()

        if event_name and event_date:
            EventRequest.objects.create(
                requested_by=request.user,
                event_name=event_name,
                event_date=event_date,
                description=description,
            )
            messages.success(request, f'Event request "{event_name}" submitted. Awaiting owner approval. Naipadala na, hintayin ang approval ng owner.')
        else:
            messages.error(request, 'Please fill in event name and date. Kailangan ang event name at date.')

    return redirect('view-events')


@login_required
def events_view(request):
    current_role = getattr(request.user, 'role', '').strip().upper()

    if request.method == 'POST':
        if current_role != 'OWNER':
            messages.error(request, 'Access Denied.')
            return redirect('view-events')

        event_name  = request.POST.get('event_name')
        event_date  = request.POST.get('event_date')
        description = request.POST.get('description', '')
        OwnerEvent.objects.create(
            event_name=event_name,
            event_date=event_date,
            description=description,
        )
        messages.success(request, 'Event created successfully.')
        return redirect('view-events')

    today = timezone.now().date()
    OwnerEvent.objects.filter(event_date__lt=today, is_archived=False).update(is_archived=True)

    upcoming  = OwnerEvent.objects.filter(is_archived=False).order_by('event_date')
    archived  = OwnerEvent.objects.filter(is_archived=True).order_by('-event_date')
    read_only = (current_role != 'OWNER')

    my_event_requests = []
    if current_role == 'STAFF':
        my_event_requests = EventRequest.objects.filter(
            requested_by=request.user
        ).order_by('-created_at')

    return render(request, 'PAGES/events.html', {
        'upcoming':          upcoming,
        'archived':          archived,
        'read_only':         read_only,
        'my_event_requests': my_event_requests,
    })


