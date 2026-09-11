from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import render, redirect
from django.contrib import messages
from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.core.mail import send_mail
from django.utils import timezone
from .models import User
from django.http import JsonResponse
from .models import Notification

import secrets
from datetime import timedelta


# ── TOP-LEVEL: must be defined BEFORE notifications_json ──

# Matches the expiry window used on the inventory page (inventory/views.py).
EXPIRY_WARNING_DAYS = 7


def _raise_alert(user, notif_type, key, title, message, link=''):
    """
    Create an alert unless the same one is already sitting unread in the bell.

    `key` identifies the condition (e.g. "low_stock:14"). Once the owner marks
    it read it can be raised again the next time the condition is detected, so
    a re-order that dips below the threshold again still notifies.

    `link` is where clicking the notification should take the user — set
    here, at creation time, since this is where the code actually knows
    which item/request the alert is about.
    """
    exists = Notification.objects.filter(
        user=user, notif_type=notif_type, key=key, is_read=False
    ).exists()
    if exists:
        return
    Notification.objects.create(
        user=user, notif_type=notif_type, key=key, title=title, message=message, link=link
    )


def generate_system_notifications(user):
    try:
        from inventory.models import Inventory
        from owner.models import InventoryRequest, EventRequest, SalesUploadRequest
    except ImportError as e:
        print(f"[Notifications] Import error: {e}")
        return

    today = timezone.localdate()

    # ── Stock level alerts: out of stock + restock needed ──
    try:
        for item in Inventory.objects.all():
            threshold = item.restock_threshold or 0

            item_link = f'/inventory/?tab={item.category}#inv-item-{item.pk}'

            if item.stock_qty <= 0:
                _raise_alert(
                    user, 'out_of_stock', f'out_of_stock:{item.pk}',
                    f'Out of Stock: {item.item_name}',
                    f'{item.item_name} is fully depleted. Restock before it blocks production.',
                    link=item_link,
                )
            elif item.stock_qty <= threshold:
                _raise_alert(
                    user, 'low_stock', f'low_stock:{item.pk}',
                    f'Restock Needed: {item.item_name}',
                    f'Only {item.stock_qty:g} {item.unit} left '
                    f'(restock threshold is {threshold:g} {item.unit}).',
                    link=item_link,
                )
    except Exception as e:
        print(f"[Notifications] Stock level error: {e}")

    # ── Expiry alerts: already expired + expiring within the warning window ──
    try:
        dated = Inventory.objects.filter(category='Stock', expiry_date__isnull=False)
        for item in dated:
            days_left = (item.expiry_date - today).days

            expiry_link = f'/inventory/?tab=Stock#inv-item-{item.pk}'

            if days_left < 0:
                _raise_alert(
                    user, 'expired', f'expired:{item.pk}:{item.expiry_date}',
                    f'Expired: {item.item_name}',
                    f'{item.item_name} expired on {item.expiry_date:%b %d, %Y} '
                    f'({abs(days_left)} day{"s" if abs(days_left) != 1 else ""} ago). '
                    f'Remove it from stock and log the waste.',
                    link=expiry_link,
                )
            elif days_left <= EXPIRY_WARNING_DAYS:
                when = 'today' if days_left == 0 else (
                    'tomorrow' if days_left == 1 else f'in {days_left} days'
                )
                _raise_alert(
                    user, 'expiring', f'expiring:{item.pk}:{item.expiry_date}',
                    f'Expiring Soon: {item.item_name}',
                    f'{item.item_name} expires {when} '
                    f'({item.expiry_date:%b %d, %Y}). Use or rotate it first.',
                    link=expiry_link,
                )
    except Exception as e:
        print(f"[Notifications] Expiry error: {e}")

    # ── Owner-only alerts ──
    if getattr(user, 'role', '') == 'OWNER':
        # Pending approvals
        try:
            pending_inv = InventoryRequest.objects.filter(status='pending').count()
            pending_evt = EventRequest.objects.filter(status='pending').count()
            pending_csv = SalesUploadRequest.objects.filter(status='pending').count()
            total       = pending_inv + pending_evt + pending_csv

            if total > 0:
                _raise_alert(
                    user, 'approval', f'approval:{pending_inv}:{pending_evt}:{pending_csv}',
                    f'{total} Pending Approval{"s" if total > 1 else ""}',
                    f'{pending_inv} inventory, '
                    f'{pending_evt} event, '
                    f'{pending_csv} CSV upload '
                    f'request{"s" if total > 1 else ""} awaiting your review.',
                    link='/owner/approvals/',
                )
        except Exception as e:
            print(f"[Notifications] Approval error: {e}")

        # Predicted ingredient shortages (Forecast × Recipes × Inventory)
        try:
            from inventory.services import get_ingredient_demand_forecast

            forecast = get_ingredient_demand_forecast(days_ahead=1)
            if forecast['has_enough_data']:
                for item in forecast['items']:
                    if item['status'] not in ('out', 'low'):
                        continue
                    verb = 'run out of' if item['status'] == 'out' else 'be low on'
                    _raise_alert(
                        user, 'predicted_shortage',
                        f"predicted_shortage:{item['item']}:{forecast['target_date']}",
                        f"Predicted Shortage: {item['item']}",
                        f"Forecasted demand for {forecast['day_label']} would {verb} "
                        f"{item['item']} — about {item['remaining_after']:g} {item['unit']} "
                        f"would be left after ~{item['predicted_use']:g} {item['unit']} predicted use.",
                        link='/inventory/recipes/?tab=outlook',
                    )
        except Exception as e:
            print(f"[Notifications] Predicted shortage error: {e}")

        # Contracts expiring / expired
        try:
            from .models import EmployeeProfile

            profiles = EmployeeProfile.objects.select_related('user').exclude(
                contract_end__isnull=True
            )
            for profile in profiles:
                status = profile.contract_status
                if status not in ('expired', 'expiring_soon'):
                    continue

                name = profile.user.full_name or profile.user.username
                days = profile.days_until_expiry
                if status == 'expired':
                    msg = (f"{name}'s contract ended on "
                           f"{profile.contract_end:%b %d, %Y}.")
                else:
                    msg = (f"{name}'s contract ends in {days} "
                           f"day{'s' if days != 1 else ''} "
                           f"({profile.contract_end:%b %d, %Y}).")

                _raise_alert(
                    user, 'contract',
                    f'contract:{profile.pk}:{profile.contract_end}',
                    f'Contract {"Expired" if status == "expired" else "Expiring"}: {name}',
                    msg,
                    link=f'/owner/employees/{profile.user.pk}/',
                )
        except Exception as e:
            print(f"[Notifications] Contract error: {e}")


