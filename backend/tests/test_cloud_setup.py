"""Regressioni del provisioning cloud del training.

Sono test sul *file* di setup, non sul backend: il loro scopo è impedire che due
errori già visti su un host GPU tornino silenziosamente.

1. Il fork ms-swift di MonkeyOCRv2 non versiona
   `swift/llm/dataset/data/dataset_info.json`, ma lo importa a runtime:
   senza, `swift sft` muore con `JSONDecodeError` prima di leggere il dataset.
2. Torch non deve essere riscaricato a ogni provisioning quando il venv ha già
   la CUDA che l'indice del driver richiede.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "cloud" / "setup_cloud_train.sh"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def test_missing_dataset_info_is_created_for_the_monkey_fork():
    text = _script()
    assert "swift/llm/dataset/data/dataset_info.json" in text
    assert "printf '[]\\n'" in text
    assert "register_dataset_info" in text


def test_deepspeed_is_installed_because_the_official_script_uses_it():
    assert 'deepspeed==0.17.1' in _script()


def test_torch_install_is_skipped_when_cuda_already_matches():
    text = _script()
    assert 'HAVE_CUDA="$(python -c' in text
    assert 'HAVE_VISION="$(python -c' in text
    assert 'if [ -n "$HAVE_CUDA" ] && [ "$HAVE_CUDA" = "$WANT_CUDA" ] && [ -n "$HAVE_VISION" ]' in text
    # Torchvision è importato dal modello: non basta controllare torch.
    assert 'python -m pip install torch torchvision --index-url "$TORCH_INDEX"' in text
    # L'installazione deve stare nel ramo else, non essere incondizionata.
    install = 'python -m pip install torch torchvision --index-url "$TORCH_INDEX"'
    before = text[: text.index(install)][-1500:]
    assert "else" in before


def test_want_cuda_maps_each_torch_index_correctly():
    text = _script()
    match = re.search(r"WANT_CUDA=.*?sed -nE '([^']+)'", text)
    assert match, "espressione WANT_CUDA non trovata"
    expression = match.group(1)
    expected = {
        "https://download.pytorch.org/whl/cu128": "12.8",
        "https://download.pytorch.org/whl/cu126": "12.6",
        "https://download.pytorch.org/whl/cu124": "12.4",
        "https://download.pytorch.org/whl/cu121": "12.1",
    }
    for url, cuda in expected.items():
        result = subprocess.run(
            ["sed", "-nE", expression],
            input=url,
            capture_output=True,
            text=True,
            check=True,
        )
        assert result.stdout.strip() == cuda, (url, result.stdout)
