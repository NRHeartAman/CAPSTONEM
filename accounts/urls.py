from django.urls import path
from . import views
from owner import views as owner_views

urlpatterns = [
    path('',                  views.login_view,                  name='landing'),
    path('login/',            views.login_view,                  name='login'),
    path('register/',         views.auth_page,                   name='auth'),
    path('admin_management/', owner_views.admin_management_view, name='admin-management'),

    # Custom password reset flow
    path('forgot-password/',                    views.forgot_password,          name='forgot_password'),
    path('forgot-password/sent/owner/',         views.password_reset_sent_owner, name='password_reset_sent_owner'),
    path('forgot-password/sent/staff/',         views.password_reset_sent_staff, name='password_reset_sent_staff'),
    # /success/ must be registered before <str:token>/ — otherwise it matches
    # the token pattern first (token="success") and never reaches this view.
    path('reset-password/success/',             views.password_reset_success,   name='password_reset_success'),
    path('reset-password/<str:token>/',         views.reset_password_confirm,   name='reset_password_confirm'),
    # Notifications (bell menu in base.html)
    # NOTE: /notifications/api/ generates + lists notifications (used when the
    # panel is opened); /notifications/count/ is the lightweight unread-count
    # check (used by the 60s poll) — keep these separate, don't point the poll
    # at the generation endpoint again.
    path('notifications/api/',        views.notifications_json,        name='notifications_api'),
    path('notifications/count/',      views.notifications_unread_count, name='notifications_count'),
    path('notifications/read-all/',   views.mark_notifications_read,   name='notifications_read_all'),
    path('notifications/<int:pk>/read/', views.mark_notification_read, name='notification_read'),
]