def notifications_json(request):
    if not request.user.is_authenticated:
        return JsonResponse({'notifications': [], 'unread': 0})

    generate_system_notifications(request.user)

    notifs_qs = Notification.objects.filter(user=request.user)
    unread    = notifs_qs.filter(is_read=False).count()
    notifs    = notifs_qs[:30]

    data = [{
        'id':         n.pk,
        'type':       n.notif_type,
        'title':      n.title,
        'message':    n.message,
        'link':       n.link,
        'is_read':    n.is_read,
        'created_at': n.created_at.isoformat(),
        'time':       timezone.localtime(n.created_at).strftime('%b %d, %I:%M %p'),
    } for n in notifs]

    return JsonResponse({'notifications': data, 'unread': unread})


def notifications_unread_count(request):
    """Lightweight endpoint for the nav badge poll — no alert generation."""
    if not request.user.is_authenticated:
        return JsonResponse({'unread': 0})
    unread = Notification.objects.filter(user=request.user, is_read=False).count()
    return JsonResponse({'unread': unread})


def mark_notification_read(request, pk):
    if request.method != 'POST' or not request.user.is_authenticated:
        return JsonResponse({'status': 'error'}, status=400)
    updated = Notification.objects.filter(pk=pk, user=request.user, is_read=False) \
                                  .update(is_read=True)
    return JsonResponse({'status': 'ok', 'updated': updated})


def mark_notifications_read(request):
    if request.method != 'POST' or not request.user.is_authenticated:
        return JsonResponse({'status': 'error'}, status=400)
    updated = Notification.objects.filter(user=request.user, is_read=False) \
                                  .update(is_read=True)
    return JsonResponse({'status': 'ok', 'updated': updated})


def login_view(request):
    if request.method == 'POST':
        u = request.POST.get('username')
        p = request.POST.get('password')
        selected_role = request.POST.get('role', '').strip().upper()
        user = authenticate(request, username=u, password=p)
        if user is not None:
            if not user.is_active:
                messages.error(request, "Your account is not yet activated. Please check your email for the activation link.", extra_tags='login_fail')
                return redirect('login')
            db_role = getattr(user, 'role', '').strip().upper()
            if selected_role != db_role:
                messages.error(request, f"Access Denied: Your account is registered as {db_role}, but you attempted to use {selected_role} Access.", extra_tags='login_fail')
                return redirect('login')
            login(request, user)
            if db_role == 'OWNER':
                return redirect('owner-dashboard')
            elif db_role == 'STAFF':
                return redirect('staff-dashboard')
            else:
                messages.error(request, "Your account has no role assigned. Contact an administrator.", extra_tags='login_fail')
                return redirect('login')
        else:
            messages.error(request, "Invalid username or password. Please try again.", extra_tags='login_fail')
            return redirect('login')
    return render(request, 'login.html')


