"""HTTP endpoints exposing authentication and user data."""

from __future__ import annotations

from fastapi import APIRouter

from app.auth.service import AuthService, build_default_auth_service
from app.database.connection import get_database
from app.users.repository import UserRepository

router = APIRouter(prefix="/auth")


def get_auth_service() -> AuthService:
    return build_default_auth_service(UserRepository(get_database()))


@router.post("/login/{provider_name}")
async def login(provider_name: str, code: str) -> dict[str, str]:
    """Exchange an OAuth code for a session with the given provider."""
    service = get_auth_service()
    user = await service.login_with_provider(provider_name, code)
    return {"email": user.email, "role": user.role}


@router.get("/me")
async def current_user(user_id: int) -> dict[str, str]:
    """Return the profile of the currently authenticated user."""
    return {"id": str(user_id)}
