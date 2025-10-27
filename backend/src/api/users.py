# -*- coding: utf-8 -*-
"""
API de Usuarios (/api/users)
---------------------------
CRUD básico de usuarios + cambio de contraseña.
Permisos:
- Listar/crear/eliminar usuarios: admin
- Ver/editar: el propio usuario o admin
- Cambio de contraseña: el propio usuario; admin puede forzar sin contraseña actual
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from db.session import get_db
from db.models import Usuario, Rol
from db.schemas import (
    UsuarioCreate,
    UsuarioResponse,
    UsuarioUpdate,
    PasswordChangeRequest,
)
from core.security import (
    current_user_dep,
    require_roles,
    is_admin,
    hash_password,
    verify_password,
)

router = APIRouter()


# ----------------------------------------
# Helpers
# ----------------------------------------
def _user_to_schema(u: Usuario) -> UsuarioResponse:
    return UsuarioResponse(
        id_usuario=u.id_usuario,
        nombre=u.nombre,
        correo=u.correo,
        telefono=u.telefono,
        fecha_creado=u.fecha_creado,
        rol_id=u.rol_id,
    )


def _ensure_unique_fields(db: Session, correo: Optional[str] = None, telefono: Optional[str] = None, exclude_id: Optional[int] = None):
    if correo:
        q = db.query(Usuario).filter(Usuario.correo == correo)
        if exclude_id:
            q = q.filter(Usuario.id_usuario != exclude_id)
        if db.query(q.exists()).scalar():
            raise HTTPException(status_code=400, detail="El correo ya está registrado")

    if telefono:
        q = db.query(Usuario).filter(Usuario.telefono == telefono)
        if exclude_id:
            q = q.filter(Usuario.id_usuario != exclude_id)
        if db.query(q.exists()).scalar():
            raise HTTPException(status_code=400, detail="El teléfono ya está registrado")


def _validate_role(db: Session, role_id: Optional[int]) -> None:
    if role_id is None:
        return
    exists = db.query(Rol).filter(Rol.id_rol == role_id).first()
    if not exists:
        raise HTTPException(status_code=400, detail="Rol especificado no existe")


# ----------------------------------------
# Endpoints
# ----------------------------------------
@router.get("/ping")
def ping():
    return {"ok": True, "service": "users"}


@router.get("/", response_model=List[UsuarioResponse], dependencies=[Depends(require_roles("admin"))])
def list_users(
    db: Session = Depends(get_db),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    users = (
        db.query(Usuario)
        .order_by(Usuario.id_usuario.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [_user_to_schema(u) for u in users]


@router.post("/", response_model=UsuarioResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_roles("admin"))])
def create_user(payload: UsuarioCreate, db: Session = Depends(get_db)):
    _ensure_unique_fields(db, correo=payload.correo, telefono=payload.telefono)
    _validate_role(db, payload.rol_id)

    nuevo = Usuario(
        nombre=payload.nombre,
        correo=payload.correo,
        telefono=payload.telefono,
        contrasena=hash_password(payload.contrasena),
        rol_id=payload.rol_id,
    )
    db.add(nuevo)
    db.commit()
    db.refresh(nuevo)
    return _user_to_schema(nuevo)


@router.get("/{user_id}", response_model=UsuarioResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_db),
    me: Usuario = Depends(current_user_dep),
):
    u = db.query(Usuario).filter(Usuario.id_usuario == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    if me.id_usuario != u.id_usuario and not is_admin(me):
        raise HTTPException(status_code=403, detail="Operación no permitida")
    return _user_to_schema(u)


@router.put("/{user_id}", response_model=UsuarioResponse)
def update_user(
    user_id: int,
    payload: UsuarioUpdate,
    db: Session = Depends(get_db),
    me: Usuario = Depends(current_user_dep),
):
    u = db.query(Usuario).filter(Usuario.id_usuario == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Permisos: admin o el propio usuario
    if me.id_usuario != u.id_usuario and not is_admin(me):
        raise HTTPException(status_code=403, detail="Operación no permitida")

    # Unicidad de teléfono si viene
    if payload.telefono is not None:
        _ensure_unique_fields(db, telefono=payload.telefono, exclude_id=u.id_usuario)

    # Actualizar campos permitidos
    if payload.nombre is not None:
        u.nombre = payload.nombre
    if payload.telefono is not None:
        u.telefono = payload.telefono

    # Solo admin puede cambiar rol
    if payload.rol_id is not None:
        if not is_admin(me):
            raise HTTPException(status_code=403, detail="Solo un admin puede cambiar el rol")
        _validate_role(db, payload.rol_id)
        u.rol_id = payload.rol_id

    db.commit()
    db.refresh(u)
    return _user_to_schema(u)


@router.delete("/{user_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(require_roles("admin"))])
def delete_user(
    user_id: int,
    db: Session = Depends(get_db),
):
    u = db.query(Usuario).filter(Usuario.id_usuario == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    db.delete(u)
    db.commit()
    return {"deleted": user_id}


@router.post("/{user_id}/password", status_code=status.HTTP_200_OK)
def change_password(
    user_id: int,
    payload: PasswordChangeRequest,
    db: Session = Depends(get_db),
    me: Usuario = Depends(current_user_dep),
):
    u = db.query(Usuario).filter(Usuario.id_usuario == user_id).first()
    if not u:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Permisos: admin o el propio usuario
    if me.id_usuario != u.id_usuario and not is_admin(me):
        raise HTTPException(status_code=403, detail="Operación no permitida")

    if not payload.nueva_contrasena:
        raise HTTPException(status_code=400, detail="La nueva contraseña es obligatoria")

    # Si NO es admin, debe proveer y validar su contraseña actual
    if me.id_usuario == u.id_usuario and not is_admin(me):
        if not payload.contrasena_actual:
            raise HTTPException(status_code=400, detail="Debe indicar su contraseña actual")
        if not verify_password(payload.contrasena_actual, u.contrasena):
            raise HTTPException(status_code=401, detail="Contraseña actual incorrecta")

    u.contrasena = hash_password(payload.nueva_contrasena)
    db.commit()
    return {"id_usuario": u.id_usuario}
