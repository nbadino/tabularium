"""Lettura dell'hardware e verdetto locale per modello.

Queste asserzioni difendono una promessa di prodotto: Tabularium dice cosa
gira su *questa* macchina, e quando non gira dice perché. Un test che passasse
solo su Linux falserebbe la promessa, quindi la piattaforma è simulata.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.services import hardware


def _machine(monkeypatch, *, system: str, machine: str, gpus: list[dict] | None = None, memory_gb: float = 24.0):
    monkeypatch.setattr(hardware.platform, "system", lambda: system)
    monkeypatch.setattr(hardware.platform, "machine", lambda: machine)
    monkeypatch.setattr(hardware, "cuda_devices", lambda: gpus or [])
    monkeypatch.setattr(hardware, "total_memory_gb", lambda: memory_gb)
    monkeypatch.setattr(hardware.sys, "platform", "darwin" if system == "Darwin" else "linux")
    return hardware.summary()


def test_apple_silicon_hosts_mlx_not_vllm(monkeypatch):
    m = _machine(monkeypatch, system="Darwin", machine="arm64")
    assert m["apple_silicon"] is True
    assert m["usable_runtimes"] == [hardware.RUNTIME_MLX]
    # vLLM richiede CUDA: su macOS non è una questione di installazione.
    assert m["runtimes"][hardware.RUNTIME_VLLM]["supported"] is False
    assert m["runtimes"][hardware.RUNTIME_VLLM]["reason"] == hardware.REASON_CUDA_REQUIRED


def test_linux_with_nvidia_hosts_vllm_and_not_mlx(monkeypatch):
    m = _machine(
        monkeypatch,
        system="Linux",
        machine="x86_64",
        gpus=[{"memory_total": 8188}],
    )
    assert m["usable_runtimes"] == [hardware.RUNTIME_VLLM]
    assert m["runtimes"][hardware.RUNTIME_MLX]["reason"] == hardware.REASON_APPLE_SILICON_REQUIRED


def test_linux_without_nvidia_says_which_piece_is_missing(monkeypatch):
    m = _machine(monkeypatch, system="Linux", machine="x86_64")
    assert m["usable_runtimes"] == []
    assert m["runtimes"][hardware.RUNTIME_VLLM]["reason"] == hardware.REASON_NO_NVIDIA_GPU


def test_windows_points_at_wsl_instead_of_denying_cuda(monkeypatch):
    m = _machine(monkeypatch, system="Windows", machine="AMD64")
    assert m["runtimes"][hardware.RUNTIME_VLLM]["wsl"] is True


def test_model_without_an_mlx_port_does_not_run_on_a_mac(monkeypatch):
    """MonkeyOCRv2 e MinerU2.5 sono il caso reale: l'architettura non è in
    `mlx-vlm`, quindi su Apple Silicon non girano in locale — e la causa è il
    modello, non la macchina."""
    m = _machine(monkeypatch, system="Darwin", machine="arm64")
    plan = hardware.plan_local(("vllm",), machine=m, approx_size_gb=1.5)
    assert plan == {
        "runnable": False,
        "runtime": None,
        "reason": hardware.REASON_MODEL_UNSUPPORTED,
    }


def test_model_with_an_mlx_checkpoint_runs_and_says_which_runtime(monkeypatch):
    m = _machine(monkeypatch, system="Darwin", machine="arm64")
    plan = hardware.plan_local(("vllm", "mlx-vlm"), machine=m, approx_size_gb=1.8)
    assert plan == {"runnable": True, "runtime": hardware.RUNTIME_MLX, "reason": None}


def test_on_a_machine_without_any_runtime_the_reason_is_the_machine(monkeypatch):
    m = _machine(monkeypatch, system="Linux", machine="x86_64")
    plan = hardware.plan_local(("vllm", "mlx-vlm"), machine=m, approx_size_gb=1.8)
    assert plan["reason"] == hardware.REASON_NO_LOCAL_RUNTIME


def test_weights_that_do_not_fit_say_so_instead_of_offering_the_button(monkeypatch):
    m = _machine(monkeypatch, system="Darwin", machine="arm64", memory_gb=8.0)
    plan = hardware.plan_local(("vllm", "mlx-vlm"), machine=m, approx_size_gb=16.3)
    assert plan["reason"] == hardware.REASON_INSUFFICIENT_MEMORY


def test_the_mlx_weight_factor_is_what_makes_a_8b_fit_on_24gb(monkeypatch):
    """Il checkpoint MLX è quantizzato a 4 bit: confrontare i pesi pieni
    produrrebbe un falso «non ci sta» proprio sui modelli che su un Mac
    girano meglio."""
    assert hardware.weight_factor(hardware.RUNTIME_MLX) < hardware.weight_factor(hardware.RUNTIME_VLLM)
    m = _machine(monkeypatch, system="Darwin", machine="arm64", memory_gb=24.0)
    assert hardware.fits_in_memory(16.3, m, runtime=hardware.RUNTIME_MLX) is True


def test_native_mineru_mlx_preflight_does_not_assume_a_four_bit_checkpoint(monkeypatch):
    m = _machine(monkeypatch, system="Darwin", machine="arm64", memory_gb=4.0)
    assert hardware.weight_factor(hardware.RUNTIME_MLX) == 0.35
    assert hardware.weight_factor(hardware.RUNTIME_MLX, 1.0) == 1.0
    assert hardware.fits_in_memory(2.5, m, runtime=hardware.RUNTIME_MLX, mlx_weight_factor=1.0) is False


def test_the_serve_choice_is_additive_and_leaves_linux_alone(monkeypatch):
    """Su una macchina CUDA resta il percorso vLLM: l'aggiunta di MLX non deve
    cambiare il comportamento dove vLLM funziona già."""
    caps = SimpleNamespace(local_runtimes=("vllm", "mlx-vlm"), local_mlx_repo="mlx-community/x")
    linux = _machine(monkeypatch, system="Linux", machine="x86_64", gpus=[{"memory_total": 24576}])
    assert hardware.pick_serve_runtime(caps, machine=linux) == hardware.RUNTIME_VLLM

    mac = _machine(monkeypatch, system="Darwin", machine="arm64")
    assert hardware.pick_serve_runtime(caps, machine=mac) == hardware.RUNTIME_MLX

    # Un modello senza checkpoint MLX resta fuori dal percorso Apple.
    without_mlx = SimpleNamespace(local_runtimes=("vllm",), local_mlx_repo="")
    assert hardware.pick_serve_runtime(without_mlx, machine=mac) == hardware.RUNTIME_VLLM
