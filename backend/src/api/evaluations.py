# -*- coding: utf-8 -*-
"""
Router para la API de Evaluaciones Nutricionales
Endpoints: /api/evaluations/
"""

from datetime import datetime, date, timezone
from decimal import Decimal, ROUND_HALF_UP
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Column, Integer, Date, DateTime, DECIMAL, String, Text, ForeignKey
from sqlalchemy.orm import Session, relationship

# Asegúrate de que estos imports apunten a tus archivos reales
from db.session import get_db
from db.base import Base
from db.models import Alerta 


# -------------------------------------------------------------------
# Modelo ORM local (mapea a la tabla 'evaluaciones')
# -------------------------------------------------------------------
class Evaluation(Base):
    __tablename__ = "evaluaciones"
    __table_args__ = {"extend_existing": True}

    id = Column(Integer, primary_key=True, index=True)
    # CLAVE CORREGIDA: Apunta a la PK real en la tabla infantes
    child_id = Column(Integer, ForeignKey("infantes.id_infante"), nullable=False) 
    
    # La relación usa la clave foránea correcta para evitar el error 500
    infante = relationship("Infante", back_populates="evaluaciones", foreign_keys=[child_id]) 
    
    fecha = Column(Date, nullable=False)
    peso_kg = Column(DECIMAL(5, 2), nullable=False)
    talla_cm = Column(DECIMAL(5, 2), nullable=False)
    imc = Column(DECIMAL(5, 2), nullable=False)
    estado_nutricional = Column(String(32), nullable=False)
    observaciones = Column(Text)
    created_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


# -------------------------------------------------------------------
# Schemas Pydantic
# -------------------------------------------------------------------
class EvaluationCreate(BaseModel):
    child_id: int = Field(..., description="ID del infante")
    fecha: date
    peso_kg: float = Field(..., gt=0)
    talla_cm: float = Field(..., gt=0)
    observaciones: Optional[str] = None


class EvaluationUpdate(BaseModel):
    fecha: Optional[date] = None
    peso_kg: Optional[float] = Field(None, gt=0)
    talla_cm: Optional[float] = Field(None, gt=0)
    observaciones: Optional[str] = None


class EvaluationOut(BaseModel):
    id: int
    child_id: int
    fecha: date
    peso_kg: float
    talla_cm: float
    imc: float
    estado_nutricional: str
    observaciones: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class AlertaOut(BaseModel):
    id_alerta: int
    infante_id: int
    seguimiento_id: Optional[int] = None
    tipo_alerta: str
    mensaje: str
    estado_alerta: str
    fecha_creacion: datetime
    fecha_resuelta: Optional[datetime] = None

    class Config:
        from_attributes = True


