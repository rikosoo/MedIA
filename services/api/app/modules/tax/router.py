"""Tributos PF/PJ: alíquotas médias padrão (estimativas) e o efeito delas no mês."""

from datetime import date

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.audit import audit
from app.core.deps import TenantContext, get_tenant
from app.core.rbac import P_FIN_READ, P_GOAL_READ, P_ORG_UPDATE
from app.db.models import TaxSetting
from app.db.session import get_db
from app.modules.goals.projection import breakdown, month_numbers
from app.modules.tax.defaults import DEFAULT_PF_BPS, DEFAULT_PJ_BPS, load

router = APIRouter(prefix="/api/v1/tax", tags=["tax"])

MONTH_RE = r"^\d{4}-(0[1-9]|1[0-2])$"
NOTES = [
    "São médias de referência, não tributos apurados: confirme com seu contador.",
    "A alíquota informada na renda ou no plano do mês sempre vale mais que o padrão.",
    "CLT e outros regimes não têm padrão: informe a retenção na própria renda.",
]


def _out(d) -> dict:
    return {
        "enabled": d.enabled, "pf_bps": d.pf_bps, "pj_bps": d.pj_bps, "clinic_regime": d.clinic_regime,
        "reference": {"pf_bps": DEFAULT_PF_BPS, "pj_bps": DEFAULT_PJ_BPS}, "notes": NOTES,
    }  # fmt: skip


class SettingsIn(BaseModel):
    enabled: bool
    pf_bps: int = Field(ge=0, le=10_000)
    pj_bps: int = Field(ge=0, le=10_000)
    clinic_regime: str = Field(pattern="^(PF|PJ)$")


@router.get("/settings")
def get_settings(ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)):
    ctx.require(P_FIN_READ)
    return _out(load(db, ctx.organization_id))


@router.put("/settings")
def put_settings(
    body: SettingsIn, ctx: TenantContext = Depends(get_tenant), db: Session = Depends(get_db)
):
    """Só o proprietário muda: o padrão afeta os números de todos os membros da organização."""
    ctx.require(P_ORG_UPDATE)
    row = db.scalar(select(TaxSetting).where(TaxSetting.organization_id == ctx.organization_id))
    if row is None:
        row = TaxSetting(organization_id=ctx.organization_id)
        db.add(row)
    row.enabled, row.pf_bps, row.pj_bps = body.enabled, body.pf_bps, body.pj_bps
    row.clinic_regime = body.clinic_regime
    audit(db, action="tax.settings", object_type="organization", object_id=ctx.organization_id,
          organization_id=ctx.organization_id, actor_user_id=ctx.user.id,
          metadata={"enabled": body.enabled})  # fmt: skip
    db.commit()
    return _out(load(db, ctx.organization_id))


@router.get("/summary")
def summary(
    month: str = Query(pattern=MONTH_RE),
    ctx: TenantContext = Depends(get_tenant),
    db: Session = Depends(get_db),
):
    """Retenção estimada de cada renda planejada no mês e de onde veio a alíquota."""
    ctx.require(P_GOAL_READ)
    m = date(int(month[:4]), int(month[5:]), 1)
    nums = month_numbers(db, ctx, m, "PERSONAL_NET")
    rows = [
        {
            "name": r["name"], "kind": r["kind"], "regime": r.get("regime"),
            "tax_rate_bps": r.get("tax_rate_bps", 0), "tax_source": r.get("tax_source", "NENHUMA"),
            "units": r["units"], "gross_cents": r["gross_cents"], "tax_cents": r["tax_cents"],
            "net_cents": r["net_cents"],
        }
        for r in breakdown(nums)
    ]  # fmt: skip
    d = load(db, ctx.organization_id)
    return {
        "month": month, "enabled": d.enabled, "rows": rows,
        "total_gross_cents": sum(r["gross_cents"] for r in rows),
        "total_tax_cents": sum(r["tax_cents"] for r in rows),
        "uses_default": any(r["tax_source"] == "PADRAO" for r in rows),
        "tax_is_estimate": True, "notes": NOTES,
    }  # fmt: skip
