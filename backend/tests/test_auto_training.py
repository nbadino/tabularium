"""Il percorso a un click: profilo giusto, comando onstart, job persistito."""
from __future__ import annotations

import base64
import re

import pytest

from app import config
from app.services import auto_training, train_recipes


def test_every_trainable_recipe_has_a_profile():
    for recipe in train_recipes.TRAIN_RECIPES.values():
        if recipe.script_ready and recipe.train_types and recipe.toolchain != "none":
            assert auto_training.profile_for(recipe.adapter_id), recipe.adapter_id


def test_expected_paths_match_the_provisioning_root():
    assert auto_training.expected_paths("monkey")["repo"].endswith("/MonkeyOCRv2/parsing/train")
    assert auto_training.expected_paths("glm")["repo"].endswith("/LLaMA-Factory")
    assert auto_training.expected_paths("paddle")["repo"].endswith("/PaddleFormers")
    assert auto_training.expected_paths("qwen")["python"].endswith("/qwen/venv/bin/python")


def test_onstart_embeds_the_script_and_the_profile():
    command = auto_training._onstart("glm", "glm-ocr")  # noqa: SLF001
    assert "TABULARIUM_TRAIN_PROFILE=glm" in command
    assert "TABULARIUM_TRAIN_ADAPTER=glm-ocr" in command
    assert "setup_train_env.sh" in command
    match = re.search(r"echo ([A-Za-z0-9+/=]+)", command)
    assert match, command
    decoded = base64.b64decode(match.group(1)).decode("utf-8")
    assert "TABULARIUM_TRAIN_PROFILE" in decoded
    assert "LLaMA-Factory" in decoded


def test_provisioning_script_is_packaged():
    path = config.RESOURCE_DIR / "scripts" / "cloud" / "setup_train_env.sh"
    assert path.is_file()
    assert "paddleformers-cli" in path.read_text(encoding="utf-8")


def test_job_path_refuses_traversal():
    with pytest.raises(FileNotFoundError):
        auto_training.get_job("../../etc/passwd")
    with pytest.raises(FileNotFoundError):
        auto_training.get_job("auto_1/../../x")


def test_run_rents_provisions_and_starts_training(monkeypatch):
    import subprocess
    import threading

    from app.services import cloud_manager, trainer

    job = {
        "id": "auto_123",
        "project_id": 1,
        "adapter_id": "glm-ocr",
        "profile": "glm",
        "state": "queued",
        "phase": "rent",
        "message": "",
        "error": None,
        "log": "",
        "offer": None,
        "instance_id": None,
        "host": None,
        "port": None,
        "run_id": None,
        "instance_destroyed": False,
        "created_at": "now",
        "updated_at": "now",
    }
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    monkeypatch.setattr(
        cloud_manager, "search_vast_offers",
        lambda *args, **kwargs: [{"id": 5, "gpu_name": "A100", "dph_total": 0.5, "cuda_max_good": 12.8}],
    )
    rented: dict = {}

    def fake_rent(*args, **kwargs):
        rented.update(kwargs)
        return {"contract_id": 77}

    monkeypatch.setattr(cloud_manager, "rent_vast_instance", fake_rent)
    monkeypatch.setattr(cloud_manager, "track_cloud_resource", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cloud_manager, "list_vast_instances",
        lambda *args, **kwargs: [{"id": 77, "is_running": True, "ssh_host": "gpu.example", "ssh_port": 2222}],
    )
    monkeypatch.setattr(cloud_manager, "pin_ssh_host_key", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(cloud_manager, "check_ssh_access", lambda *args, **kwargs: {"ok": True})
    ready = '{"python": "/root/tabularium-train/glm/venv/bin/python", "repo": "/root/tabularium-train/glm/LLaMA-Factory"}'
    monkeypatch.setattr(auto_training, "_ssh", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, ready, ""))
    started: dict = {}

    def fake_start(project_id, cfg, **kwargs):
        started.update({"project_id": project_id, "cfg": cfg, **kwargs})
        return {"run": {"run_id": "run_1"}}

    monkeypatch.setattr(trainer, "start_run", fake_start)
    monkeypatch.setattr(auto_training, "_monitor_run", lambda *args, **kwargs: None)
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "glm-ocr", "train_type": "lora"}, "key", "glm", 1, "it", 1.0, 24, 100,
        threading.Event(),
    )
    assert job["state"] == "running"
    assert job["run_id"] == "run_1"
    assert started["cfg"]["executor"] == "vast"
    assert started["cfg"]["ssh_host"] == "gpu.example"
    assert started["cfg"]["auto_prepare"] is True
    assert started["cfg"]["use_hf"] is True
    assert rented["image"] == auto_training.PREBUILT_IMAGES["glm"]
    assert rented["onstart_script"] == ""