# -------------------------------------------------------------------
# Lógica de negocio (IMC + estado + alertas) - Funciones auxiliares
# -------------------------------------------------------------------
def _calc_imc(peso_kg: float, talla_cm: float) -> float:
    m = Decimal(str(talla_cm)) / Decimal("100")
    kg = Decimal(str(peso_kg))
    if m <= 0:
        raise ValueError("La talla debe ser > 0")
    imc = kg / (m * m)
    return float(imc.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def _clasificar_estado(imc: float) -> str:
    # Clasificación simple
    if imc < 14:
        return "bajo"
    if imc < 18.5:
        return "riesgo"
    if imc < 25:
        return "normal"
    if imc < 30:
        return "sobrepeso"
    return "obesidad"


def _crear_alerta_si_aplica(db: Session, child_id: int, estado: str, imc: float):
    if estado == "normal":
        return
    tipo_map = {
        "bajo": "imc_bajo",
        "riesgo": "imc_riesgo",
        "sobrepeso": "imc_sobrepeso",
        "obesidad": "imc_obesidad",
    }
    tipo = tipo_map.get(estado, "imc_fuera_rango")
    mensaje = f"IMC {imc:.2f} clasificado como '{estado}'. Revisión requerida."

    alerta = Alerta(
        infante_id=child_id,
        seguimiento_id=None,
        tipo_alerta=tipo,
        mensaje=mensaje,
        estado_alerta="pendiente",
        fecha_creacion=datetime.now(timezone.utc),
    )
    db.add(alerta)
    db.flush()


def _resolver_alertas_previas_si_normal(db: Session, child_id: int):
    pendientes = (
        db.query(Alerta)
        .filter(
            Alerta.infante_id == child_id,
            Alerta.estado_alerta == "pendiente",
            Alerta.tipo_alerta.in_(
                ["imc_bajo", "imc_riesgo", "imc_sobrepeso", "imc_obesidad", "imc_fuera_rango"]
            ),
        )
        .all()
    )
    if not pendientes:
        return
    now = datetime.now(timezone.utc)
    for a in pendientes:
        a.estado_alerta = "resuelta"
        a.fecha_resuelta = now


# -------------------------------------------------------------------
# Router (SIN prefijo; el prefijo lo añade main.py)
# -------------------------------------------------------------------
router = APIRouter()


@router.post("/", response_model=EvaluationOut, status_code=status.HTTP_201_CREATED)
# Asume que un Depends(get_current_active_user) está implícito o en un wrapper
def create_evaluation(payload: EvaluationCreate, db: Session = Depends(get_db)): 
    try:
        imc = _calc_imc(payload.peso_kg, payload.talla_cm)
        estado = _clasificar_estado(imc)

        ev = Evaluation(
            child_id=payload.child_id,
            fecha=payload.fecha,
            peso_kg=payload.peso_kg,
            talla_cm=payload.talla_cm,
            imc=imc,
            estado_nutricional=estado,
            observaciones=payload.observaciones,
            created_at=datetime.now(timezone.utc),
        )
        db.add(ev)

        if estado != "normal":
            _crear_alerta_si_aplica(db, payload.child_id, estado, imc)

        db.commit()
        db.refresh(ev)
        return ev
    except HTTPException:
        raise
    except Exception as ex:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ex))


@router.get("/", response_model=List[EvaluationOut])
def list_evaluations(
    child_id: Optional[int] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    q = db.query(Evaluation)
    if child_id is not None:
        q = q.filter(Evaluation.child_id == child_id)
    q = q.order_by(Evaluation.fecha.desc(), Evaluation.id.desc()).limit(limit).offset(offset)
    return q.all()


@router.get("/{evaluation_id}", response_model=EvaluationOut)
def get_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")
    return ev


@router.put("/{evaluation_id}", response_model=EvaluationOut)
def update_evaluation(
    evaluation_id: int,
    payload: EvaluationUpdate,
    db: Session = Depends(get_db),
):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        raise HTTPException(status_code=404, detail="Evaluación no encontrada")

    try:
        if payload.fecha is not None:
            ev.fecha = payload.fecha
        if payload.peso_kg is not None:
            ev.peso_kg = payload.peso_kg
        if payload.talla_cm is not None:
            ev.talla_cm = payload.talla_cm
        if payload.observaciones is not None:
            ev.observaciones = payload.observaciones

        if payload.peso_kg is not None or payload.talla_cm is not None:
            imc = _calc_imc(float(ev.peso_kg), float(ev.talla_cm))
            estado = _clasificar_estado(imc)
            ev.imc = imc
            ev.estado_nutricional = estado

            if estado == "normal":
                _resolver_alertas_previas_si_normal(db, ev.child_id)
            else:
                _crear_alerta_si_aplica(db, ev.child_id, estado, imc)

        db.commit()
        db.refresh(ev)
        return ev
    except HTTPException:
        raise
    except Exception as ex:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(ex))


@router.delete("/{evaluation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_evaluation(evaluation_id: int, db: Session = Depends(get_db)):
    ev = db.query(Evaluation).filter(Evaluation.id == evaluation_id).first()
    if not ev:
        return
    db.delete(ev)
    db.commit()
    return


@router.get("/alerts/{child_id}", response_model=List[AlertaOut])
def active_alerts(child_id: int, db: Session = Depends(get_db)):
    q = (
        db.query(Alerta)
        .filter(
            Alerta.infante_id == child_id,
            Alerta.estado_alerta == "pendiente",
            Alerta.tipo_alerta.in_(
                ["imc_bajo", "imc_riesgo", "imc_sobrepeso", "imc_obesidad", "imc_fuera_rango"]
            ),
        )
        .order_by(Alerta.fecha_creacion.desc(), Alerta.id_alerta.desc())
        .all()
    )
    return q