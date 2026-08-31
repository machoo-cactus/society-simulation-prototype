from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles

from stage0_sim import __version__
from stage0_sim.adapters.characters import FileSystemCharacterLibrary
from stage0_sim.adapters.persistence import SQLiteDatasetStore
from stage0_sim.api.characters import router as characters_router
from stage0_sim.api.simulation import router as simulation_router
from stage0_sim.api.ui import OperatorSessionStore
from stage0_sim.api.ui import router as ui_router
from stage0_sim.application.manager import SimulationManager
from stage0_sim.config import create_model_client, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    store = SQLiteDatasetStore(
        settings.data_directory / settings.dataset_database
    )
    character_library = FileSystemCharacterLibrary(
        settings.character_directory
    )
    manager = SimulationManager(
        dataset_store=store,
        character_library=character_library,
        model_client=create_model_client(settings),
        model_max_output_tokens=settings.llm_max_output_tokens,
        model_max_concurrency=settings.llm_max_concurrency,
    )
    app.state.simulation_manager = manager
    app.state.character_library = character_library
    app.state.operator_sessions = OperatorSessionStore()
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
app.include_router(simulation_router)
app.include_router(characters_router)
app.include_router(ui_router)
web_directory = Path(__file__).parents[1] / "web"
app.mount(
    "/ui/assets",
    StaticFiles(directory=web_directory),
    name="ui-assets",
)


@app.get("/", include_in_schema=False)
async def index() -> RedirectResponse:
    return RedirectResponse(url="/ui/")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}
