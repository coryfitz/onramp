"""Verified, resource-neutral subscriptions and retry-safe delivery."""

from .models import NotificationContactToken, NotificationDelivery, NotificationSubscription
from .service import (
    DeliveryOutcome,
    DispatchReport,
    NotificationRequestContext,
    anonymize_notification_contact,
    cleanup_notification_data,
    dispatch_notification,
    dispatch_subscriptions,
    issue_notification_contact_token,
    notification_report,
    notification_unsubscribe_token,
    notification_unsubscribe_path,
    notification_unsubscribe_url,
    request_subscription,
    revoke_notification_contact_token,
    suppress_subscription,
    verify_subscription,
)

__all__ = [
    "DeliveryOutcome",
    "DispatchReport",
    "NotificationDelivery",
    "NotificationContactToken",
    "NotificationRequestContext",
    "NotificationSubscription",
    "anonymize_notification_contact",
    "cleanup_notification_data",
    "dispatch_notification",
    "dispatch_subscriptions",
    "issue_notification_contact_token",
    "notification_report",
    "notification_unsubscribe_token",
    "notification_unsubscribe_path",
    "notification_unsubscribe_url",
    "request_subscription",
    "revoke_notification_contact_token",
    "suppress_subscription",
    "verify_subscription",
]
