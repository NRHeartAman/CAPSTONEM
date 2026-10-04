# inventory/recipe_views.py

import json
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from .models import ProductRecipe, RecipeIngredient, Inventory
from .services import get_inventory_forecast, get_ingredient_demand_forecast


def _menu_product_list():
    """The client's actual menu — every product name that's ever been sold
    (from uploaded sales data), plus any product that already has a recipe
    but hasn't been sold yet, so nothing with a recipe goes missing from
    the picker."""
    from sales.models import SalesRecord
    sold = set(SalesRecord.objects.values_list('product_name', flat=True))
    recipes = set(ProductRecipe.objects.values_list('product_name', flat=True))
    return sorted(sold | recipes, key=str.lower)


def _existing_recipes_by_product():
    """product_name -> {notes, ingredients:[{id, qty}]} for every recipe
    that already exists, so the Add Recipe form can auto-populate instantly
    when an existing product is picked, without a second request."""
    data = {}
    for r in ProductRecipe.objects.prefetch_related('ingredients__inventory_item'):
        data[r.product_name] = {
            'notes': r.notes,
            'ingredients': [
                {'id': line.inventory_item_id, 'qty': line.qty_per_serving}
                for line in r.ingredients.all()
            ],
        }
    return data


# ── Recipe List ───────────────────────────────────────────────────────────────

@login_required
def recipe_list(request):
    recipes   = ProductRecipe.objects.prefetch_related('ingredients__inventory_item').all()
    forecasts = {f['product']: f for f in get_inventory_forecast()}

    recipe_data = []
    for r in recipes:
        f = forecasts.get(r.product_name, {})
        recipe_data.append({
            'recipe':        r,
            'can_make':      f.get('can_make', '—'),
            'limiting_item': f.get('limiting_item', '—'),
        })

    demand_forecast = get_ingredient_demand_forecast(days_ahead=1)
    read_only = getattr(request.user, 'role', 'STAFF') != 'OWNER'

    return render(request, 'PAGES/recipes.html', {
        'recipe_data':     recipe_data,
        'demand_forecast': demand_forecast,
        'read_only':       read_only,
    })


# ── Add Recipe ────────────────────────────────────────────────────────────────

@login_required
def recipe_add(request):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        messages.error(request, 'Access Denied.')
        return redirect('recipe_list')

    ingredients = Inventory.objects.filter(category='Stock').order_by('item_name')

    if request.method == 'POST':
        product_name = request.POST.get('product_name', '').strip()
        notes        = request.POST.get('notes', '').strip()

        if not product_name:
            messages.error(request, "Product name is required.")
            return render(request, 'PAGES/recipe_form.html', {
                'ingredients': ingredients,
                'action': 'Add',
                'menu_products': _menu_product_list(),
                'existing_recipes_json': json.dumps(_existing_recipes_by_product()),
            })

        recipe, created = ProductRecipe.objects.get_or_create(
            product_name=product_name,
            defaults={'notes': notes}
        )
        if not created:
            recipe.notes = notes
            recipe.save()

        RecipeIngredient.objects.filter(recipe=recipe).delete()

        item_ids = request.POST.getlist('ingredient_id[]')
        qtys     = request.POST.getlist('qty_per_serving[]')

        # ✅ Deduplicate: if the same ingredient appears twice, last one wins
        seen = {}
        for item_id, qty in zip(item_ids, qtys):
            if item_id:
                seen[item_id] = qty  # later rows overwrite earlier ones

        for item_id, qty in seen.items():
            try:
                inv_item = Inventory.objects.get(pk=item_id)
                qty_f    = float(qty)
                if qty_f > 0:
                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        inventory_item=inv_item,
                        qty_per_serving=qty_f,
                    )
            except (Inventory.DoesNotExist, ValueError):
                continue

        messages.success(request, f"Recipe for '{product_name}' saved!")
        return redirect('recipe_list')

    return render(request, 'PAGES/recipe_form.html', {
        'ingredients': ingredients,
        'action': 'Add',
        'menu_products': _menu_product_list(),
        'existing_recipes_json': json.dumps(_existing_recipes_by_product()),
    })

# ── Edit Recipe ───────────────────────────────────────────────────────────────

@login_required
def recipe_edit(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        messages.error(request, 'Access Denied.')
        return redirect('recipe_list')

    recipe      = get_object_or_404(ProductRecipe, pk=pk)
    ingredients = Inventory.objects.filter(category='Stock').order_by('item_name')
    existing    = {ri.inventory_item_id: ri.qty_per_serving
                   for ri in recipe.ingredients.all()}

    if request.method == 'POST':
        recipe.product_name = request.POST.get('product_name', recipe.product_name).strip()
        recipe.notes        = request.POST.get('notes', '').strip()
        recipe.save()

        RecipeIngredient.objects.filter(recipe=recipe).delete()

        item_ids = request.POST.getlist('ingredient_id[]')
        qtys     = request.POST.getlist('qty_per_serving[]')

        # Same dedup as recipe_add: if the same ingredient is submitted
        # twice, the last row wins — otherwise the second create() hits
        # RecipeIngredient's (recipe, inventory_item) unique constraint
        # and crashes the request instead of just saving one line.
        seen = {}
        for item_id, qty in zip(item_ids, qtys):
            if item_id:
                seen[item_id] = qty

        for item_id, qty in seen.items():
            try:
                inv_item = Inventory.objects.get(pk=item_id)
                qty_f    = float(qty)
                if qty_f > 0:
                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        inventory_item=inv_item,
                        qty_per_serving=qty_f,
                    )
            except (Inventory.DoesNotExist, ValueError):
                continue

        messages.success(request, f"Recipe for '{recipe.product_name}' updated!")
        return redirect('recipe_list')

    return render(request, 'PAGES/recipe_form.html', {
        'recipe':      recipe,
        'ingredients': ingredients,
        'existing':    existing,
        'action':      'Edit',
    })


# ── Delete Recipe ─────────────────────────────────────────────────────────────

@login_required
def recipe_delete(request, pk):
    if getattr(request.user, 'role', 'STAFF') != 'OWNER':
        messages.error(request, 'Access Denied.')
        return redirect('recipe_list')

    recipe = get_object_or_404(ProductRecipe, pk=pk)
    if request.method == 'POST':
        name = recipe.product_name
        recipe.delete()
        messages.success(request, f"Recipe for '{name}' deleted.")
    return redirect('recipe_list')


