"""Generic verified notification demand records."""

import uuid

from onramp.db import models


class NotificationContactToken(models.Model):
    """Remembered email proof for notifications, never account authentication."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    token_hash = models.CharField(max_length=64, unique=True)
    email_hash = models.CharField(max_length=64, db_index=True)
    resource_type = models.CharField(max_length=64, db_index=True)
    environment = models.CharField(max_length=24, db_index=True)
    verified_at = models.DateTimeField()
    expires_at = models.DateTimeField(null=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        table = "notification_contact_tokens"


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
    suppressed_at = models.DateTimeField(null=True, db_index=True)
    suppression_reason = models.CharField(max_length=64, null=True)
    consent_generation = models.IntegerField(default=0)

    class Meta:
        table = "notification_subscriptions"
        unique_together = (
            (
                "contact_email_hash",
                "resource_type",
                "source",
                "resource_id",
                "environment",
            ),
        )


class NotificationDelivery(models.Model):
    """Retry-safe delivery of one application event to one subscription."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    subscription_id = models.UUIDField(db_index=True)
    event_key = models.CharField(max_length=255, db_index=True)
    environment = models.CharField(max_length=24, db_index=True)
    idempotency_key = models.CharField(max_length=255, unique=True)
    recipient_email_hash = models.CharField(max_length=64, null=True, db_index=True)
    status = models.CharField(max_length=24, default="pending", db_index=True)
    attempt_count = models.IntegerField(default=0)
    provider = models.CharField(max_length=64, null=True)
    provider_message_id = models.CharField(max_length=255, null=True)
    last_error = models.TextField(null=True)
    metadata = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_attempt_at = models.DateTimeField(null=True)
    sent_at = models.DateTimeField(null=True)

    class Meta:
        table = "notification_deliveries"
        # Event keys are application-global logical events. One recipient may
        # request that event through several provider-specific resources, but
        # must receive it only once in each isolated runtime environment.
        unique_together = (("recipient_email_hash", "event_key", "environment"),)
