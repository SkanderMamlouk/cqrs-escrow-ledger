from django.urls import path

from web import views

urlpatterns = [
    path("", views.dashboard, name="dashboard"),
    path("fragments/recent/", views.recent_events, name="recent_events"),
    path("accounts/<uuid:account_id>/", views.account_detail, name="account"),
    path("alerts/", views.alerts, name="alerts"),
    path("accounts/open/", views.open_account, name="open_account"),
    path("accounts/<uuid:account_id>/command/<str:name>/", views.account_command, name="account_command"),
    path("accounts/<uuid:account_id>/race-demo/", views.race_demo, name="race_demo"),
    path("accounts/<uuid:account_id>/replay/", views.replay, name="replay"),
    path("accounts/<uuid:account_id>/audit/", views.audit, name="audit"),
    path("audit/<int:run_id>/", views.audit_run, name="audit_run"),
    path("events/", views.event_store, name="events"),
    path("api/accounts/<uuid:account_id>/balance-at/", views.balance_at_api, name="balance_at"),
    path("system/", views.system, name="system"),
    path("webhooks/receive/", views.webhook_receive, name="webhook_receive"),
    path("health/", views.health, name="health"),
]

