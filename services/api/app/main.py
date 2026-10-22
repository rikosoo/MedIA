from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.middleware import install as install_guard
from app.db.session import get_db
from app.modules.account.router import router as account_router
from app.modules.acquisition.router import router as acquisition_router
from app.modules.alerts.router import router as alerts_router
from app.modules.assistant.router import router as assistant_router
from app.modules.auth.router import router as auth_router
from app.modules.billing.router import router as billing_router
from app.modules.billing.router import webhook_router as billing_webhook_router
from app.modules.clinic.router import router as clinic_router
from app.modules.finances.router import router as finances_router
from app.modules.forecasts.router import router as forecasts_router
from app.modules.goals.router import router as goals_router
from app.modules.imports.router import router as imports_router
from app.modules.optimizer.router import router as optimizer_router
from app.modules.organizations.router import router as org_router
from app.modules.realized.router import router as realized_router
from app.modules.reports.router import public as reports_public_router
from app.modules.reports.router import router as reports_router
from app.modules.schedule.router import router as schedule_router
from app.modules.team.router import router as team_router
from app.modules.work.router import router as work_router


def create_app() -> FastAPI:
    settings = get_settings()
    dev = settings.environment == "dev"  # documentação interativa só em desenvolvimento
    app = FastAPI(
        title="FinanceMed AI API", version="0.1.0",
        docs_url="/docs" if dev else None, redoc_url="/redoc" if dev else None,
        openapi_url="/openapi.json" if dev else None,
    )  # fmt: skip
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Organization-Id", "Idempotency-Key"],
    )

    install_guard(app)

    @app.get("/health", tags=["health"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["health"])
    def ready(db: Session = Depends(get_db)):
        """Pronto para tráfego: confere o banco (usado pelo monitoramento/balanceador)."""
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    for r in (
        auth_router,
        account_router,
        org_router,
        team_router,
        work_router,
        clinic_router,
        finances_router,
        schedule_router,
        goals_router,
        optimizer_router,
        imports_router,
        realized_router,
        reports_router,
        reports_public_router,
        alerts_router,
        acquisition_router,
        forecasts_router,
        assistant_router,
        billing_router,
        billing_webhook_router,
    ):
        app.include_router(r)
    return app


app = create_app()
