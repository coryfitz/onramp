"""Batteries-included passwordless accounts for OnRamp applications."""

from .models import (
    Account,
    AccountSession,
    AudienceIdentity,
    ClientRequestRateLimit,
    EmailChallenge,
    EmailChallengeRateLimit,
)
from .email import EmailSendResult, send_transactional_email
from .service import (
    account_for_request,
    account_for_token,
    classify_email,
    request_account_code,
    verify_account_code,
)

__all__ = [
    "Account",
    "AccountSession",
    "AudienceIdentity",
    "ClientRequestRateLimit",
    "EmailChallenge",
    "EmailChallengeRateLimit",
    "EmailSendResult",
    "account_for_request",
    "account_for_token",
    "classify_email",
    "request_account_code",
    "send_transactional_email",
    "verify_account_code",
]
