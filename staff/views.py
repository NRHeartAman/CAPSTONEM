
from django.shortcuts import render, redirect
from django.contrib.auth.decorators import login_required
from django.db.models import Sum, F
from django.utils import timezone
from datetime import timedelta
from sales.models import SalesRecord
from inventory.models import Inventory
import json
from .models import Event
from django.contrib import messages

@login_required
def staff_dashboard_view(request):
    today = timezone.now().date()
    seven_days_ago = today - timedelta(days=6)


    daily_sold = SalesRecord.objects.filter(sale_date=today).aggregate(Sum('quantity'))['quantity__sum'] or 0
    

    daily_orders = SalesRecord.objects.filter(sale_date=today).count()
    

    weekly_revenue = SalesRecord.objects.filter(sale_date__gte=seven_days_ago).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # 2. LOW STOCK ALERTS
    # Get items where stock is 20 or below
    low_stock_query = Inventory.objects.filter(stock_qty__lte=20).order_by('stock_qty')
    low_stock_count = low_stock_query.count()
    low_stock_items = low_stock_query[:5] # Show top 5 urgent items

    # 3. CHART LOGIC (7-Day Sales Performance)
    chart_labels = []
    chart_values = []

    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        chart_labels.append(date.strftime('%b %d')) # e.g., "Oct 01"
        
        # Calculate revenue for this specific day
        daily_rev = SalesRecord.objects.filter(sale_date=date).aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or 0
        chart_values.append(float(daily_rev))

    # 4. CONTEXT DICTIONARY (Matches the HTML variables)
    context = {
        'daily_sold': daily_sold,
        'daily_orders': daily_orders,
        'weekly_sales': weekly_revenue,
        'low_stock_count': low_stock_count,
        'low_stock_items': low_stock_items,
        'chart_labels': json.dumps(chart_labels), # Convert to JSON for JS
        'chart_values': json.dumps(chart_values), # Convert to JSON for JS
    }

    return render(request, 'STAFF/staff.html', context)


    


def events_view(request):
    if request.method == "POST":
        name = request.POST.get('event_name')
        date = request.POST.get('event_date')
        desc = request.POST.get('description')

        Event.objects.create(event_name=name, event_date=date, description=desc)
        messages.success(request, "New event added successfully!")
        return redirect('view-events') # Replace with your URL name

    events = Event.objects.all()
    return render(request, 'PAGES/events.html', {'events': events})