"""
Modul autentikasi:
- Hashing & verifikasi password (bcrypt langsung, tanpa passlib -- ada
  konflik versi dikenal antara passlib dan bcrypt terbaru)
- Pembuatan & decode JWT access token
- Dependency FastAPI untuk mendapatkan user yang sedang login dari token
"""
import os
import bcrypt
from datetime import datetime, timedelta
from jose import jwt, JWTError
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User

# --- Konfigurasi ---
# PENTING: sebelum deploy ke production, set environment variable
# NETMONITOR_SECRET_KEY ke string acak yang panjang & rahasia.
# Contoh generate: python3 -c "import secrets; print(secrets.token_hex(32))"
SECRET_KEY = os.environ.get("NETMONITOR_SECRET_KEY", "dev-secret-key-ganti-saat-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7  # 7 hari

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


# --- Password hashing ---

def hash_password(plain_password: str) -> str:
    """Hash password dengan bcrypt. Truncate ke 72 byte (batas bcrypt)."""
    pw_bytes = plain_password.encode("utf-8")[:72]
    hashed = bcrypt.hashpw(pw_bytes, bcrypt.gensalt())
    return hashed.decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    pw_bytes = plain_password.encode("utf-8")[:72]
    return bcrypt.checkpw(pw_bytes, hashed_password.encode("utf-8"))


# --- JWT ---

def create_access_token(user_id: int, email: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(user_id), "email": email, "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token(token: str) -> dict:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except JWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token tidak valid atau kedaluwarsa",
            headers={"WWW-Authenticate": "Bearer"},
        )


# --- Dependency: ambil user yang sedang login dari token ---

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    payload = decode_access_token(token)
    user_id = payload.get("sub")
    if user_id is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token tidak valid")

    user = await db.get(User, int(user_id))
    if user is None or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User tidak ditemukan atau nonaktif")
    return user
