"""Built-in Starlette endpoints for accounts and verified subscriptions."""

from __future__ import annotations

from datetime import timezone
import html
from urllib.parse import parse_qs

from starlette.responses import HTMLResponse, JSONResponse
from starlette.routing import Route

from onramp.api import APIError, bounded_body, json_body
from onramp.auth.config import auth_config
from onramp.notifications.service import (
    NotificationRequestContext,
    issue_notification_contact_token,
    notification_unsubscribe_path,
    notification_unsubscribe_url,
    request_subscription,
    revoke_notification_contact_token,
    subscription_for_unsubscribe_token,
    suppress_subscription,
    verify_subscription,
)

from .security import runtime_environment
from .service import (
    account_for_request,
    account_json,
    delete_account,
    enforce_client_request_limit,
    request_account_code,
    request_account_deletion,
    request_token,
    revoke_session,
    verify_account_code,
)


def auth_routes(app_dir: str) -> list[Route]:
    unsubscribe_headers = {
        "Cache-Control": "no-store",
        "Content-Security-Policy": (
            "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; "
            "base-uri 'none'; frame-ancestors 'none'"
        ),
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
    }

    def notification_request_context(request) -> NotificationRequestContext:
        direct_host = request.client.host if request.client else None
        # Only the ASGI server's trusted-proxy middleware may resolve forwarding
        # headers. Reading X-Forwarded-For here would let clients rotate buckets.
        return NotificationRequestContext(
            client_host=direct_host,
            direct_client_host=direct_host,
            user_agent=request.headers.get("user-agent", "")[:500] or None,
            origin=request.headers.get("origin", "")[:500] or None,
        )

    async def request_code(request):
        await enforce_client_request_limit(
            notification_request_context(request),
            scope="auth_request",
            config_key="auth_ip_hourly_limit",
            app_dir=app_dir,
        )
        body = await json_body(request)
        email = await request_account_code(
            body.get("email"), body.get("intent", ""), app_dir=app_dir
        )
        return JSONResponse(
            {"email": email, "code_sent": True, "expires_in_seconds": 600},
            status_code=202,
        )

    async def verify_code(request):
        await enforce_client_request_limit(
            notification_request_context(request),
            scope="auth_verify",
            config_key="auth_ip_hourly_limit",
            app_dir=app_dir,
        )
        body = await json_body(request)
        account, token, session = await verify_account_code(
            body.get("email"),
            body.get("intent", ""),
            body.get("code"),
            app_dir=app_dir,
        )
        cookie_mode = body.get("session_mode") == "cookie"
        payload = {
            "account": account_json(account),
            "expires_at": session.expires_at.isoformat(),
        }
        if not cookie_mode:
            payload["session_token"] = token
        response = JSONResponse(payload)
        if cookie_mode:
            # Tortoise returns a zoneinfo-backed UTC datetime. Python's HTTP
            # formatter requires the singleton datetime.timezone.utc object.
            cookie_expiry = session.expires_at.astimezone(timezone.utc).replace(
                tzinfo=timezone.utc
            )
            response.set_cookie(
                "onramp_session",
                token,
                httponly=True,
                secure=runtime_environment() in {"staging", "production"},
                samesite="lax",
                expires=cookie_expiry,
                path="/",
            )
        return response

    async def account(request):
        if request.method == "GET":
            return JSONResponse(
                {"account": account_json(await account_for_request(request))}
            )
        await enforce_client_request_limit(
            notification_request_context(request),
            scope="auth_verify",
            config_key="auth_ip_hourly_limit",
            app_dir=app_dir,
        )
        body = await json_body(request)
        current = await account_for_request(request)
        results = await delete_account(current, body.get("code"), app_dir=app_dir)
        response = JSONResponse({"deleted": True, **results})
        response.delete_cookie("onramp_session", path="/")
        return response

    async def logout(request):
        await revoke_session(request_token(request))
        response = JSONResponse({"signed_out": True})
        response.delete_cookie("onramp_session", path="/")
        return response

    async def deletion_request(request):
        await enforce_client_request_limit(
            notification_request_context(request),
            scope="auth_request",
            config_key="auth_ip_hourly_limit",
            app_dir=app_dir,
        )
        await request_account_deletion(
            await account_for_request(request), app_dir=app_dir
        )
        return JSONResponse(
            {"code_sent": True, "expires_in_seconds": 600}, status_code=202
        )

    async def subscribe(request):
        body = await json_body(
            request,
            maximum_bytes=int(
                auth_config(app_dir).get("notification_request_bytes", 16_384)
            ),
        )
        current = await account_for_request(request, required=False)
        subscription, needs_verification = await request_subscription(
            body,
            current,
            app_dir=app_dir,
            request_context=notification_request_context(request),
            notification_token=request.headers.get("x-onramp-notification-token"),
        )
        anonymous = current is None and needs_verification
        payload = {
            "subscription_id": str(subscription.id),
            "status": "unverified" if anonymous or needs_verification else "verified",
            "verification_required": True if anonymous else needs_verification,
            "demand_eligible": False if anonymous else subscription.demand_eligible,
            "suppressed": False if anonymous else subscription.suppressed_at is not None,
        }
        if not anonymous and not needs_verification:
            payload["unsubscribe_url"] = notification_unsubscribe_url(
                subscription, app_dir=app_dir
            )
            payload["unsubscribe_path"] = notification_unsubscribe_path(subscription)
        return JSONResponse(
            payload,
            status_code=202 if anonymous or needs_verification else 200,
        )

    async def verify_subscription_route(request):
        body = await json_body(request)
        remember_email = body.get("remember_email", False)
        if not isinstance(remember_email, bool):
            raise APIError("remember_email must be a boolean.")
        subscription = await verify_subscription(
            body.get("subscription_id"),
            body.get("email"),
            body.get("code"),
            app_dir=app_dir,
            request_context=notification_request_context(request),
        )
        payload = {
            "subscription_id": str(subscription.id),
            "status": "verified",
            "verified": True,
            "demand_eligible": subscription.demand_eligible,
            "unsubscribe_url": notification_unsubscribe_url(
                subscription, app_dir=app_dir
            ),
            "unsubscribe_path": notification_unsubscribe_path(subscription),
        }
        if remember_email:
            token, contact = await issue_notification_contact_token(
                subscription, app_dir=app_dir
            )
            payload["notification_token"] = token
            payload["notification_token_expires_at"] = (
                contact.expires_at.isoformat() if contact.expires_at is not None else None
            )
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})

    async def revoke_notification_contact(request):
        await enforce_client_request_limit(
            notification_request_context(request),
            scope="notification_revoke",
            config_key="notification_ip_hourly_limit",
            app_dir=app_dir,
        )
        await revoke_notification_contact_token(
            request.headers.get("x-onramp-notification-token")
        )
        return JSONResponse({"revoked": True}, headers={"Cache-Control": "no-store"})

    async def unsubscribe(request):
        if request.method == "GET":
            token = request.query_params.get("token", "")
            subscription = await subscription_for_unsubscribe_token(token)
            escaped_token = html.escape(token, quote=True)
            escaped_title = html.escape(subscription.resource_title)
            return HTMLResponse(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<meta name='robots' content='noindex,nofollow'>"
                "<title>Stop notification</title></head>"
                "<body style='font-family:system-ui;max-width:36rem;margin:4rem auto;"
                "padding:0 1rem'><h1>Stop this notification?</h1>"
                f"<p>You will no longer receive updates for <strong>{escaped_title}"
                "</strong>.</p>"
                "<form method='post'>"
                f"<input type='hidden' name='token' value='{escaped_token}'>"
                "<button type='submit'>Stop notification</button></form></body></html>",
                headers=unsubscribe_headers,
            )

        content_type = request.headers.get("content-type", "").lower()
        form_response = "application/json" not in content_type
        if form_response:
            raw_body = (
                await bounded_body(request, maximum_bytes=8_192)
            ).decode("utf-8", "replace")
            token = parse_qs(raw_body).get("token", [""])[0]
        else:
            token = (await json_body(request)).get("token")
        subscription = await suppress_subscription(token)
        if form_response:
            return HTMLResponse(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<meta name='viewport' content='width=device-width,initial-scale=1'>"
                "<meta name='robots' content='noindex,nofollow'>"
                "<title>Notification stopped</title></head>"
                "<body style='font-family:system-ui;max-width:36rem;margin:4rem auto;"
                "padding:0 1rem'><h1>Notification stopped</h1>"
                "<p>You will not receive this update.</p></body></html>",
                headers=unsubscribe_headers,
            )
        return JSONResponse(
            {"unsubscribed": True, "subscription_id": str(subscription.id)},
            headers=unsubscribe_headers,
        )

    return [
        Route("/api/auth/request", request_code, methods=["POST"]),
        Route("/api/auth/verify", verify_code, methods=["POST"]),
        Route("/api/auth/logout", logout, methods=["POST"]),
        Route("/api/account", account, methods=["GET", "DELETE"]),
        Route(
            "/api/account/delete/request", deletion_request, methods=["POST"]
        ),
        Route("/api/notifications/subscriptions", subscribe, methods=["POST"]),
        Route(
            "/api/notifications/subscriptions/verify",
            verify_subscription_route,
            methods=["POST"],
        ),
        Route(
            "/api/notifications/contact/revoke",
            revoke_notification_contact,
            methods=["POST"],
        ),
        Route(
            "/api/notifications/unsubscribe",
            unsubscribe,
            methods=["GET", "POST"],
        ),
    ]
