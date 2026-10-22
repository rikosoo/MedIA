"""Matriz papel -> permissões (PRD 2.1). Aplicada no backend; a UI apenas reflete."""

from app.db.models import Role

P_ORG_UPDATE = "org:update"
P_TEAM_MANAGE = "team:manage"
P_TEAM_READ = "team:read"
P_WORK_READ_OWN = "work:read_own"
P_WORK_READ_ALL = "work:read_all"
P_WORK_WRITE = "work:write"
P_CLINIC_READ = "clinic:read"
P_CLINIC_WRITE = "clinic:write"
P_FIN_READ = "finance:read"
P_FIN_WRITE = "finance:write"
P_GOAL_READ = "goal:read"
P_GOAL_WRITE = "goal:write"
P_BILLING = "billing:manage"  # só o dono
P_ACQ_READ = "acquisition:read"
P_ACQ_WRITE = "acquisition:write"
P_SCHED_READ = "schedule:read"
P_SCHED_WRITE = "schedule:write"
P_SCHED_ALL = "schedule:all"  # ver/editar a agenda de outros membros

_CLINIC_RW = {P_CLINIC_READ, P_CLINIC_WRITE, P_FIN_READ, P_FIN_WRITE}
_GOALS = {P_GOAL_READ, P_GOAL_WRITE}  # metas e rendas pessoais: dono e médico, mais ninguém
_ACQ = {P_ACQ_READ, P_ACQ_WRITE}  # funil: inclui a secretária (só agregados)
_SCHED_OWN = {P_SCHED_READ, P_SCHED_WRITE}
_SCHED_ALL = _SCHED_OWN | {P_SCHED_ALL}

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.OWNER: frozenset(
        {
            P_ORG_UPDATE,
            P_BILLING,
            P_TEAM_MANAGE,
            P_TEAM_READ,
            P_WORK_READ_OWN,
            P_WORK_READ_ALL,
            P_WORK_WRITE,
        }
        | _CLINIC_RW
        | _SCHED_ALL
        | _GOALS
        | _ACQ
    ),
    Role.PHYSICIAN: frozenset(
        {P_TEAM_READ, P_WORK_READ_OWN, P_WORK_WRITE} | _CLINIC_RW | _SCHED_OWN | _GOALS | _ACQ
    ),
    Role.MANAGER: frozenset(
        {P_TEAM_READ, P_WORK_READ_OWN, P_WORK_READ_ALL, P_WORK_WRITE}
        | _CLINIC_RW
        | _SCHED_ALL
        | _ACQ
    ),
    # Secretária: sem rendimentos pessoais por padrão (US-012).
    # Agenda sim (opera a agenda do médico); finanças e rendas não.
    Role.ASSISTANT: frozenset(_SCHED_ALL | _ACQ),
    # Contador: somente leitura de dados aprovados.
    Role.ACCOUNTANT: frozenset({P_WORK_READ_OWN, P_WORK_READ_ALL, P_CLINIC_READ, P_FIN_READ}),
    Role.PLATFORM_SUPPORT: frozenset(),
}


def permissions_for(role: str, extra: dict | None = None) -> frozenset[str]:
    """Permissões do papel + concessões granulares explícitas (`{"grant": [...]}`)."""
    base = ROLE_PERMISSIONS.get(role, frozenset())
    grants = frozenset((extra or {}).get("grant", []))
    return base | grants
