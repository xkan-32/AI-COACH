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
