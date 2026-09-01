async def verify_cloud_task_request(request, settings) -> None:
    """Require a Google-signed OIDC token on production task endpoints."""
    if settings.app_env == "local":
        return
    import asyncio

    from fastapi import HTTPException
    from google.auth.transport import requests as google_requests
    from google.oauth2 import id_token

    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing task identity token")
    try:
        claims = await asyncio.to_thread(
            id_token.verify_oauth2_token,
            token,
            google_requests.Request(),
            settings.worker_url,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid task identity token"
        ) from exc
    if claims.get("email") != settings.task_service_account_email:
        raise HTTPException(status_code=403, detail="Unexpected task service account")


class ApprovalActionError(ValueError):
    pass


class ApprovalActionSigner:
    def __init__(self, secret: str, clock=None) -> None:
        import time

        self._secret = secret.encode()
        self._clock = clock or time.time

    def create(
        self,
        proposal_id: str,
        line_user_id: str,
        decision: str,
        expires_at: int,
    ) -> str:
        import hashlib
        import hmac

        payload = f"{proposal_id}:{line_user_id}:{decision}:{expires_at}"
        signature = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        return (
            f"action=proposal&proposal_id={proposal_id}&decision={decision}"
            f"&expires_at={expires_at}&signature={signature}"
        )

    def verify(
        self,
        proposal_id: str,
        line_user_id: str,
        decision: str,
        expires_at: str,
        signature: str,
    ) -> None:
        import hashlib
        import hmac

        try:
            expiry = int(expires_at)
        except ValueError as exc:
            raise ApprovalActionError("Invalid approval expiry") from exc
        if expiry < int(self._clock()):
            raise ApprovalActionError("Approval action has expired")
        payload = f"{proposal_id}:{line_user_id}:{decision}:{expiry}"
        expected = hmac.new(self._secret, payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise ApprovalActionError("Invalid approval signature")
