"""Generic verified notification demand records."""

import uuid

from onramp.db import models


class NotificationSubscription(models.Model):
    """A verified request to hear when a named resource becomes available."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    resource_type = models.CharField(max_length=64, db_index=True)
    resource_id = models.CharField(max_length=255, db_index=True)
    source = models.CharField(max_length=64, default="app", db_index=True)
    resource_title = models.CharField(max_length=500)
    canonical_resource_id = models.UUIDField(null=True, db_index=True)
    metadata = models.JSONField(default=dict)

    account_id = models.UUIDField(null=True, db_index=True)
    contact_email = models.CharField(max_length=255, null=True)
    contact_email_hash = models.CharField(max_length=64, null=True, db_index=True)
    contact_verified_at = models.DateTimeField(null=True)

    environment = models.CharField(max_length=24, db_index=True)
    audience_type = models.CharField(max_length=24, db_index=True)
    demand_eligible = models.BooleanField(default=False, db_index=True)
    source_app_version = models.CharField(max_length=32, null=True)

    requested_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    notified_at = models.DateTimeField(null=True)
    anonymized_at = models.DateTimeField(null=True)

    class Meta:
        table = "notification_subscriptions"
        unique_together = (
            ("contact_email_hash", "resource_type", "source", "resource_id"),
        )
