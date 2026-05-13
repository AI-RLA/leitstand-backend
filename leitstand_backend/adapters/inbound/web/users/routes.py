"""Current-user endpoint under /api/v1/users."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from leitstand_backend.domain.user import User
from leitstand_backend.infrastructure.deps import get_current_user

router = APIRouter(prefix="/api/v1/users", tags=["users"])


@router.get("/me", response_model=User)
def me(user: User = Depends(get_current_user)) -> User:
    return user
