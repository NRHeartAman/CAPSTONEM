import pandas as pd
from sklearn.linear_model import LinearRegression
from sales.models import SalesRecord


def _get_daily_dataframe():
    """Returns SalesRecord history aggregated to one row per day, with
    day_of_week / month already extracted. None if there's no data yet."""
    data = SalesRecord.objects.all().values('quantity', 'temp_c', 'sale_date')
    if not data:
        return None

    df = pd.DataFrame(data)
    df['day_of_week'] = pd.to_datetime(df['sale_date']).dt.dayofweek
    df['month'] = pd.to_datetime(df['sale_date']).dt.month

    daily = df.groupby('sale_date').agg(
        total_qty=('quantity', 'sum'),
        temp_c=('temp_c', 'first'),
        day_of_week=('day_of_week', 'first'),
        month=('month', 'first')
    ).reset_index()

    return daily


def train_and_predict(current_temp, day_of_week, month=None):
    """
    Predicts total units for a single day given temperature, day-of-week,
    and month. `month` defaults to the current month — pass it explicitly
    when forecasting a future date (e.g. for the monthly overview).
    """
    if month is None:
        month = pd.Timestamp.now().month

    daily = _get_daily_dataframe()
    if daily is None or len(daily) < 5:
        return None, None, 0

    X = daily[['temp_c', 'day_of_week', 'month']]
    y = daily['total_qty']

    model = LinearRegression()
    model.fit(X, y)

    accuracy = max(0.0, model.score(X, y))

    input_data = pd.DataFrame(
        [[current_temp, day_of_week, month]],
        columns=['temp_c', 'day_of_week', 'month']
    )
    prediction = model.predict(input_data)

    return max(0, round(prediction[0])), round(accuracy, 2), len(daily)


def predict_per_product(current_temp, day_of_week, month=None):
    if month is None:
        month = pd.Timestamp.now().month

    data = SalesRecord.objects.all().values('quantity', 'temp_c', 'sale_date', 'product_name')
    if not data or len(data) < 5:
        return []

    df = pd.DataFrame(data)
    df['day_of_week'] = pd.to_datetime(df['sale_date']).dt.dayofweek
    df['month'] = pd.to_datetime(df['sale_date']).dt.month

    products = df['product_name'].unique()
    results = []

    for product in products:
        product_df = df[df['product_name'] == product]
        if len(product_df) < 3:
            continue

        X = product_df[['temp_c', 'day_of_week', 'month']]
        y = product_df['quantity']

        model = LinearRegression()
        model.fit(X, y)

        input_data = pd.DataFrame(
            [[current_temp, day_of_week, month]],
            columns=['temp_c', 'day_of_week', 'month']
        )
        predicted_qty = max(0, round(model.predict(input_data)[0]))

        avg_qty = y.mean()
        max_qty = y.max() * 1.3  # Allow max 30% above historical peak
        predicted_qty = min(predicted_qty, int(max_qty))
        predicted_qty = max(0, predicted_qty)
        trend = "up" if predicted_qty >= avg_qty else "down"

        results.append({
            'name': product,
            'qty': predicted_qty,
            'trend': trend
        })

    results.sort(key=lambda x: x['qty'], reverse=True)
    return results


def get_historical_avg_temp(day_of_week=None):
    """
    Average recorded temperature from actual sales history. If
    day_of_week (0=Mon..6=Sun) is given and there are at least 2 records
    for that weekday, returns that weekday's average. Otherwise falls
    back to the overall average. Returns None if there's no temperature
    history at all yet.
    """
    data = SalesRecord.objects.exclude(temp_c__isnull=True).values('sale_date', 'temp_c')
    if not data:
        return None

    df = pd.DataFrame(data)
    df['day_of_week'] = pd.to_datetime(df['sale_date']).dt.dayofweek

    if day_of_week is not None:
        subset = df[df['day_of_week'] == day_of_week]
        if len(subset) >= 2:
            return round(float(subset['temp_c'].mean()), 1)

    return round(float(df['temp_c'].mean()), 1)
