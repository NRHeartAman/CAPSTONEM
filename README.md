# CraveCast

A demand-forecasting and inventory management system for a milk tea / cafe
business, built with Django. Two roles — **Owner** and **Staff** — share one
system that connects sales history, recipes, inventory, and demand
forecasting so a predicted spike in sales shows up as a predicted ingredient
shortage before it happens, not after the shelf is already empty.

## Tech stack

- **Backend:** Django 6.0, Python 3.12+
- **Database:** PostgreSQL
- **Forecasting:** scikit-learn (linear regression over temperature,
  day-of-week, and month), with live weather data from OpenWeatherMap /
  Open-Meteo
- **Email:** Gmail SMTP (password reset, staff account activation)

## Project structure

Six Django apps, each owning one concern:

| App | Responsibility |
|---|---|
| `accounts` | Custom `User` model (OWNER/STAFF roles), auth, password reset, the notification engine |
| `inventory` | Stock items, recipes (`ProductRecipe` → `RecipeIngredient`), auto-deduction, demand-forecast service |
| `sales` | `SalesRecord` — the raw sales history everything else reads from |
| `forecast` | The ML engine and its API endpoints |
| `owner` | Dashboard, CSV upload, staff request approvals, employee management, events |
| `staff` | Staff dashboard, prep-task auto-generation, inventory/event request submission |

## Setup

```bash
# 1. Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
source .venv/bin/activate       # macOS/Linux

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a PostgreSQL database
#    (default expected name: cravecast_db)

# 4. Configure config/settings.py
#    Set DATABASES, SECRET_KEY, and EMAIL_HOST_USER/EMAIL_HOST_PASSWORD
#    (a Gmail App Password, not your regular password) for email features.

# 5. Run migrations
python manage.py migrate

# 6. Create an owner account
python manage.py createsuperuser
#    then set that user's `role` to 'OWNER' via the Django admin or shell

# 7. Run the dev server
python manage.py runserver
```

Visit `http://127.0.0.1:8000/login/`.

## Running tests

```bash
python manage.py test
```

39 tests across 5 apps — each one is a regression check tied to a specific
bug found and fixed during development (a hardcoded low-stock threshold
reimplemented six different ways, a text/bytes crash on CSV approval, a URL
ordering bug that broke password reset, double-counted toast icons, and
more).

## Core feature: the Sales → Recipes → Inventory → Forecast loop

1. An Owner uploads a sales CSV (`Date, Product Name, Quantity, Unit Price`).
2. New rows are inserted (deduplicated by product + date); a batched
   weather lookup enriches each date with that day's temperature.
3. For every newly-inserted sale, the matching `ProductRecipe` is found and
   its ingredients are deducted from `Inventory` immediately — no separate
   approval step for the deduction itself.
4. That same sales history trains the forecast model. `get_ingredient_demand_forecast()`
   projects tomorrow's predicted unit sales through each product's recipe
   to flag ingredients that will run low or out *before* it happens.
5. The notification system watches stock levels, expiry dates, predicted
   shortages, and pending approvals, deduplicating so the same condition
   doesn't spam — clicking a notification navigates straight to what it's
   about.

The forecast needs at least 5 days of sales history before it will predict
anything; with less, it explicitly reports "not enough data" rather than
guessing.

## Known limitations

- **Not deployed** — runs locally via `manage.py runserver` only.
- **Staff CSV submission is currently disabled.** The underlying approval
  model (`SalesUploadRequest`) and the Owner-side approval view still exist,
  but the staff-facing submission page was intentionally removed. Direct
  Owner upload (`/owner/upload/`) is the active path.
- Bilingual (English/Filipino) toast messages throughout the UI are a
  deliberate accessibility choice, not yet a full i18n implementation.
