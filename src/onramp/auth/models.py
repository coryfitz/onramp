"""Framework-owned account, challenge, and session models."""

from __future__ import annotations

import uuid

from onramp.db import models


class Account(models.Model):
    """An explicit, verified, email-only application account."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    email = models.CharField(max_length=255, unique=True)
    email_hash = models.CharField(max_length=64, unique=True, db_index=True)
    audience_type = models.CharField(max_length=24, default="regular", db_index=True)
    roles = models.JSONField(default=list)
    verified_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        table = "accounts"


class AccountSession(models.Model):
    """An opaque, revocable session with only its digest stored."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    account_id = models.UUIDField(db_index=True)
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField()

    class Meta:
        table = "account_sessions"


class EmailChallenge(models.Model):
    """A short-lived single-use challenge scoped to one explicit purpose."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    email = models.CharField(max_length=255)
    email_hash = models.CharField(max_length=64, db_index=True)
    purpose = models.CharField(max_length=48, db_index=True)
    subject_id = models.CharField(max_length=128, null=True, db_index=True)
    code_digest = models.CharField(max_length=64)
    attempts = models.IntegerField(default=0)
    expires_at = models.DateTimeField(db_index=True)
    consumed_at = models.DateTimeField(null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        table = "email_challenges"


class AudienceIdentity(models.Model):
    """A server-controlled tester/internal classification keyed by email HMAC."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4)
    email_hash = models.CharField(max_length=64, unique=True, db_index=True)
    audience_type = models.CharField(max_length=24)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        table = "audience_identities"
