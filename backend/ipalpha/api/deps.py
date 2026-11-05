import secrets

from fastapi import Header, HTTPException

from ipalpha.config import get_settings


def require_admin(x_admin_token: str | None = Header(None)) -> None:
    expected = get_settings().admin_token
    if not expected:
        raise HTTPException(503, "Admin endpoints are disabled; set ADMIN_TOKEN.")
    if x_admin_token is None or not secrets.compare_digest(x_admin_token, expected):
        raise HTTPException(401, "Invalid or missing X-Admin-Token.")