def test_best_offer_prefers_vram_within_budget():
    offers = [
        {"id": 1, "dph_total": 0.20, "gpu_ram": 24 * 1024, "compute_cap": 860, "reliability": 0.99},
        {"id": 2, "dph_total": 0.35, "gpu_ram": 24 * 1024, "compute_cap": 890, "reliability": 0.98},
        {"id": 3, "dph_total": 0.90, "gpu_ram": 40 * 1024, "compute_cap": 800, "reliability": 0.99},
    ]
    assert auto_training.best_offer(offers, 0.5)["id"] == 2
    assert auto_training.best_offer(offers, 1.0)["id"] == 3
    assert auto_training.best_offer(offers, 0.1) is None


def test_start_auto_requires_a_vast_key():
    with pytest.raises(ValueError, match="API Key"):
        auto_training.start_auto(1, {"adapter_id": "glm-ocr"}, "", owner_id=1)


def test_monkey_run_uses_the_prebuilt_image(monkeypatch):
    import subprocess
    import threading

    from app.services import cloud_manager, trainer

    job = {
        "id": "auto_2", "project_id": 1, "adapter_id": "monkeyocrv2-parsing", "profile": "monkey",
        "state": "queued", "phase": "rent", "message": "", "error": None, "log": "",
        "offer": None, "instance_id": None, "host": None, "port": None, "run_id": None,
        "instance_destroyed": False, "created_at": "now", "updated_at": "now",
    }
    monkeypatch.setattr(auto_training, "_save", lambda _job: None)
    monkeypatch.setattr(
        cloud_manager, "search_vast_offers",
        lambda *args, **kwargs: [{"id": 5, "gpu_name": "A100", "dph_total": 0.5, "cuda_max_good": 12.8}],
    )
    rented: dict = {}

    def fake_rent(*args, **kwargs):
        rented.update(kwargs)
        return {"contract_id": 77}

    monkeypatch.setattr(cloud_manager, "rent_vast_instance", fake_rent)
    monkeypatch.setattr(cloud_manager, "track_cloud_resource", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cloud_manager, "list_vast_instances",
        lambda *args, **kwargs: [{"id": 77, "is_running": True, "ssh_host": "gpu.example", "ssh_port": 2222}],
    )
    monkeypatch.setattr(cloud_manager, "pin_ssh_host_key", lambda *args, **kwargs: {"ok": True})
    monkeypatch.setattr(cloud_manager, "check_ssh_access", lambda *args, **kwargs: {"ok": True})
    ready = '{"python": "/opt/tabularium/venv/bin/python", "repo": "/opt/tabularium/MonkeyOCRv2/parsing/train"}'
    monkeypatch.setattr(auto_training, "_ssh", lambda *args, **kwargs: subprocess.CompletedProcess([], 0, ready, ""))
    started: dict = {}
    monkeypatch.setattr(trainer, "start_run", lambda project_id, cfg, **kwargs: started.update({"cfg": cfg, **kwargs}) or {"run": {"run_id": "run_2"}})
    monkeypatch.setattr(auto_training, "_monitor_run", lambda *args, **kwargs: None)
    auto_training._run(  # noqa: SLF001
        job, {"adapter_id": "monkeyocrv2-parsing", "train_type": "lora"}, "key", "monkey", 1, "it", 1.0, 24, 100,
        threading.Event(),
    )
    assert rented["image"] == auto_training.PREBUILT_IMAGES["monkey"]
    assert rented["onstart_script"] == ""
    assert started["cfg"]["disable_deepspeed"] is True


def test_every_runtime_profile_has_a_prebuilt_image():
    assert set(auto_training.PROFILES.values()) - {"dots"} <= set(auto_training.PREBUILT_IMAGES)
    assert "dots" not in auto_training.PREBUILT_IMAGES
    assert all(tag.startswith("ghcr.io/nbadino/tabularium-train-") for tag in auto_training.PREBUILT_IMAGES.values())
