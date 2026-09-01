from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from stage0_sim.domain.events import JsonValue


class ExtensibleCharacterProfileModel(BaseModel):
    model_config = ConfigDict(extra="allow")

    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)


class CharacterIdentityDefinition(ExtensibleCharacterProfileModel):

    display_name: str = Field(min_length=1)
    age: int | None = Field(default=None, ge=0, le=150)
    birth_date: date | None = None
    gender: str = ""
    pronouns: str = ""
    occupation: str = ""


class CharacterBodyMeasurementsDefinition(ExtensibleCharacterProfileModel):

    measured_on: date | None = None
    height_cm: float | None = Field(default=None, gt=0, le=300)
    weight_kg: float | None = Field(default=None, gt=0, le=700)
    chest_cm: float | None = Field(default=None, gt=0, le=300)
    waist_cm: float | None = Field(default=None, gt=0, le=300)
    hips_cm: float | None = Field(default=None, gt=0, le=300)
    inseam_cm: float | None = Field(default=None, gt=0, le=200)
    shoe_size_system: str = ""
    shoe_size_value: float | None = Field(default=None, gt=0, le=100)


class CharacterAppearanceDefinition(ExtensibleCharacterProfileModel):

    summary: str = ""
    height: str = ""
    build: str = ""
    hair: str = ""
    eyes: str = ""
    clothing: str = ""
    distinguishing_features: list[str] = Field(default_factory=list)


class CharacterPersonalityDefinition(ExtensibleCharacterProfileModel):

    summary: str = ""
    traits: list[str] = Field(default_factory=list)
    temperament: str = ""
    social_style: str = ""
    speech_style: str = ""
    strengths: list[str] = Field(default_factory=list)
    flaws: list[str] = Field(default_factory=list)


class CharacterBackgroundDefinition(ExtensibleCharacterProfileModel):

    birthplace: str = ""
    residence: str = ""
    education: str = ""
    history: str = ""


class CharacterFinancialSituationDefinition(ExtensibleCharacterProfileModel):

    as_of_date: date | None = None
    currency: str = ""
    annual_gross_income: int | None = Field(default=None, ge=0)
    income_band: str = ""
    liquid_assets: int | None = Field(default=None, ge=0)
    total_assets: int | None = Field(default=None, ge=0)
    total_debt: int | None = Field(default=None, ge=0)
    monthly_fixed_expenses: int | None = Field(default=None, ge=0)
    housing_tenure: str = ""
    financial_dependents: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def currency_is_iso_style(self) -> "CharacterFinancialSituationDefinition":
        if self.currency and (
            len(self.currency) != 3 or not self.currency.isalpha()
        ):
            raise ValueError("financial currency must be a three-letter code")
        self.currency = self.currency.upper()
        return self


