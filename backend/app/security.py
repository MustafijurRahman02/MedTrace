"""Password hashing, JWT tokens, role-based access control, rate limiting."""
import hashlib
import hmac
import os
import secrets
import time
from collections import defaultdict, deque

import jwt
from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from .db import get_db

ITERATIONS = 200_000
TOKEN_HOURS = 8
_DEFAULT_KEY = secrets.token_hex(32)  # random per process if SECRET_KEY is not set


def secret_key() -> str:
    return os.environ.get("SECRET_KEY") or _DEFAULT_KEY


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, iters, salt, digest = stored.split("$")
        test = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iters))
        return hmac.compare_digest(test.hex(), digest)
    except (ValueError, AttributeError):
        return False


DUMMY_HASH = hash_password("not-a-real-password")  # equalises timing for unknown emails


def create_token(user_id: int) -> str:
    now = int(time.time())
    return jwt.encode({"sub": str(user_id), "iat": now, "exp": now + TOKEN_HOURS * 3600},
                      secret_key(), algorithm="HS256")


bearer = HTTPBearer(auto_error=False)


def optional_user(creds: HTTPAuthorizationCredentials = Depends(bearer), conn=Depends(get_db)):
    """Return the active user for a valid token, else None. Role is always read from the DB."""
    if not creds:
        return None
    try:
        payload = jwt.decode(creds.credentials, secret_key(), algorithms=["HS256"])
        uid = int(payload["sub"])
    except (jwt.PyJWTError, KeyError, ValueError):
        return None
    row = conn.execute(
        "SELECT u.user_id, u.name, u.email, r.role_name AS role FROM users u"
        " JOIN roles r ON r.role_id = u.role_id WHERE u.user_id = ? AND u.is_active = 1", (uid,)
    ).fetchone()
    return dict(row) if row else None


def require(*roles: str):
    """Deny-by-default role check, enforced server-side on every protected endpoint."""
    def dep(user=Depends(optional_user)):
        if user is None:
            raise HTTPException(401, "Authentication required")
        if roles and user["role"] not in roles:
            raise HTTPException(403, "Your role is not permitted to do this")
        return user
    return dep


class RateLimiter:
    def __init__(self, limit: int, window: int):
        self.limit, self.window = limit, window
        self.hits = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.time()
        q = self.hits[key]
        while q and q[0] < now - self.window:
            q.popleft()
        if len(q) >= self.limit:
            raise HTTPException(429, "Too many requests. Please wait and try again.")
        q.append(now)


login_limiter = RateLimiter(limit=10, window=300)
verify_limiter = RateLimiter(limit=60, window=60)


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"
