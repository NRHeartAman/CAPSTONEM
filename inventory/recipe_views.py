# inventory/recipe_views.py

from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from .models import ProductRecipe, RecipeIngredient, Inventory
from .services import get_inventory_forecast, get_ingredient_demand_forecast


# ── Recipe List ───────────────────────────────────────────────────────────────

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

    return render(request, 'PAGES/recipes.html', {
        'recipe_data':     recipe_data,
        'demand_forecast': demand_forecast,
    })


# ── Add Recipe ────────────────────────────────────────────────────────────────

def recipe_add(request):
    ingredients = Inventory.objects.filter(category='Stock').order_by('item_name')

    if request.method == 'POST':
        product_name = request.POST.get('product_name', '').strip()
        notes        = request.POST.get('notes', '').strip()

        if not product_name:
            messages.error(request, "Product name is required.")
            return render(request, 'PAGES/recipe_form.html', {'ingredients': ingredients, 'action': 'Add'})

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

        errors = []
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
    })

# ── Edit Recipe ───────────────────────────────────────────────────────────────

def recipe_edit(request, pk):
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

        for item_id, qty in zip(item_ids, qtys):
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

def recipe_delete(request, pk):
    recipe = get_object_or_404(ProductRecipe, pk=pk)
    if request.method == 'POST':
        name = recipe.product_name
        recipe.delete()
        messages.success(request, f"Recipe for '{name}' deleted.")
    return redirect('recipe_list')


