"""Session-based authentication and role/project enforcement.

Sign-in is intentionally simple (username + demo password) but every API route
resolves the caller from a server-side session token and enforces both role and
project membership. The browser cannot pick a role or project it was not
granted (PRD: right access).
"""

from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException, status

from backend.database import (
    get_user_by_session,
    user_has_project_access,
)

# Roles permitted to perform each action.
ROLE_ALLOWED = {
    "read": {"Project Manager", "Team Member", "Sponsor", "Admin"},
    "approve": {"Project Manager"},
    "edit": {"Project Manager"},
    "reject": {"Project Manager"},
    "upload_meeting": {"Project Manager", "Admin"},
    "manage_private": {"Project Manager", "Admin"},
    "manage_project": {"Project Manager", "Admin"},
    "admin": {"Admin"},
    "integrations": {"Admin"},
    "update_any_action": {"Project Manager", "Admin"},
    "view_metrics": {"Project Manager", "Sponsor", "Admin"},
}

ALLOWED_ROLES = {"Project Manager", "Team Member", "Sponsor", "Admin"}


class UserContext:
    def __init__(self, user_id: int, username: str, display_name: str, role: str) -> None:
        self.user_id = user_id
        self.username = username
        self.display_name = display_name
        self.role = role

    @property
    def is_admin(self) -> bool:
        return self.role == "Admin"

    def can(self, action: str) -> bool:
        return self.role in ROLE_ALLOWED.get(action, set())

    def require(self, action: str) -> "UserContext":
        if not self.can(action):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{self.role}' is not allowed to {action.replace('_', ' ')}.",
            )
        return self

    async def require_project(self, project_key: str, action: str = "read") -> "UserContext":
        self.require(action)
        allowed = await user_has_project_access(self.user_id, self.is_admin, project_key)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"You do not have access to project '{project_key}'.",
            )
        return self


async def get_current_user(authorization: Optional[str] = Header(None)) -> UserContext:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Sign in required. Send Authorization: Bearer <token>.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = authorization.split(" ", 1)[1].strip()
    user = await get_user_by_session(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is invalid or has expired. Sign in again.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if user.role not in ALLOWED_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Role '{user.role}' is not recognized.",
        )
    return UserContext(
        user_id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
    )


async def require_admin(user: UserContext) -> UserContext:
    return user.require("admin")


async def require_integrations_admin(user: UserContext) -> UserContext:
    return user.require("integrations")
