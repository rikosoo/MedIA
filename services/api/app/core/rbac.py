"""Matriz papel -> permissões (PRD 2.1). Aplicada no backend; a UI apenas reflete."""

from app.db.models import Role

P_ORG_UPDATE = "org:update"
P_TEAM_MANAGE = "team:manage"
P_TEAM_READ = "team:read"
P_WORK_READ_OWN = "work:read_own"
P_WORK_READ_ALL = "work:read_all"
P_WORK_WRITE = "work:write"

ROLE_PERMISSIONS: dict[str, frozenset[str]] = {
    Role.OWNER: frozenset(
        {P_ORG_UPDATE, P_TEAM_MANAGE, P_TEAM_READ, P_WORK_READ_OWN, P_WORK_READ_ALL, P_WORK_WRITE}
    ),
    Role.PHYSICIAN: frozenset({P_TEAM_READ, P_WORK_READ_OWN, P_WORK_WRITE}),
    Role.MANAGER: frozenset({P_TEAM_READ, P_WORK_READ_OWN, P_WORK_READ_ALL, P_WORK_WRITE}),
    # Secretária: sem rendimentos pessoais por padrão (US-012).
    Role.ASSISTANT: frozenset(),
    # Contador: somente leitura de dados aprovados.
    Role.ACCOUNTANT: frozenset({P_WORK_READ_OWN, P_WORK_READ_ALL}),
    Role.PLATFORM_SUPPORT: frozenset(),
}


def permissions_for(role: str, extra: dict | None = None) -> frozenset[str]:
    """Permissões do papel + concessões granulares explícitas (`{"grant": [...]}`)."""
    base = ROLE_PERMISSIONS.get(role, frozenset())
    grants = frozenset((extra or {}).get("grant", []))
    return base | grants
