from django.db import models


class AuditRun(models.Model):
    STATUS = [("VERIFIED", "AI answer verified"), ("FALLBACK", "Deterministic fallback"),
              ("REJECTED", "Rejected")]
    created_at = models.DateTimeField(auto_now_add=True)
    account_id = models.UUIDField(db_index=True)
    owner_name = models.CharField(max_length=80)
    window_start = models.DateTimeField(null=True, blank=True)
    window_end = models.DateTimeField(null=True, blank=True)
    provider = models.CharField(max_length=20)
    model = models.CharField(max_length=60)
    provider_reason = models.CharField(max_length=255, blank=True, default="")
    status = models.CharField(max_length=10, choices=STATUS)
    report = models.JSONField(default=dict)
    verification = models.JSONField(default=dict)
    attempts = models.JSONField(default=list)
    evidence = models.JSONField(default=dict)
    latency_ms = models.IntegerField(default=0)
    closing_balance_tnd = models.CharField(max_length=24, default="0.000")

    class Meta:
        ordering = ["-created_at"]

