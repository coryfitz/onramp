"""Built-in Starlette endpoints for accounts and verified subscriptions."""

from __future__ import annotations

from datetime import timezone

from starlette.responses import JSONResponse
from starlette.routing import Route

from onramp.api import json_body
from onramp.notifications.service import request_subscription, verify_subscription

from .security import runtime_environment
from .service import (
    account_for_request,
    account_json,
    delete_account,
    request_account_code,
    request_account_deletion,
    request_token,
    revoke_session,
    verify_account_code,
)


def auth_routes(app_dir: str) -> list[Route]:
    async def request_code(request):
        body = await json_body(request)
        email = await request_account_code(
            body.get("email"), body.get("intent", ""), app_dir=app_dir
        )
        return JSONResponse(
            {"email": email, "code_sent": True, "expires_in_seconds": 600},
            status_code=202,
        )

    async def verify_code(request):
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
        await request_account_deletion(
            await account_for_request(request), app_dir=app_dir
        )
        return JSONResponse(
            {"code_sent": True, "expires_in_seconds": 600}, status_code=202
        )

    async def subscribe(request):
        body = await json_body(request)
        current = await account_for_request(request, required=False)
        subscription, needs_verification = await request_subscription(
            body, current, app_dir=app_dir
        )
        return JSONResponse(
            {
                "subscription_id": str(subscription.id),
                "status": "unverified" if needs_verification else "verified",
                "verification_required": needs_verification,
                "demand_eligible": subscription.demand_eligible,
            },
            status_code=202 if needs_verification else 200,
        )

    async def verify_subscription_route(request):
        body = await json_body(request)
        subscription = await verify_subscription(
            body.get("subscription_id"),
            body.get("email"),
            body.get("code"),
            app_dir=app_dir,
        )
        return JSONResponse(
            {
                "subscription_id": str(subscription.id),
                "status": "verified",
                "verified": True,
                "demand_eligible": subscription.demand_eligible,
            }
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
    ]
