from django.shortcuts import render
from django.shortcuts import render, redirect
from django.contrib import messages
from django.contrib.auth import update_session_auth_hash
from .models import SystemSetting
from sales.models import SalesRecord
from django.db.models import Sum, F, Count
from django.utils import timezone
from datetime import timedelta
from inventory.models import Inventory
import json
import io
import csv, requests

def owner_dashboard_view(request):
    today = timezone.now().date()
    seven_days_ago = today - timedelta(days=6)

    # 1. ANALYTICS CARDS
    daily_sold = SalesRecord.objects.filter(sale_date=today).aggregate(Sum('quantity'))['quantity__sum'] or 0
    daily_orders = SalesRecord.objects.filter(sale_date=today).count()
    
    weekly_revenue = SalesRecord.objects.filter(sale_date__gte=seven_days_ago).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    low_stock_count = Inventory.objects.filter(stock_qty__lte=20).count()
    print(low_stock_count)
    # 2. CHART LOGIC (7-Day Sales Performance)
    chart_labels = []
    chart_values = []

    
    for i in range(6, -1, -1):
        date = today - timedelta(days=i)
        # I-format ang label (e.g., "Apr 12")
        chart_labels.append(date.strftime('%b %d')) 
        
       
        daily_rev = SalesRecord.objects.filter(sale_date=date).aggregate(
            total=Sum(F('quantity') * F('price'))
        )['total'] or 0
        
        chart_values.append(float(daily_rev))

    low_stock_items = Inventory.objects.filter(stock_qty__lte=20).order_by('stock_qty')[:5]

    context = {
        'daily_sold': daily_sold,
        'daily_orders': daily_orders,
        'weekly_sales': weekly_revenue,
        'low_stock_count': low_stock_count,
        'low_stock_items': low_stock_items,
       
        'chart_labels': json.dumps(chart_labels),
        'chart_values': json.dumps(chart_values),
    }
    
    return render(request, 'OWNER/owner.html', context)
def sales_analytics_view(request):
    today = timezone.now().date()
    start_of_week = today - timedelta(days=7)
    start_of_month = today.replace(day=1)

    # 1. Units Sold Today
    units_today = SalesRecord.objects.filter(sale_date=today).aggregate(total=Sum('quantity'))['total'] or 0

    # 2. Total Orders Today (Count of transactions)
    orders_today = SalesRecord.objects.filter(sale_date=today).count()

    # 3. Weekly Revenue (Sum of Qty * Price for last 7 days)
    weekly_rev = SalesRecord.objects.filter(sale_date__gte=start_of_week).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # 4. Monthly Revenue
    monthly_rev = SalesRecord.objects.filter(sale_date__gte=start_of_month).aggregate(
        total=Sum(F('quantity') * F('price'))
    )['total'] or 0

    # 5. Top Product (Most units sold ever)
    top_prod_query = SalesRecord.objects.values('product_name').annotate(
        total_qty=Sum('quantity')
    ).order_by('-total_qty').first()
    
    top_product = top_prod_query['product_name'] if top_prod_query else "No Data"

    # 6. Recent Transactions
    recent_sales = SalesRecord.objects.all().order_by('-sale_date')[:15]

    context = {
        'units_sold_today': units_today,
        'total_orders_today': orders_today,
        'weekly_revenue': "{:,.2f}".format(weekly_rev),
        'monthly_revenue': "{:,.2f}".format(monthly_rev),
        'top_product': top_product,
        'sales': recent_sales,
    }

    return render(request, 'OWNER/sales_analytics.html', context)



def get_historical_temp(date_str, lat=14.4667, lon=121.1833):
    """Auto-fetches historical temp from Open-Meteo (free, no API key)."""
    url = (
        f"https://archive-api.open-meteo.com/v1/archive"
        f"?latitude={lat}&longitude={lon}"
        f"&start_date={date_str}&end_date={date_str}"
        f"&daily=temperature_2m_mean&timezone=Asia%2FManila"
    )
    try:
        res = requests.get(url, timeout=5)
        data = res.json()
        return data['daily']['temperature_2m_mean'][0]
    except Exception:
        return None

def upload_data_view(request):
    if request.method == "POST":
        csv_file = request.FILES.get('csv_file')

        if not csv_file:
            messages.error(request, "Please select a file first.")
        elif not csv_file.name.endswith('.csv'):
            messages.error(request, "This is not a CSV file.")
        else:
            try:
                data_set = csv_file.read().decode('UTF-8')
                io_string = io.StringIO(data_set)
                next(io_string)  # Skip header

                saved, skipped = 0, 0

                for row in csv.reader(io_string, delimiter=','):
                    date_str    = row[0].strip()
                    product     = row[1].strip()
                    quantity    = row[2].strip()
                    price       = row[3].strip()

             
                    temp = get_historical_temp(date_str)

                    if temp is None:
                        skipped += 1
                        continue  

                    SalesRecord.objects.update_or_create(
                        sale_date=date_str,
                        product_name=product,
                        defaults={
                            'quantity': quantity,
                            'price': price,
                            'temp_c': temp, 
                        }
                    )
                    saved += 1

                if skipped > 0:
                    messages.warning(request, f"Saved {saved} rows. Skipped {skipped} rows (temp fetch failed).")
                else:
                    messages.success(request, f"Successfully uploaded {saved} records with temperature data!")

            except Exception as e:
                messages.error(request, f"Error processing file: {e}")

        return redirect('view-upload-data')

    recent_sales = SalesRecord.objects.all().order_by('-uploaded_at')[:10]
    return render(request, 'OWNER/upload_data.html', {'sales': recent_sales})


def settings_view(request):
    config, created = SystemSetting.objects.get_or_create(id=1)

    if request.method == "POST":

        if 'update_config' in request.POST:
            config.store_name = request.POST.get('store_name')
            config.contact_number = request.POST.get('contact_number')
            config.stock_threshold = request.POST.get('stock_threshold')
            config.weather_api_key = request.POST.get('weather_api_key')
            config.forecast_mode = request.POST.get('forecast_mode')
            config.store_lat = request.POST.get('store_lat') 
            config.store_lon = request.POST.get('store_lon') 
            config.save()
            messages.success(request, "Configuration Updated Successfully")

        elif 'update_password' in request.POST:
            current_pass = request.POST.get('current_password')
            new_pass = request.POST.get('new_password')
            confirm_pass = request.POST.get('confirm_password')

            if request.user.check_password(current_pass):
                if new_pass == confirm_pass:
                    request.user.set_password(new_pass)
                    request.user.save()
                    update_session_auth_hash(request, request.user) # Keep user logged in
                    messages.success(request, "Password Changed Successfully")
                else:
                    messages.error(request, "New passwords do not match")
            else:
                messages.error(request, "Incorrect current password")

        return redirect('settings') # Replace with your actual URL name

    return render(request, 'OWNER/settings.html', {'config': config})