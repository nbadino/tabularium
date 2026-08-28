"""Stima della VRAM richiesta da una configurazione di training.

Il preflight controllava la GPU in assoluto — «meno di 2 GB liberi: errore,
meno di 6 GB: avviso» — senza mai mettere in relazione la *configurazione* con
la scheda. Su una 4060 da 8 GB scarica quel controllo passa sempre, e poi
`swift sft` va in OOM dopo aver scaricato i pesi e costruito il dataset: il
costo dell'errore è mezz'ora, non un secondo.

## Da dove viene il numero

Con `train_type lora` e `gradient_checkpointing True` — cioè il percorso
ufficiale — i termini che contano sono quattro, e **non** sono quelli che ci si
aspetta:

| Termine | Formula | A 16384 token, batch 1 |
|---|---|---|
| **Logits** | `B × S × vocab × 2` | **4,6 GiB** |
| Pesi | dal disco, bf16 | 2,0 GiB |
| Attivazioni salvate | `L × B × S × H × 2` | 0,9 GiB |
| Ricalcolo di uno strato | `B × S × (H + 3·I) × 2` | 0,3 GiB |
| LoRA + Adam | `r`-dipendente, fp32 | ~0,1 GiB |

Il termine dominante è quello del **vocabolario**: `MonkeyOCRv2-B-Parsing` ha
`vocab_size` 151936 e `hidden_size` 1024, quindi la matrice dei logit è 148
volte più larga dello stato nascosto. È il motivo per cui l'intuizione «è un
modello da 0,7B, ci sta comodo» sbaglia: ci stanno comodi i *pesi*, non la
testa di uscita a lunghezza piena.

Conseguenza pratica sul preset ufficiale (`batch_size 4`, `max_length 16384`):
i soli logit chiedono 18 GiB. Non è una configurazione che una scheda da 8 GB
possa eseguire, e nessun accorgimento a valle la salva.

## Cosa questa stima non è

Una previsione. È un **limite inferiore ragionevole**: ignora la
frammentazione dell'allocatore, i buffer di DeepSpeed, il picco della
cross-entropy in fp32 (che su alcune versioni raddoppia il termine dei logit)
e il contesto CUDA. Serve a dire «questa configurazione non ci sta» con
sicurezza, non «questa ci sta» con sicurezza: perciò il preflight la usa per
bloccare, e lascia un margine prima di dichiarare che va bene.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

MIB = 1024 * 1024

# Geometria di `zenosai/MonkeyOCRv2-B-Parsing`, letta dal checkout ufficiale
# quando c'è; questi valori sono il ripiego se non è configurato.
_DEFAULT_ARCH = {
    "hidden_size": 1024,
    "intermediate_size": 3072,
    "num_hidden_layers": 28,
    "vocab_size": 151936,
    "weights_mib": 2048,
}

# Margine sopra la stima prima di dichiarare che una configurazione ci sta:
# la stima è un limite inferiore, non una previsione.
_HEADROOM = 1.25
# Frammentazione e contesto CUDA: non entrano in nessuna formula ma esistono.
_OVERHEAD_MIB = 640


@dataclass
class VramEstimate:
    """Stima in MiB, scomposta nei termini che la compongono."""

    total_mib: int
    terms: dict[str, int]
    assumptions: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_mib": self.total_mib,
            "terms": self.terms,
            "assumptions": self.assumptions,
        }


def _architecture(model_path: str | None) -> dict[str, Any]:
    """Geometria del modello dal `config.json` ufficiale, se raggiungibile."""
    arch = dict(_DEFAULT_ARCH)
    if not model_path:
        return arch
    config_file = Path(model_path) / "config.json"
    if not config_file.is_file():
        return arch
    try:
        config = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return arch
    for key in ("hidden_size", "intermediate_size", "num_hidden_layers", "vocab_size"):
        value = config.get(key)
        if isinstance(value, int) and value > 0:
            arch[key] = value
    # Il peso reale sul disco è più onesto di qualunque conteggio di parametri.
    total = sum(
        f.stat().st_size
        for f in Path(model_path).glob("*.safetensors")
        if f.is_file()
    )
    if total > 0:
        arch["weights_mib"] = int(total / MIB)
    return arch


def estimate(cfg: dict[str, Any], *, model_path: str | None = None) -> VramEstimate:
    """VRAM richiesta, in MiB, per la configurazione `cfg` su **una** GPU."""
    arch = _architecture(model_path)
    hidden = int(arch["hidden_size"])
    inter = int(arch["intermediate_size"])
    layers = int(arch["num_hidden_layers"])
    vocab = int(arch["vocab_size"])

    batch = max(1, int(cfg.get("batch_size", 1) or 1))
    length = max(1, int(cfg.get("max_length", 16384) or 16384))
    lora = str(cfg.get("train_type", "lora")).lower() == "lora"
    tokens = batch * length

    # bf16 ovunque: è `torch_dtype bfloat16` degli iperparametri ufficiali.
    logits = tokens * vocab * 2 // MIB
    weights = int(arch["weights_mib"])
    # Con gradient checkpointing si conserva l'ingresso di ogni strato.
    saved = layers * tokens * hidden * 2 // MIB
    # E si ricalcola uno strato alla volta: attenzione più MLP.
    recompute = tokens * (hidden + 3 * inter) * 2 // MIB

    if lora:
        # Gradienti e stati Adam solo sugli adattatori: trascurabili accanto al
        # resto, ma vanno contati perché stanno in fp32.
        rank = max(1, int(cfg.get("lora_rank", 8) or 8))
        adapters = layers * rank * (4 * hidden + 4 * inter)
        optimiser = adapters * 16 // MIB
    else:
        # Full-SFT: gradienti bf16 più stati Adam fp32 su *tutti* i pesi. È la
        # ragione per cui non è una strada su una scheda da 8 GB.
        params = weights * MIB // 2
        optimiser = params * 14 // MIB

    terms = {
        "logits": logits,
        "weights": weights,
        "activations_saved": saved,
        "activation_recompute": recompute,
        "optimizer": optimiser,
        "overhead": _OVERHEAD_MIB,
    }
    return VramEstimate(
        total_mib=sum(terms.values()),
        terms=terms,
        assumptions={
            "dtype": "bfloat16",
            "gradient_checkpointing": True,
            "train_type": "lora" if lora else "full",
            "tokens_per_step": tokens,
            **{k: arch[k] for k in ("hidden_size", "vocab_size", "num_hidden_layers")},
        },
    )


def largest_fitting_length(
    cfg: dict[str, Any], free_mib: int, *, model_path: str | None = None
) -> int:
    """La `max_length` più grande che entra in `free_mib` a batch 1.

    Serve al preflight per dire *cosa fare*, non solo che non ci sta. Cerca per
    bisezione sulla stessa stima, così il consiglio non può contraddirla.
    """
    probe = dict(cfg)
    probe["batch_size"] = 1
    low, high = 256, max(512, int(cfg.get("max_length", 16384) or 16384))
    probe["max_length"] = low
    if estimate(probe, model_path=model_path).total_mib * _HEADROOM > free_mib:
        return 0
    while low < high:
        middle = (low + high + 256) // 2
        probe["max_length"] = middle
        if estimate(probe, model_path=model_path).total_mib * _HEADROOM <= free_mib:
            low = middle
        else:
            high = middle - 256
    return (low // 256) * 256


def fits(estimate_mib: int, free_mib: int) -> bool:
    """Vero se la stima sta in `free_mib` con il margine di sicurezza."""
    return estimate_mib * _HEADROOM <= free_mib