class CharacterMotivationsDefinition(ExtensibleCharacterProfileModel):

    values: list[str] = Field(default_factory=list)
    fears: list[str] = Field(default_factory=list)
    needs: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def rejects_situational_fields(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = {"goals", "current_priorities"} & set(value)
            if forbidden:
                raise ValueError(
                    "character motivations cannot contain scenario-owned fields: "
                    f"{sorted(forbidden)}"
                )
        return value


class CharacterCapabilitiesDefinition(ExtensibleCharacterProfileModel):

    skills: list[str] = Field(default_factory=list)
    knowledge_areas: list[str] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class CharacterPreferencesDefinition(ExtensibleCharacterProfileModel):

    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    routines: list[str] = Field(default_factory=list)


class CharacterPresentationDefinition(ExtensibleCharacterProfileModel):

    aesthetic_identity: str = ""
    wardrobe_palette: list[str] = Field(default_factory=list)
    preferred_silhouettes: list[str] = Field(default_factory=list)
    preferred_fabrics: list[str] = Field(default_factory=list)
    formality_range: str = ""
    comfort_priorities: list[str] = Field(default_factory=list)
    grooming_norms: list[str] = Field(default_factory=list)
    usual_accessories: list[str] = Field(default_factory=list)
    practical_constraints: list[str] = Field(default_factory=list)
    purchase_habits: list[str] = Field(default_factory=list)
    context_variations: list[str] = Field(default_factory=list)


class CharacterDispositionsDefinition(ExtensibleCharacterProfileModel):

    summary: str = ""
    emotional_baseline: str = ""
    sociability: str = ""
    assertiveness: str = ""
    patience: str = ""
    conscientiousness: str = ""
    openness: str = ""
    adaptability: str = ""
    risk_tolerance: str = ""
    ambiguity_tolerance: str = ""
    impulse_control: str = ""
    conflict_style: str = ""
    cooperation_style: str = ""
    trust_formation: str = ""
    boundary_setting: str = ""
    help_seeking: str = ""
    pressure_response: str = ""
    fatigue_response: str = ""
    novelty_response: str = ""
    authority_response: str = ""
    crowd_response: str = ""


class CharacterCommunicationDefinition(ExtensibleCharacterProfileModel):

    cadence: str = ""
    vocabulary: str = ""
    directness: str = ""
    politeness: str = ""
    humor: str = ""
    gesture: str = ""
    posture: str = ""
    facial_expressiveness: str = ""
    listening_style: str = ""
    disagreement_style: str = ""
    apology_style: str = ""
    with_intimates: str = ""
    with_colleagues: str = ""
    with_strangers: str = ""
    with_authority: str = ""


class CharacterDecisionCopingDefinition(ExtensibleCharacterProfileModel):

    information_seeking: str = ""
    planning_horizon: str = ""
    default_heuristics: list[str] = Field(default_factory=list)
    error_sensitivity: str = ""
    persistence: str = ""
    recovery_habits: list[str] = Field(default_factory=list)
    self_soothing: list[str] = Field(default_factory=list)
    stress_signals: list[str] = Field(default_factory=list)
    disposition_shifts: list[str] = Field(default_factory=list)


class CharacterLifeStructureDefinition(ExtensibleCharacterProfileModel):

    household: str = ""
    recurring_obligations: list[str] = Field(default_factory=list)
    material_habits: list[str] = Field(default_factory=list)
    typical_possessions: list[str] = Field(default_factory=list)
    cultural_practices: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)
    social_patterns: list[str] = Field(default_factory=list)


class CharacterFamilyMemberDefinition(ExtensibleCharacterProfileModel):

    member_id: str = Field(min_length=1)
    linked_character_id: str = ""
    display_name: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    birth_date: date | None = None
    living_status: Literal["alive", "deceased", "unknown"] = "unknown"
    residence: str = ""
    household_member: bool = False
    financial_dependent: bool = False


class CharacterFamilyDefinition(ExtensibleCharacterProfileModel):

    members: list[CharacterFamilyMemberDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def member_ids_are_unique(self) -> "CharacterFamilyDefinition":
        member_ids = [member.member_id for member in self.members]
        if len(member_ids) != len(set(member_ids)):
            raise ValueError("family member IDs must be unique")
        return self


class CharacterHealthConditionDefinition(ExtensibleCharacterProfileModel):

    name: str = Field(min_length=1)
    status: Literal["active", "managed", "resolved", "unknown"] = "unknown"
    diagnosed_on: date | None = None
    notes: str = ""


class CharacterHealthAllergyDefinition(ExtensibleCharacterProfileModel):

    substance: str = Field(min_length=1)
    reaction: str = ""
    severity: Literal["mild", "moderate", "severe", "unknown"] = "unknown"


class CharacterMedicationDefinition(ExtensibleCharacterProfileModel):

    name: str = Field(min_length=1)
    dose: str = ""
    schedule: str = ""
    purpose: str = ""


class CharacterHealthDefinition(ExtensibleCharacterProfileModel):

    as_of_date: date | None = None
    blood_type: str = ""
    conditions: list[CharacterHealthConditionDefinition] = Field(
        default_factory=list
    )
    allergies: list[CharacterHealthAllergyDefinition] = Field(
        default_factory=list
    )
    medications: list[CharacterMedicationDefinition] = Field(
        default_factory=list
    )
    disabilities: list[str] = Field(default_factory=list)
    vision: str = ""
    hearing: str = ""
    mobility: str = ""
    past_procedures: list[str] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)


