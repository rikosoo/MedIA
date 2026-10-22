"""Modelo de dados do otimizador (PRD 8.1). Dinheiro em centavos; tempo em minutos inteiros.

Atividades `BLOCK` (plantão, sobreaviso, CLT extra...) só existem em *candidatos* com data e hora
reais; atividades `CONSULT` são quantidades limitadas por demanda e pelos minutos livres da agenda.
"""

from dataclasses import dataclass, field

ENGINE_VERSION = "1.0.0"

OBJECTIVES = ("LESS_HOURS", "MAX_MARGIN", "LEAST_CHANGE", "BALANCED", "CONSERVATIVE")

# Famílias de restrição que podem ser relaxadas uma a uma para explicar a inviabilidade.
ACTIVE_LIMIT = "ACTIVE_LIMIT"
COMMITTED_LIMIT = "COMMITTED_LIMIT"
MAX_UNITS = "MAX_UNITS"
DEMAND = "DEMAND"
CONSULT_CAPACITY = "CONSULT_CAPACITY"
CALENDAR = "CALENDAR"
BUDGET = "BUDGET"
NIGHT_LIMIT = "NIGHT_LIMIT"
MAX_EVENTS = "MAX_EVENTS"
CONSTRAINT_FAMILIES = (
    ACTIVE_LIMIT, COMMITTED_LIMIT, MAX_UNITS, DEMAND, CONSULT_CAPACITY, CALENDAR, BUDGET,
    NIGHT_LIMIT, MAX_EVENTS,
)  # fmt: skip


@dataclass(frozen=True)
class Activity:
    key: str
    kind: str  # BLOCK | CONSULT
    label: str
    net_gain_cents: int  # por unidade, já líquido de tributo estimado, custo e captação
    net_gain_pessimistic_cents: int
    active_minutes: int = 0  # BLOCK
    committed_minutes: int = 0  # BLOCK
    max_units: int = 0
    demand_limit: int | None = None  # CONSULT; None = demanda não informada
    demand_limit_pessimistic: int | None = None
    slots_limit: int | None = None  # CONSULT: horários inteiros existentes na agenda
    consult_minutes_x10: int = 0  # CONSULT: 552 = 55,2 min por paciente novo (com retornos)
    acquisition_cost_cents: int = 0  # entra no orçamento de captação

    def __post_init__(self):
        if self.kind not in ("BLOCK", "CONSULT"):
            raise ValueError("tipo de atividade inválido")
        if self.max_units < 0:
            raise ValueError("max_units não pode ser negativo")


@dataclass(frozen=True)
class Candidate:
    """Uma ocupação possível de um bloco: [start, end) ativo, [end, occ_end) é descanso."""

    id: str
    activity_key: str
    start: int  # minutos desde a época (UTC)
    end: int
    occ_end: int
    day: str  # dia local de início (custo de mudança de rotina)
    is_night: bool = False
    consult_minutes_lost: int = 0  # minutos de consulta que esta ocupação elimina

    def __post_init__(self):
        if not (self.start < self.end <= self.occ_end):
            raise ValueError("intervalo de candidato inválido")


@dataclass(frozen=True)
class Problem:
    gap_cents: int
    activities: tuple[Activity, ...]
    candidates: tuple[Candidate, ...] = ()
    max_active_minutes: int | None = None
    max_committed_minutes: int | None = None
    consult_free_minutes: int = 0
    max_night_shifts: int | None = None
    marketing_budget_cents: int | None = None
    max_block_events: int | None = None
    # pesos do cenário "Equilíbrio" (somam 100): horas, mudança de rotina, risco, folga financeira
    weights: dict = field(
        default_factory=lambda: {"hours": 40, "change": 20, "risk": 20, "margin": 20}
    )

    def __post_init__(self):
        if self.gap_cents < 0:
            raise ValueError("lacuna não pode ser negativa")
        keys = [a.key for a in self.activities]
        if len(set(keys)) != len(keys):
            raise ValueError("chaves de atividade duplicadas")
        known = {a.key: a for a in self.activities}
        for c in self.candidates:
            if c.activity_key not in known or known[c.activity_key].kind != "BLOCK":
                raise ValueError(f"candidato {c.id} aponta para atividade inválida")
        if sum(self.weights.values()) != 100:
            raise ValueError("pesos do equilíbrio devem somar 100")

    def activity(self, key: str) -> Activity:
        return next(a for a in self.activities if a.key == key)


@dataclass(frozen=True)
class Solution:
    units: dict  # key -> quantidade (BLOCK: nº de candidatos escolhidos)
    placements: tuple  # ids de candidatos escolhidos, ordenados
    gain_cents: int
    gain_pessimistic_cents: int
    active_minutes: int
    committed_minutes: int
    days_used: int
    block_events: int
    consult_units: int
    risk_cents: int
    optimal: bool = True
