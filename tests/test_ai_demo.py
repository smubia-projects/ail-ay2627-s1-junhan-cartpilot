"""Demo-mode AI tests: no network, no API key needed."""
import pytest

from app import ai, config
from pydantic import ValidationError

from app.models import Ingredient, IngredientList


@pytest.fixture(autouse=True)
def demo(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", True)


async def test_recipe_options():
    r = await ai.suggest_recipes("bake a cake", 6)
    assert 1 <= len(r.options) <= 3


async def test_ingredients_scale_with_servings():
    base = await ai.generate_ingredients("cake", "Classic Chocolate Cake", 6)
    double = await ai.generate_ingredients("cake", "Classic Chocolate Cake", 12)
    flour = lambda l: next(i for i in l.ingredients if i.name == "plain flour").quantity
    assert flour(double) == pytest.approx(2 * flour(base), rel=0.05)
    assert isinstance(base, IngredientList)


async def test_eggs_are_whole_pieces():
    r = await ai.generate_ingredients("cake", "Classic Chocolate Cake", 5)
    eggs = next(i for i in r.ingredients if i.name == "egg")
    assert eggs.unit == "piece" and eggs.quantity == int(eggs.quantity)


# ---- cooking steps ----
async def test_demo_cake_has_steps_and_times():
    r = await ai.generate_ingredients("cake", "Classic Chocolate Cake", 6)
    assert 5 <= len(r.steps) <= 10
    assert r.prep_time and r.cook_time
    assert all(s.strip() and s[0].isalpha() for s in r.steps)       # plain sentences, no "1." in front


async def test_demo_steps_use_the_scaled_quantities():
    six = " ".join((await ai.generate_ingredients("cake", "Classic Chocolate Cake", 6)).steps)
    twelve = " ".join((await ai.generate_ingredients("cake", "Classic Chocolate Cake", 12)).steps)
    assert "225 g plain flour" in six and "3 eggs" in six and "a pinch of salt" in six
    assert "450 g plain flour" in twelve and "6 eggs" in twelve


async def test_every_demo_recipe_has_clean_steps():
    for rec in {**ai._DEMO_RECIPES, **ai._DEMO_EXTRA}.values():
        r = await ai.generate_ingredients("x", rec[0], rec[4])
        assert r.steps, rec[0]
        assert not any("{" in s or "}" in s for s in r.steps), rec[0]   # every placeholder was filled in


def test_ingredient_list_schema_with_steps():
    base = dict(recipe_title="X", servings=2, ingredients=[Ingredient(name="flour", quantity=100, unit="g")])
    full = IngredientList(**base, steps=["Mix.", "Bake."], prep_time="5 min", cook_time="20 min")
    assert full.steps == ["Mix.", "Bake."] and full.prep_time == "5 min" and full.cook_time == "20 min"
    assert IngredientList.model_validate_json(full.model_dump_json()) == full
    bare = IngredientList(**base)                                   # steps and times are optional
    assert bare.steps == [] and bare.prep_time == "" and bare.cook_time == ""
    with pytest.raises(ValidationError):
        IngredientList(**base, steps="not a list")


async def test_live_mode_asks_for_steps_in_the_same_call(monkeypatch):
    """One Gemini call returns ingredients AND steps; numbering the model adds is stripped."""
    monkeypatch.setattr(config, "DEMO_MODE", False)
    calls = []

    async def fake_call_json(prompt, schema):
        calls.append(prompt)
        return IngredientList(recipe_title="X", servings=2, ingredients=[Ingredient(name="flour", quantity=100, unit="g")],
                              steps=["1. Mix the flour.", "Step 2: Bake.", "  ", "3) Cool."], cook_time="20 min")

    monkeypatch.setattr(ai, "call_json", fake_call_json)
    r = await ai.generate_ingredients("x", "X", 4)
    assert len(calls) == 1 and "steps" in calls[0] and "5 to 10" in calls[0]
    assert r.steps == ["Mix the flour.", "Bake.", "Cool."] and r.servings == 4 and r.cook_time == "20 min"


def test_clean_steps_keeps_real_quantities():
    assert ai._clean_steps(["2.5 cups of milk go in", "175 g butter", "10-minute rest", "2. Stir"]) == \
        ["2.5 cups of milk go in", "175 g butter", "10-minute rest", "Stir"]


async def test_missing_key_gives_friendly_error(monkeypatch):
    monkeypatch.setattr(config, "DEMO_MODE", False)
    monkeypatch.setattr(config, "API_KEY", "")
    with pytest.raises(ai.AIError):
        await ai.suggest_recipes("cake", 4)


def test_ai_schemas_avoid_keywords_gemini_sdk_rejects():
    """Field(gt=..)/Field(lt=..) emit 'exclusiveMinimum/Maximum', which the Gemini SDK refuses.
    Use ge/le instead. This catches the mistake offline."""
    from app.models import FridgeScan, IngredientList, LeftoverIdeas, RecipeOptions, ReviewResult
    for schema in (RecipeOptions, IngredientList, FridgeScan, ReviewResult, LeftoverIdeas):
        text = str(schema.model_json_schema())
        assert "exclusiveMinimum" not in text and "exclusiveMaximum" not in text, schema.__name__
