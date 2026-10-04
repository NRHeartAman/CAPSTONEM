from django.shortcuts import render
from django.http import JsonResponse
from django.contrib.auth.decorators import login_required
from django.conf import settings
import datetime
import requests

from .ml_engine import train_and_predict, predict_per_product, get_historical_avg_temp
from accounts.decorators import owner_required


def _get_weather_api_key():
    """The Owner's own key (Settings) wins when set; otherwise the
    server-configured default. Never sent to the browser - only used
    here, server-side, by the proxy views below."""
    try:
        from owner.models import SystemSetting
        config = SystemSetting.objects.first()
        key = (config.weather_api_key or '').strip() if config else ''
        if key and key.lower() != 'none':
            return key
    except Exception:
        pass
    return settings.OPENWEATHER_API_KEY


def _get_store_location():
    """(city label, lat, lon) from the Owner's configured store, falling
    back to sane defaults if Settings has never been saved. The weather
    lookup goes by lat/lon — Store Name is the shop's brand name (shown on
    the login page), not a city, so it's deliberately not used here."""
    try:
        from owner.models import SystemSetting
        config = SystemSetting.objects.first()
        if config and config.store_lat is not None and config.store_lon is not None:
            return 'Binangonan', config.store_lat, config.store_lon
    except Exception:
        pass
    return 'Binangonan', 14.4667, 121.1833


@login_required
def forecast_view(request):
    current_temp = 31
    current_day = datetime.datetime.now().weekday()

    predicted_cups, _accuracy, _rows = train_and_predict(current_temp, current_day)

    if predicted_cups is None:
        predicted_cups = "Need more data"

    default_city, default_lat, default_lon = _get_store_location()

    return render(request, 'PAGES/forecast.html', {
        'predicted_cups': predicted_cups,
        'default_city':   default_city,
        'default_lat':    default_lat,
        'default_lon':    default_lon,
    })


@login_required
def predicted_demand_view(request):
    """Forecast → Predicted Demand: per-product predicted demand vs. current
    sellable stock, with an actionable recommended-order quantity. Open to
    both Owner and Staff — operational info, like the existing Recipes
    'Outlook' tab this reuses services from."""
    from inventory.services import get_predicted_demand_report, get_ingredient_demand_forecast

    report   = get_predicted_demand_report(days_ahead=1)
    # Ingredient-level shopping list: products share ingredients (e.g. Milk),
    # so "what to buy" has to be totalled per ingredient, not per product.
    shopping = get_ingredient_demand_forecast(days_ahead=1)
    to_buy   = [i for i in shopping['items'] if i['to_buy'] > 0]
    return render(request, 'PAGES/forecast_predicted.html', {
        'report':      report,
        'to_buy':      to_buy,
        'show_costs':  getattr(request.user, 'role', '') == 'OWNER',
    })


@login_required
def weather_forecast_proxy(request):
    """
    Proxies OpenWeatherMap's 5-day/3-hour forecast so the real API key
    never reaches the browser. Accepts ?city= or ?lat=&lon=.
    """
    api_key = _get_weather_api_key()
    if not api_key:
        return JsonResponse({'cod': '401', 'message': 'No weather API key configured.'}, status=200)

    city = request.GET.get('city', '').strip()
    lat  = request.GET.get('lat')
    lon  = request.GET.get('lon')

    params = {'units': 'metric', 'appid': api_key}
    if lat and lon:
        params['lat'] = lat
        params['lon'] = lon
    elif city:
        params['q'] = city
    else:
        _, params['lat'], params['lon'] = _get_store_location()

    try:
        r = requests.get('https://api.openweathermap.org/data/2.5/forecast', params=params, timeout=8)
        return JsonResponse(r.json(), status=r.status_code, safe=False)
    except requests.RequestException:
        return JsonResponse({'cod': '500', 'message': 'Weather service unreachable.'}, status=200)


@login_required
def weather_geo_reverse_proxy(request):
    """Proxies OpenWeatherMap's reverse-geocoding lookup (device GPS -> city name)."""
    api_key = _get_weather_api_key()
    if not api_key:
        return JsonResponse([], safe=False)

    lat = request.GET.get('lat')
    lon = request.GET.get('lon')
    if not lat or not lon:
        return JsonResponse([], safe=False)

    try:
        r = requests.get(
            'https://api.openweathermap.org/geo/1.0/reverse',
            params={'lat': lat, 'lon': lon, 'limit': 1, 'appid': api_key},
            timeout=8,
        )
        return JsonResponse(r.json(), safe=False, status=r.status_code)
    except requests.RequestException:
        return JsonResponse([], safe=False)


def _log_predictions_once(products, target_date):
    """Locks in today's predicted qty per product the first time it's seen
    today — later calls this same day don't overwrite it (get_or_create),
    so Forecast Results always compares actuals against the prediction as
    it stood before the day played out, never a number computed after."""
    from .models import ForecastLog
    for p in products:
        ForecastLog.objects.get_or_create(
            product_name=p['name'], target_date=target_date,
            defaults={'predicted_qty': p['qty']},
        )


@login_required
def get_prediction_api(request):
    # Falls back to sensible defaults on a malformed/non-numeric value
    # instead of crashing — matches the "still show a forecast" fallback
    # philosophy already used elsewhere on this page (runForecastWithoutWeather).
    try:
        temp = float(request.GET.get('temp', 30))
    except (TypeError, ValueError):
        temp = 30.0
    try:
        humidity = float(request.GET.get('humidity', 60))
    except (TypeError, ValueError):
        humidity = 60.0
    today    = datetime.date.today()
    day      = today.weekday()

    products = predict_per_product(temp, day)
    total = sum(p['qty'] for p in products)

    if products:
        _log_predictions_once(products, today)

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


@owner_required
def forecast_results_view(request):
    """Forecast Results / Accuracy — Owner-only, matching the Owner-only
    'Sales analytics' / 'Forecast results' bullets in the revision brief.
    Compares each locked-in ForecastLog prediction to the actual sales
    that came in for that date, once that date's sales data exists.
    Never fabricates a number: a target date with zero uploaded sales
    records (of ANY product) is treated as 'not yet comparable', not as
    a real zero-actual result."""
    from .models import ForecastLog
    from sales.models import SalesRecord
    from django.db.models import Sum

    logs = ForecastLog.objects.all().order_by('-target_date')

    dates_with_sales = set(
        SalesRecord.objects.values_list('sale_date', flat=True).distinct()
    )

    comparisons  = []
    pending_count = 0

    for log in logs:
        if log.target_date not in dates_with_sales:
            pending_count += 1
            continue

        actual = SalesRecord.objects.filter(
            product_name=log.product_name, sale_date=log.target_date
        ).aggregate(total=Sum('quantity'))['total'] or 0

        comparisons.append({
            'product':      log.product_name,
            'target_date':  log.target_date,
            'predicted':    log.predicted_qty,
            'actual':       actual,
            'difference':   actual - log.predicted_qty,
        })

    total_predicted = sum(c['predicted'] for c in comparisons)
    total_actual    = sum(c['actual'] for c in comparisons)

    return render(request, 'PAGES/forecast_results.html', {
        'comparisons':     comparisons,
        'pending_count':   pending_count,
        'total_predicted': total_predicted,
        'total_actual':    total_actual,
        'total_difference': total_actual - total_predicted,
        'has_enough_data': len(comparisons) > 0,
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