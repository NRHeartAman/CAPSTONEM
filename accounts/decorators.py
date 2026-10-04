"""
Reusable role-check decorators.

The codebase already enforces OWNER-only access consistently via an inline
check at the top of each view (`if request.user.role != 'OWNER': redirect(...)`
or, for JSON endpoints, a 403 JsonResponse) — that pattern is correct and is
left as-is on existing views to avoid unnecessary risk. This decorator exists
so *new* owner-only views (added for the panelist revisions) don't have to
hand-roll that check again, and so backend enforcement stays consistent
regardless of what the frontend shows or hides.
"""
from functools import wraps
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect
from django.http import JsonResponse


def owner_required(view_func):
    """Redirects non-Owner users to their dashboard (HTML views)."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, 'role', 'STAFF') != 'OWNER':
            return redirect('staff-dashboard')
        return view_func(request, *args, **kwargs)
    return _wrapped


def owner_required_json(view_func):
    """Same check, but returns 403 JSON instead of redirecting — for API
    endpoints where a redirect response wouldn't make sense to the caller."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if getattr(request.user, 'role', 'STAFF') != 'OWNER':
            return JsonResponse({'error': 'Access denied.'}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped
