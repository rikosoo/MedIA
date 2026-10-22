"""Agenda e capacidade (PRD 6.6, 8.2, 8.6): intervalos, conflitos, descanso e slots reais.

Tudo com datetimes *com fuso* e minutos inteiros. Sem I/O.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

ACTIVE_TYPES = frozenset(
    {"CONSULT_NEW", "CONSULT_RETURN", "SHIFT", "EMPLOYMENT", "TELEMEDICINE", "PROCEDURE"}
)
CONSULT_TYPES = frozenset({"CONSULT_NEW", "CONSULT_RETURN"})
# Tempo "comprometido" = ativo + sobreaviso + deslocamento (PRD 7.1), sem dupla contagem.
COMMITTED_TYPES = ACTIVE_TYPES | {"ON_CALL", "TRAVEL"}
EVENT_TYPES = ACTIVE_TYPES | {"ON_CALL", "TRAVEL", "BREAK", "BLOCK"}


@dataclass(frozen=True)
class Interval:
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise ValueError("intervalos exigem datetimes com fuso")
        if self.end < self.start:
            raise ValueError("fim anterior ao início")

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


@dataclass(frozen=True)
class Event:
    id: str
    type: str
    start: datetime
    end: datetime
    allow_overlap: bool = False  # só vale para ON_CALL
    recovery_minutes: int = 0  # descanso exigido após o fim

    @property
    def interval(self) -> Interval:
        return Interval(self.start, self.end)


@dataclass(frozen=True)
class Conflict:
    a_id: str
    b_id: str
    kind: str  # HARD | CONDITIONAL
    overlap_minutes: int


@dataclass(frozen=True)
class RestViolation:
    after_id: str
    next_id: str
    required_minutes: int
    gap_minutes: int


def merge(intervals: list[Interval]) -> list[Interval]:
    out: list[Interval] = []
    for iv in sorted(intervals, key=lambda i: (i.start, i.end)):
        if iv.end <= iv.start:
            continue
        if out and iv.start <= out[-1].end:
            if iv.end > out[-1].end:
                out[-1] = Interval(out[-1].start, iv.end)
        else:
            out.append(iv)
    return out


def subtract(base: list[Interval], blockers: list[Interval]) -> list[Interval]:
    """`base` menos `blockers`. Resultado ordenado e sem sobreposição."""
    free = merge(base)
    for b in merge(blockers):
        nxt: list[Interval] = []
        for f in free:
            if b.end <= f.start or b.start >= f.end:
                nxt.append(f)
                continue
            if b.start > f.start:
                nxt.append(Interval(f.start, b.start))
            if b.end < f.end:
                nxt.append(Interval(b.end, f.end))
        free = nxt
    return free


def clip(intervals: list[Interval], window: Interval) -> list[Interval]:
    out = []
    for iv in intervals:
        s, e = max(iv.start, window.start), min(iv.end, window.end)
        if e > s:
            out.append(Interval(s, e))
    return out


def total_minutes(intervals: list[Interval]) -> int:
    return sum(i.minutes for i in merge(intervals))


def count_slots(intervals: list[Interval], slot_minutes: int) -> int:
    """Quantos horários inteiros de `slot_minutes` cabem, sem atravessar lacunas."""
    if slot_minutes <= 0:
        raise ValueError("slot deve ser positivo")
    return sum(i.minutes // slot_minutes for i in merge(intervals))


def local_day(d: date, tz: ZoneInfo) -> Interval:
    start = datetime.combine(d, time.min, tzinfo=tz)
    return Interval(start, datetime.combine(d + timedelta(days=1), time.min, tzinfo=tz))


def local_month(month: date, tz: ZoneInfo) -> Interval:
    nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    return Interval(
        datetime.combine(month, time.min, tzinfo=tz), datetime.combine(nxt, time.min, tzinfo=tz)
    )


def find_conflicts(events: list[Event]) -> list[Conflict]:
    """Sobreposição é conflito duro, exceto sobreaviso com `allow_overlap` (condicional ao
    acionamento, PRD 6.6). Dois sobreavisos, ou qualquer outro par, nunca coexistem."""
    evs = sorted(events, key=lambda e: (e.start, e.end, e.id))
    out: list[Conflict] = []
    for i, a in enumerate(evs):
        for b in evs[i + 1 :]:
            if b.start >= a.end:
                break
            overlap = int((min(a.end, b.end) - b.start).total_seconds() // 60)
            if overlap <= 0:
                continue
            pair = {a.type, b.type}
            on_call = next((e for e in (a, b) if e.type == "ON_CALL"), None)
            conditional = (
                on_call is not None
                and on_call.allow_overlap
                and pair != {"ON_CALL"}
                and not ({"BLOCK"} & pair)
            )
            out.append(Conflict(a.id, b.id, "CONDITIONAL" if conditional else "HARD", overlap))
    return out


def rest_violations(events: list[Event]) -> list[RestViolation]:
    """Evento com `recovery_minutes` > 0 exige esse intervalo antes da próxima atividade."""
    active = sorted((e for e in events if e.type in ACTIVE_TYPES), key=lambda e: (e.start, e.id))
    out: list[RestViolation] = []
    for a in active:
        if a.recovery_minutes <= 0:
            continue
        limit = a.end + timedelta(minutes=a.recovery_minutes)
        for b in active:
            if b.id != a.id and a.end <= b.start < limit:
                gap = int((b.start - a.end).total_seconds() // 60)
                out.append(RestViolation(a.id, b.id, a.recovery_minutes, gap))
    return out


def is_night(ev: Event, tz: ZoneInfo, start_hour: int = 22, end_hour: int = 5) -> bool:
    """Evento que toca a janela noturna local (22h–5h por padrão)."""
    day = ev.start.astimezone(tz).date() - timedelta(days=1)
    last = ev.end.astimezone(tz).date()
    while day <= last:
        night = Interval(
            datetime.combine(day, time(start_hour), tzinfo=tz),
            datetime.combine(day + timedelta(days=1), time(end_hour), tzinfo=tz),
        )
        if ev.start < night.end and ev.end > night.start:
            return True
        day += timedelta(days=1)
    return False


def easter(year: int) -> date:
    """Domingo de Páscoa (algoritmo anônimo gregoriano)."""
    a, b, c = year % 19, year // 100, year % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    ell = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * ell) // 451
    month = (h + ell - 7 * m + 114) // 31
    day = (h + ell - 7 * m + 114) % 31 + 1
    return date(year, month, day)


def national_holidays(year: int) -> list[tuple[date, str]]:
    """Feriados nacionais por lei. Pontos facultativos (Carnaval, Corpus Christi) ficam de fora.

    Estaduais e municipais dependem da localidade e devem ser cadastrados/confirmados à mão.
    """
    fixed = [
        (1, 1, "Confraternização Universal"),
        (4, 21, "Tiradentes"),
        (5, 1, "Dia do Trabalho"),
        (9, 7, "Independência do Brasil"),
        (10, 12, "Nossa Senhora Aparecida"),
        (11, 2, "Finados"),
        (11, 15, "Proclamação da República"),
        (12, 25, "Natal"),
    ]
    out = [(date(year, m, d), name) for m, d, name in fixed]
    if year >= 2024:
        out.append((date(year, 11, 20), "Consciência Negra"))
    out.append((easter(year) - timedelta(days=2), "Sexta-feira Santa"))
    return sorted(out)
