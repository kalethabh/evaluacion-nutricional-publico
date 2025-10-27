# -*- coding: utf-8 -*-
"""
core.security
--------------
Utilidades de seguridad compartidas:
- OAuth2PasswordBearer apuntando a /api/auth/token
- Normalización/hash/verificación de contraseñas (bcrypt)
- Decodificación de JWT y obtención de usuario actual
- Dependencias para exigir roles (admin, nutricionista, cuidador)
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta
from typing import Optional, Callable, Iterable

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from db.session import get_db
from db.models import Usuario

# ---------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "supersecretkey")
ALGORITHM = os.getenv("ALGORITHM", "HS256")
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))

# Debe coincidir con el endpoint real del token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token")

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


# ---------------------------------------------------------------------
# Password helpers (compatibles con api.auth)
# ---------------------------------------------------------------------
def _bcrypt_normalize(password: str) -> str:
    """Bcrypt usa 72 bytes. Normalizamos/truncamos a 72 bytes (utf-8)."""
    if not isinstance(password, str):
        raise ValueError("La contraseña debe ser texto.")
    pw_bytes = password.strip().encode("utf-8")
    if len(pw_bytes) > 72:
        pw_bytes = pw_bytes[:72]
    return pw_bytes.decode("utf-8", "ignore")


def hash_password(password: str) -> str:
    return pwd_context.hash(_bcrypt_normalize(password))


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(_bcrypt_normalize(plain_password), hashed_password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    now = datetime.utcnow()
    expire = now + (expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES))
    to_encode.update({"iat": now, "exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


# ---------------------------------------------------------------------
# JWT → usuario actual
# ---------------------------------------------------------------------
def get_current_user(db: Session, token: str) -> Usuario:
    """Decodifica el JWT y retorna el Usuario; si falla, 401."""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="No autenticado o token inválido",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        correo: str = payload.get("sub")  # email guardado en el token
        if correo is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = db.query(Usuario).filter(Usuario.correo == correo).first()
    if not user:
        raise credentials_exception
    return user


# Dependencia lista para usar en routers
def current_user_dep(
    db: Session = Depends(get_db),
    token: str = Depends(oauth2_scheme),
) -> Usuario:
    return get_current_user(db, token)


def is_admin(user: Usuario) -> bool:
    return bool(user.rol and (user.rol.nombre or "").lower() == "admin")


def require_roles(*allowed: Iterable[str]) -> Callable[[Usuario], Usuario]:
    """
    Devuelve una dependencia que exige que el usuario tenga uno
    de los roles permitidos. Si no se especifican roles, solo verifica autenticación.
    """
    allowed_set = {str(r).lower() for r in allowed} if allowed else set()

    def _dep(user: Usuario = Depends(current_user_dep)) -> Usuario:
        if not allowed_set:
            return user
        role_name = (user.rol.nombre if user.rol else "") or ""
        if role_name.lower() not in allowed_set:
            raise HTTPException(status_code=403, detail="Operación no permitida (rol insuficiente)")
        return user

    return _dep
