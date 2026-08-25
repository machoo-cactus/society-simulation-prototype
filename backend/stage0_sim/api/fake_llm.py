from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from stage0_sim.adapters.llm import (
    FakeDialogueGenerator,
    FakeEmbeddingProvider,
    FakePlanner,
)
from stage0_sim.application.cognition import (
    DialogueContext,
    LocationContext,
    PlannerContext,
    StationContext,
    VitalContext,
    ZoneContext,
)

router = APIRouter(prefix="/fake-llm/v1", tags=["fake-llm"])
planner = FakePlanner()
dialogue_generator = FakeDialogueGenerator()
embedding_provider = FakeEmbeddingProvider()


class VitalPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    satiety: float = Field(ge=0, le=100)
    energy: float = Field(ge=0, le=100)
    stress: float = Field(ge=0, le=100)


class LocationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: int
    y: int
    zone_id: str | None = None


class ZonePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    zone_type: str


class StationPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    x: int
    y: int
    actions: list[str]
    available: bool = True


class PlanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    simulation_time: float
    vitals: VitalPayload
    location: LocationPayload
    zones: list[ZonePayload] = Field(default_factory=list)
    stations: list[StationPayload] = Field(default_factory=list)
    daily_goals: list[str] = Field(default_factory=list)
    memories: list[str] = Field(default_factory=list)


class ActionResponse(BaseModel):
    action: str
    target: str | None = None
    duration: float | None = None


class PlanResponse(BaseModel):
    actions: list[ActionResponse]
    rationale: str
    provider: str = "fake"


class DialogueRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    simulation_time: float
    prompt: str
    memories: list[str] = Field(default_factory=list)


class DialogueResponse(BaseModel):
    text: str
    provider: str = "fake"


class EmbeddingRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    texts: list[str]


class EmbeddingResponse(BaseModel):
    embeddings: list[list[float]]
    dimensions: int
    provider: str = "fake"


@router.post("/plan", response_model=PlanResponse, response_model_exclude_none=True)
async def create_plan(request: PlanRequest) -> PlanResponse:
    result = planner.plan(
        PlannerContext(
            agent_id=request.agent_id,
            simulation_time=request.simulation_time,
            vitals=VitalContext(**request.vitals.model_dump()),
            location=LocationContext(**request.location.model_dump()),
            zones=tuple(
                ZoneContext(**zone.model_dump()) for zone in request.zones
            ),
            stations=tuple(
                StationContext(
                    **station.model_dump(exclude={"actions"}),
                    actions=tuple(station.actions),
                )
                for station in request.stations
            ),
            daily_goals=tuple(request.daily_goals),
            memories=tuple(request.memories),
        )
    )
    return PlanResponse(
        actions=[
            ActionResponse(
                action=action.action.value,
                target=action.target,
                duration=action.duration,
            )
            for action in result.actions
        ],
        rationale=result.rationale,
    )


@router.post("/dialogue", response_model=DialogueResponse)
async def create_dialogue(request: DialogueRequest) -> DialogueResponse:
    result = dialogue_generator.generate(
        DialogueContext(
            agent_id=request.agent_id,
            simulation_time=request.simulation_time,
            prompt=request.prompt,
            memories=tuple(request.memories),
        )
    )
    return DialogueResponse(text=result.text)


@router.post("/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest) -> EmbeddingResponse:
    embeddings = embedding_provider.embed(tuple(request.texts))
    return EmbeddingResponse(
        embeddings=[list(embedding) for embedding in embeddings],
        dimensions=embedding_provider.dimensions,
    )
