from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.config import Settings, get_settings
from app.database import Base, build_engine, build_session_factory
from app.services.processor import JobProcessor
from app.services.renderer import CertificateRenderer


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    engine = build_engine(settings.database_url)
    session_factory = build_session_factory(engine)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        settings.storage_dir.mkdir(parents=True, exist_ok=True)
        if settings.database_url.startswith("sqlite"):
            database_path = settings.database_url.removeprefix("sqlite:///")
            if database_path != ":memory:":
                from pathlib import Path

                Path(database_path).parent.mkdir(parents=True, exist_ok=True)
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="CertiFlow API",
        version="1.0.0",
        description="Resilient bulk PDF certificate generation with per-recipient outcomes.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.processor = JobProcessor(
        session_factory=session_factory,
        renderer=CertificateRenderer(settings.public_base_url),
        storage_dir=settings.storage_dir,
        workers=settings.worker_threads,
    )
    app.include_router(router)

    @app.get("/health", tags=["system"])
    def health():
        return {"status": "ok"}

    return app


app = create_app()

