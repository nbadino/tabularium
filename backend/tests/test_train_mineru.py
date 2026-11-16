"""MinerU2.5 training: the vendor path is not obtainable, and the recipe says so (R3/R5)."""
from __future__ import annotations

from app.services import auto_training, train_recipes


def test_mineru_stays_planned_without_a_one_click_profile():
    recipe = train_recipes.get_recipe("mineru2.5")
    assert recipe.maturity == "planned"
    assert recipe.toolchain == "mineru-sft"
    # A model whose vendor bundle is missing must not get a surrogate one-click run.
    assert auto_training.profile_for("mineru2.5") is None


def test_mineru_absence_cites_the_evidence_read_on_2026_11_16():
    recipe = train_recipes.get_recipe("mineru2.5")
    text = " ".join(recipe.official_notes)
    # The lesson and the tree at the commit that was checked: no zip, no release asset.
    assert "run_mineru.py sft --config configs/sft/sft_table_rec.yaml" in text
    assert "mineru_sft_code.zip" in text
    assert "761b4a9895" in text
    # The only public `run_mineru.py` is an unmerged MetaX lesson that rewrites it as
    # plain `swift sft`; it is cited and explicitly not adopted.
    assert "opendatalab/mineru-tutorials#1" in text
    assert "not adopted" in text
    # The lesson trains the 2509 base; support for the Pro checkpoint is unverified.
    assert "MinerU2.5-2509-1.2B" in text
    assert "MinerU2.5-Pro-2605-1.2B" in text and "unverified" in text
    # The user knows what to supply to unblock the recipe.
    assert "heywhale" in text


def test_mineru_keeps_the_vendor_prompts_with_the_leading_newline():
    recipe = train_recipes.get_recipe("mineru2.5")
    assert "\nTable Recognition:" in recipe.prompts.values()
