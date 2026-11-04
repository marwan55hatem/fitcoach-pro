"""Estimate a meal from a photo or a description. The AI proposes a DRAFT with ranges;
app/rules/food_validation.py checks it and nothing counts until the user confirms.

Only the photo and the user's own words about the meal are sent: no name, email or
profile. Text in the photo or the note is data, never instructions.
"""

import uuid

from pydantic import BaseModel, Field

from app.ai.client import AiClient
from app.ai.types import AiImage, Task, UserMessage
from app.ai.untrusted import with_security_preamble, wrap_untrusted

MAX_OUTPUT_TOKENS = 2500
MAX_TEXT_CHARS = 1000

SYSTEM_PROMPT = with_security_preamble(
    "You estimate the calories and macros of a meal for a gym coaching app in Egypt. "
    "List each food separately with an estimated portion (grams, or units such as "
    "'2 pieces' when grams make no sense), a calorie RANGE (kcal_min to kcal_max) and "
    "protein, carbs and fat in grams. Portions from photos are uncertain: make the "
    "range honest (wider when unsure), never claim precision, and keep calories close "
    "to 4 kcal per gram of protein and carbs and 9 per gram of fat. Count cooking oil, "
    "ghee and sauces you can reasonably expect. If the portion or the dish is unclear, "
    "still give your best range and add a short clarifying question. If there is no "
    "food, set food_found to false and return no items.\n"
    "Common Egyptian dishes and typical portions, as a rough guide only:\n"
    "- koshari: a medium plate is about 400-500 g, roughly 650-900 kcal\n"
    "- foul medames: a bowl is about 250 g, roughly 250-350 kcal before oil; each "
    "tablespoon of oil adds about 120 kcal\n"
    "- taameya (Egyptian falafel): one fried piece is about 30 g, roughly 60-90 kcal\n"
    "- fatta: a plate of rice, bread, broth and meat is roughly 700-1100 kcal\n"
    "- molokhia: a bowl is about 250 g, roughly 100-200 kcal (more with ghee)\n"
    "- mahshi: one stuffed vine leaf is roughly 30-45 kcal; one stuffed pepper or "
    "zucchini roughly 80-140 kcal\n"
    "- kofta: one grilled skewer of about 100 g is roughly 220-300 kcal\n"
    "- shawerma: a sandwich is roughly 450-700 kcal; a plate more\n"
    "- baladi bread: one loaf is about 90-110 g, roughly 240-290 kcal\n"
    "- feteer meshaltet: a quarter is roughly 400-600 kcal\n"
    "- rice: a cooked cup (about 180 g) is roughly 230-260 kcal; Egyptian rice with "
    "vermicelli and ghee roughly 280-340 kcal\n"
    "- grilled chicken: a quarter with skin is roughly 300-400 kcal\n"
    "- tuna: a can (about 120 g drained) is roughly 130-160 kcal in water, 230-290 in oil\n"
    "- zabadi (plain yogurt): a cup is roughly 100-150 kcal\n"
    "Write food names in English, or the Egyptian name in Latin letters."
)
PHOTO_REQUEST = "Estimate the meal in this photo."
TEXT_REQUEST = "Estimate this meal from the user's description."


class AiFoodItem(BaseModel):
    name: str
    grams: float | None = Field(description="estimated weight in grams, or null")
    units: str | None = Field(description="a count such as '2 pieces', or null")
    kcal_min: int
    kcal_max: int
    protein_g: float
    carbs_g: float
    fat_g: float


class AiFoodEstimate(BaseModel):
    food_found: bool = Field(description="false when there is no food to estimate")
    meal_name: str = Field(description="a short name for the whole meal")
    items: list[AiFoodItem]
    confidence: float = Field(description="0 to 1: how sure the overall estimate is")
    questions: list[str] = Field(
        description="short questions that would make the estimate better, or none"
    )


def _estimate(ai: AiClient, user_id: uuid.UUID, message: UserMessage) -> AiFoodEstimate:
    return ai.structured(
        user_id=user_id,
        task=Task.VISION if message.images else Task.MAIN,
        schema=AiFoodEstimate,
        system=SYSTEM_PROMPT,
        messages=[message],
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def estimate_from_photo(
    ai: AiClient, *, user_id: uuid.UUID, image: AiImage, note: str | None = None
) -> AiFoodEstimate:
    text = PHOTO_REQUEST
    if note:
        text += "\nThe user's note about the meal:\n" + wrap_untrusted(
            note[:MAX_TEXT_CHARS], "meal_note"
        )
    return _estimate(ai, user_id, UserMessage(text, images=(image,)))


def estimate_from_text(
    ai: AiClient, *, user_id: uuid.UUID, text: str
) -> AiFoodEstimate:
    body = TEXT_REQUEST + "\n" + wrap_untrusted(text[:MAX_TEXT_CHARS], "meal_text")
    return _estimate(ai, user_id, UserMessage(body))
