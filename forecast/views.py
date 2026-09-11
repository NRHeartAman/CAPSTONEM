from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
import datetime

from .ml_engine import train_and_predict, predict_per_product, get_historical_avg_temp


@login_required
def forecast_view(request):
    current_temp = 31
    current_day = datetime.datetime.now().weekday()

    predicted_cups, _accuracy, _rows = train_and_predict(current_temp, current_day)

    if predicted_cups is None:
        predicted_cups = "Need more data"

    return render(request, 'PAGES/forecast.html', {
        'predicted_cups': predicted_cups
    })


@login_required
def get_prediction_api(request):
    temp     = float(request.GET.get('temp', 30))
    humidity = float(request.GET.get('humidity', 60))
    day      = datetime.datetime.now().weekday()

    products = predict_per_product(temp, day)
    total = sum(p['qty'] for p in products)

    _, accuracy, rows = train_and_predict(temp, day)

    reason = "Normal demand expected."
    if temp < 20:
        reason = "Cold weather detected — hot drink demand is elevated."
    elif temp > 30:
        reason = "High heat detected — iced drink demand is elevated."
    if humidity > 80:
        reason += " High humidity may reduce foot traffic."

    return JsonResponse({
        'prediction': total if total > 0 else "Need Data",
        'reason': reason,
        'accuracy': accuracy,
        'row_count': rows,
        'products': products
    })


@login_required
def get_monthly_forecast_api(request):
    """
    Projects demand for the next 30 days starting today.

    Query params:
      dates  - comma-separated ISO dates (YYYY-MM-DD) that have real
               weather-API forecast data (normally the next ~5 days)
      temps  - comma-separated temperatures (°C) matching `dates`, in order

    Days NOT covered by dates/temps fall back to that weekday's
    historical average temperature, pulled from actual SalesRecord
    history — so the whole projection stays grounded in real data
    instead of a guess.
    """
    dates_param = request.GET.get('dates', '')
    temps_param = request.GET.get('temps', '')

    forecast_lookup = {}
    if dates_param and temps_param:
        for d, t in zip(dates_param.split(','), temps_param.split(',')):
            d = d.strip()
            try:
                forecast_lookup[d] = float(t)
            except ValueError:
                continue

    today = datetime.date.today()
    daily_results = []
    accuracy_val = None
    rows_val = 0

    for i in range(30):
        day = today + datetime.timedelta(days=i)
        date_str = day.isoformat()
        dow = day.weekday()
        month = day.month

        if date_str in forecast_lookup:
            temp = forecast_lookup[date_str]
            source = 'forecast'
        else:
            historical_temp = get_historical_avg_temp(dow)
            temp = historical_temp if historical_temp is not None else 28.0
            source = 'historical'

        pred, accuracy, rows = train_and_predict(temp, dow, month)

        if rows:
            accuracy_val, rows_val = accuracy, rows

        daily_results.append({
            'date': date_str,
            'day_label': day.strftime('%a'),
            'temp': round(temp, 1),
            'predicted_qty': pred if pred is not None else 0,
            'source': source,
        })

    monthly_total = sum(d['predicted_qty'] for d in daily_results)

    weekly = []
    for w in range(4):
        start = w * 7
        end = start + 7 if w < 3 else 30
        bucket = daily_results[start:end]
        if not bucket:
            continue
        sources = {d['source'] for d in bucket}
        weekly.append({
            'label': f'Week {w + 1}',
            'total': sum(d['predicted_qty'] for d in bucket),
            'range_start': bucket[0]['date'],
            'range_end': bucket[-1]['date'],
            'source': 'forecast' if 'forecast' in sources else 'historical',
        })

    peak_week = max(weekly, key=lambda w: w['total']) if weekly else None
    has_enough_data = bool(rows_val and rows_val >= 5)

    return JsonResponse({
        'daily': daily_results,
        'weekly': weekly,
        'monthly_total': monthly_total,
        'avg_per_day': round(monthly_total / 30) if monthly_total else 0,
        'peak_week': peak_week,
        'accuracy': accuracy_val,
        'row_count': rows_val,
        'has_enough_data': has_enough_data,
    })