class CharacterRelationshipDefinition(ExtensibleCharacterProfileModel):

    target_id: str = Field(min_length=1)
    relationship: str = Field(min_length=1)
    sentiment: str = ""
    notes: str = ""


class CharacterCustomFieldDefinition(ExtensibleCharacterProfileModel):

    key: str = Field(min_length=1)
    label: str = Field(min_length=1)
    value: JsonValue
    prompt_visible: bool = True
    ui_visible: bool = True


class CharacterCustomSectionDefinition(ExtensibleCharacterProfileModel):

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    prompt_visible: bool = True
    ui_visible: bool = True
    fields: list[CharacterCustomFieldDefinition] = Field(default_factory=list)

    @model_validator(mode="after")
    def field_keys_are_unique(self) -> "CharacterCustomSectionDefinition":
        keys = [field.key for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError(f"custom section {self.id} field keys must be unique")
        return self


class CharacterProfileDefinition(ExtensibleCharacterProfileModel):

    template_id: str = "human-v1"
    identity: CharacterIdentityDefinition
    body_measurements: CharacterBodyMeasurementsDefinition = Field(
        default_factory=CharacterBodyMeasurementsDefinition
    )
    appearance: CharacterAppearanceDefinition = Field(
        default_factory=CharacterAppearanceDefinition
    )
    health: CharacterHealthDefinition = Field(
        default_factory=CharacterHealthDefinition
    )
    personality: CharacterPersonalityDefinition = Field(
        default_factory=CharacterPersonalityDefinition
    )
    background: CharacterBackgroundDefinition = Field(
        default_factory=CharacterBackgroundDefinition
    )
    financial_situation: CharacterFinancialSituationDefinition = Field(
        default_factory=CharacterFinancialSituationDefinition
    )
    motivations: CharacterMotivationsDefinition = Field(
        default_factory=CharacterMotivationsDefinition
    )
    capabilities: CharacterCapabilitiesDefinition = Field(
        default_factory=CharacterCapabilitiesDefinition
    )
    preferences: CharacterPreferencesDefinition = Field(
        default_factory=CharacterPreferencesDefinition
    )
    presentation: CharacterPresentationDefinition = Field(
        default_factory=CharacterPresentationDefinition
    )
    dispositions: CharacterDispositionsDefinition = Field(
        default_factory=CharacterDispositionsDefinition
    )
    communication: CharacterCommunicationDefinition = Field(
        default_factory=CharacterCommunicationDefinition
    )
    decision_coping: CharacterDecisionCopingDefinition = Field(
        default_factory=CharacterDecisionCopingDefinition
    )
    life_structure: CharacterLifeStructureDefinition = Field(
        default_factory=CharacterLifeStructureDefinition
    )
    family: CharacterFamilyDefinition = Field(
        default_factory=CharacterFamilyDefinition
    )
    relationships: list[CharacterRelationshipDefinition] = Field(
        default_factory=list
    )
    custom_sections: list[CharacterCustomSectionDefinition] = Field(
        default_factory=list
    )

    @model_validator(mode="before")
    @classmethod
    def rejects_legacy_profile_shape(cls, value: Any) -> Any:
        if isinstance(value, dict):
            forbidden = {
                "profile_ref",
                "display_name",
                "role",
                "traits",
                "values",
                "goals",
                "current_priorities",
            } & set(value)
            if forbidden:
                raise ValueError(
                    "character profile contains removed fields: "
                    f"{sorted(forbidden)}"
                )
        return value
    @model_validator(mode="after")
    def custom_sections_are_unique(self) -> "CharacterProfileDefinition":
        section_ids = [section.id for section in self.custom_sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("custom section IDs must be unique")
        return self