def auth_page(request):
    if request.method == "POST":
        username = request.POST.get("username")
        email    = request.POST.get("email")
        password = request.POST.get("password")
        role     = request.POST.get("role", "STAFF").upper()
        if User.objects.filter(username=username).exists():
            messages.error(request, "Username already exists.")
            return redirect("auth")
        User.objects.create_user(username=username, email=email, password=password, role=role)
        messages.success(request, "Account created successfully.")
        return redirect("login")
    return render(request, "login.html")


def forgot_password(request):
    if request.method == 'POST':
        role = request.POST.get('role', 'owner')
        email = request.POST.get('email', '').strip().lower()
        user = _find_user(role, email)
        if user is None:
            messages.error(request, 'No account found with that email address.')
            return render(request, 'STAFF/forgot_password.html', {'selected_role': role, 'email': email})

        from .models import PasswordResetToken
        PasswordResetToken.objects.filter(user_role=role, user_id=user.pk).delete()
        token_value = secrets.token_urlsafe(48)
        expires_at = timezone.now() + timedelta(hours=1)
        PasswordResetToken.objects.create(token=token_value, user_role=role, user_id=user.pk, expires_at=expires_at)

        reset_url = f"{request.scheme}://{request.get_host()}/reset-password/{token_value}/?role={role}"
        _send_reset_email(email, reset_url, role, user)

        return redirect('password_reset_sent_staff' if role == 'staff' else 'password_reset_sent_owner')

    return render(request, 'STAFF/forgot_password.html', {'selected_role': 'owner'})


def password_reset_sent_owner(request):
    return render(request, 'STAFF/password_done.html', {'role': 'owner'})


def password_reset_sent_staff(request):
    return render(request, 'STAFF/password_done.html', {'role': 'staff'})


def reset_password_confirm(request, token):
    from .models import PasswordResetToken
    role = request.GET.get('role') or request.POST.get('role', 'owner')
    try:
        reset_token = PasswordResetToken.objects.get(token=token, user_role=role)
    except PasswordResetToken.DoesNotExist:
        return render(request, 'STAFF/password_reset_co.html', {'valid_token': False})

    if reset_token.expires_at < timezone.now():
        reset_token.delete()
        return render(request, 'STAFF/password_reset_co.html', {'valid_token': False})

    if request.method == 'POST':
        password1 = request.POST.get('password1', '')
        password2 = request.POST.get('password2', '')
        if len(password1) < 8:
            messages.error(request, 'Password must be at least 8 characters.')
        elif password1 != password2:
            messages.error(request, 'Passwords do not match.')
        else:
            user = _find_user_by_id(role, reset_token.user_id)
            if user:
                user.password = make_password(password1)
                user.save()
                reset_token.delete()
                return redirect('password_reset_success')
            else:
                messages.error(request, 'User account not found.')
        return render(request, 'STAFF/password_reset_co.html', {'valid_token': True, 'token': token, 'role': role})

    return render(request, 'STAFF/password_reset_co.html', {'valid_token': True, 'token': token, 'role': role})


def password_reset_success(request):
    # A stale session in this same browser shouldn't outlive the password
    # that authenticated it.
    if request.user.is_authenticated:
        logout(request)
    return render(request, 'STAFF/password_reset_complete.html')


def _find_user(role, email):
    try:
        return User.objects.get(email__iexact=email, role=role.upper())
    except Exception:
        return None


def _find_user_by_id(role, user_id):
    try:
        return User.objects.get(pk=user_id, role=role.upper())
    except Exception:
        return None


def _send_reset_email(email, reset_url, role, user):
    role_label = 'Owner' if role == 'owner' else 'Staff'
    username = getattr(user, 'username', email)
    send_mail(
        subject='CraveCast — Password Reset Request',
        message=f"Hi {username},\n\nReset your password here (expires in 1 hour):\n\n{reset_url}\n\n— The CraveCast Team",
        html_message=(
            f"<p>Hi <strong>{username}</strong>,</p>"
            f"<p>Reset your <strong>{role_label}</strong> account password:</p>"
            f"<p><a href='{reset_url}' style='color:#ff3d5a;font-weight:bold;'>Click here to reset your password</a></p>"
            f"<p style='color:#888;font-size:12px;'>Expires in 1 hour. If you didn't request this, ignore this email.</p>"
        ),
        from_email=getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@cravecast.com'),
        recipient_list=[email],
        fail_silently=False,
    )