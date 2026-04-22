import pandas as pd
import numpy as np
from sklearn.linear_model import LinearRegression
from sales.models import SalesRecord

def train_and_predict(current_temp, current_day):
    data = SalesRecord.objects.all().values('quantity', 'temp_c', 'sale_date')
    if not data or len(data) < 5:
        return None, None, 0

    df = pd.DataFrame(data)
    df['day_of_week'] = pd.to_datetime(df['sale_date']).dt.dayofweek
    df['month'] = pd.to_datetime(df['sale_date']).dt.month

    # Group by date — sum all products into ONE total per day
    daily = df.groupby('sale_date').agg(
        total_qty=('quantity', 'sum'),
        temp_c=('temp_c', 'first'),
        day_of_week=('day_of_week', 'first'),
        month=('month', 'first')
    ).reset_index()

    X = daily[['temp_c', 'day_of_week', 'month']]
    y = daily['total_qty']

    model = LinearRegression()
    model.fit(X, y)

    accuracy = max(0.0, model.score(X, y))

    input_data = pd.DataFrame(
        [[current_temp, current_day, pd.Timestamp.now().month]],
        columns=['temp_c', 'day_of_week', 'month']
    )
    prediction = model.predict(input_data)

    return round(prediction[0]), round(accuracy, 2), len(daily)

def predict_per_product(current_temp, current_day):
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

        # Fix: use DataFrame here too
        input_data = pd.DataFrame([[current_temp, current_day, pd.Timestamp.now().month]],
                                   columns=['temp_c', 'day_of_week', 'month'])
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