from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from stage0_sim import __version__
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.fake_llm import router as fake_llm_router
from stage0_sim.api.simulation import router as simulation_router
from stage0_sim.application.manager import SimulationManager
from stage0_sim.config import get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    store = SQLiteDatasetStore(
        settings.data_directory / settings.dataset_database
    )
    manager = SimulationManager(dataset_store=store)
    app.state.simulation_manager = manager
    try:
        yield
    finally:
        await manager.close()


app = FastAPI(
    title="Stage 0 Simulation API",
    version=__version__,
    lifespan=lifespan,
)

settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(fake_llm_router)
app.include_router(simulation_router)
web_directory = Path(__file__).parents[1] / "web"
app.mount("/ui", StaticFiles(directory=web_directory, html=True), name="ui")


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
