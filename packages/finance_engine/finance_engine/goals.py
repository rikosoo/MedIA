def goal_gap_cents(target_net_cents: int, projected_net_cents: int) -> int:
    """lacuna = max(0, meta_liquida − liquido_projetado_base) (PRD 7.4)."""
    if target_net_cents < 0:
        raise ValueError("meta não pode ser negativa")
    return max(0, target_net_cents - projected_net_cents)
