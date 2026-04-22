from django.shortcuts import render, redirect
from django.contrib import messages
from django.utils import timezone
from datetime import timedelta
from django.db.models import Sum, F

# Import your models
from .models import Inventory
from sales.models import SalesRecord  # Adjust if your sales app name is different

def inventory_view(request):
    # 1. Setup Dates
    today = timezone.now().date()
    start_of_week = today - timedelta(days=7)
    start_of_month = today.replace(day=1)

    # 2. Handle Add New Entry (POST)
    if request.method == "POST":
        item_name = request.POST.get('item_name')
        total_stock = request.POST.get('total_stock')
        stock_qty = request.POST.get('stock_qty')
        unit = request.POST.get('unit')
        category = request.POST.get('category')

        Inventory.objects.create(
            item_name=item_name,
            total_stock=total_stock,
            stock_qty=stock_qty,
            unit=unit,
            category=category
        )
        messages.success(request, f"{item_name} added to {category} successfully!")
        return redirect(f'/inventory/?tab={category}')

    # 3. Calculate Real Sales Revenue for the Cards
    # Daily Sales
    daily_rev = SalesRecord.objects.filter(sale_date=today).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # Weekly Sales
    weekly_rev = SalesRecord.objects.filter(sale_date__gte=start_of_week).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # Monthly Sales
    monthly_rev = SalesRecord.objects.filter(sale_date__gte=start_of_month).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # 4. Handle Tabs and Inventory List
    current_tab = request.GET.get('tab', 'Stock')
    inventory_items = Inventory.objects.filter(category=current_tab).order_by('item_name')

    # 5. Prepare Context
    context = {
        'inventory': inventory_items,
        'current_tab': current_tab,
        'daily_sales': "{:,.2f}".format(daily_rev),
        'weekly_sales': "{:,.2f}".format(weekly_rev),
        'monthly_sales': "{:,.2f}".format(monthly_rev),
    }

    return render(request, 'PAGES/inventory.html', context)