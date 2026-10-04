"""
Template context shared by every page (registered in settings.TEMPLATES).
"""


def pending_approvals(request):
    """Feeds the sidebar's Approvals badge (base.html). Owner-only — staff
    never see that menu item, so skip the queries for them."""
    user = getattr(request, 'user', None)
    if not (user and user.is_authenticated and getattr(user, 'role', '') == 'OWNER'):
        return {}

    from owner.models import InventoryRequest, EventRequest, SalesUploadRequest
    count = (
        InventoryRequest.objects.filter(status='pending').count()
        + EventRequest.objects.filter(status='pending').count()
        + SalesUploadRequest.objects.filter(status='pending').count()
    )
    return {'pending_approval_count': count}
