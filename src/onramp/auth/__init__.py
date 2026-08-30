"""Batteries-included passwordless accounts for OnRamp applications."""

from .models import Account, AccountSession, AudienceIdentity, EmailChallenge
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
    "EmailChallenge",
    "account_for_request",
    "account_for_token",
    "classify_email",
    "request_account_code",
    "verify_account_code",
]
