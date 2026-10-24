"""Alíquota efetiva: a informada pelo usuário sempre vence; o padrão do regime só vale se habilitado.

Todos os valores são ESTIMATIVAS em pontos-base (1500 = 15%), nunca tributo apurado (PRD 7.5).
"""

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import TaxSetting

DEFAULT_PF_BPS, DEFAULT_PJ_BPS = 2_700, 1_500  # médias de referência sugeridas
INFORMED, DEFAULT, NONE = "INFORMADA", "PADRAO", "NENHUMA"


@dataclass(frozen=True)
class TaxDefaults:
    enabled: bool = False
    pf_bps: int = DEFAULT_PF_BPS
    pj_bps: int = DEFAULT_PJ_BPS
    clinic_regime: str = "PJ"

    def for_regime(self, regime: str | None) -> int | None:
        """Padrão do regime; None quando desabilitado ou sem padrão (CLT/OUTRO já têm retenção própria)."""
        if not self.enabled:
            return None
        return {"PF": self.pf_bps, "PJ": self.pj_bps}.get(regime or "")


def load(db: Session, org_id: uuid.UUID) -> TaxDefaults:
    row = db.scalar(select(TaxSetting).where(TaxSetting.organization_id == org_id))
    if row is None:
        return TaxDefaults()
    return TaxDefaults(row.enabled, row.pf_bps, row.pj_bps, row.clinic_regime)


def resolve(d: TaxDefaults, explicit: int | None, regime: str | None) -> tuple[int, str]:
    """(alíquota em bps, origem). `explicit` é a do plano do mês ou, na falta, a da renda."""
    if explicit is not None:
        return explicit, INFORMED
    default = d.for_regime(regime)
    if default is not None:
        return default, DEFAULT
    return 0, NONE
