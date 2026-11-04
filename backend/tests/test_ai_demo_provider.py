import pytest
from pydantic import BaseModel, ValidationError

from app.ai.client import AiClient
from app.ai.food import AiFoodEstimate
from app.ai.inbody import InbodyReading
from app.ai.providers.demo import DemoProvider
from app.ai.providers.factory import build_provider
from app.ai.types import ChatResult, Task, UserMessage
from app.core.config import Settings
from app.rules.food_validation import FoodItem, check_item
from app.rules.inbody_validation import ScanValues, validate_scan


def ask(schema: type[BaseModel]):
    return DemoProvider().structured(
        model="demo",
        system="",
        messages=[UserMessage("hi")],
        schema=schema,
        max_output_tokens=100,
        reasoning_effort=None,
        user_ref=None,
    )


def test_demo_needs_no_key_and_is_offline() -> None:
    settings = Settings(_env_file=None, ai_provider="demo")
    assert isinstance(build_provider(settings), DemoProvider)
    client = AiClient(DemoProvider(), settings, lambda: None)  # type: ignore[arg-type]
    assert client.model_for(Task.VISION) == "demo"


def test_demo_is_refused_outside_development() -> None:
    with pytest.raises(ValidationError, match="demo"):
        Settings(
            _env_file=None,
            ai_provider="demo",
            app_env="production",
            jwt_secret="x" * 40,
        )


def test_demo_food_estimate_passes_the_rules() -> None:
    raw = ask(AiFoodEstimate)
    assert isinstance(raw.parsed, AiFoodEstimate)
    assert raw.parsed.food_found and raw.parsed.items
    for i in raw.parsed.items:
        item = FoodItem(
            i.name, i.grams, i.kcal_min, i.kcal_max, i.protein_g, i.carbs_g, i.fat_g
        )
        assert check_item(item)[1] == ()


def test_demo_inbody_reading_passes_the_rules() -> None:
    raw = ask(InbodyReading)
    r = raw.parsed
    assert isinstance(r, InbodyReading) and r.test_date is not None
    values = ScanValues(
        weight_kg=r.weight_kg.value,
        smm_kg=r.skeletal_muscle_mass_kg.value,
        body_fat_mass_kg=r.body_fat_mass_kg.value,
        pbf_pct=r.percent_body_fat.value,
        bmi=r.bmi.value,
        tbw_l=r.total_body_water_l.value,
        bmr_kcal=r.bmr_kcal.value,
        visceral_fat_level=r.visceral_fat_level.value,
        inbody_score=r.inbody_score.value,
    )
    assert validate_scan(values, r.height_cm.value).flags == ()


def test_demo_has_no_answer_for_other_schemas() -> None:
    class Unknown(BaseModel):
        x: int

    raw = ask(Unknown)
    assert raw.parsed is None and raw.error


def test_demo_chat_says_it_is_a_demo() -> None:
    result = DemoProvider().chat(
        model="demo",
        system="",
        messages=[UserMessage("hi")],
        tools=[],
        max_output_tokens=100,
        reasoning_effort=None,
        user_ref=None,
    )
    assert isinstance(result, ChatResult) and "demo" in result.text.lower()
