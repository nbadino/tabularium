# Supported models

Tabularium adds new open-weight OCR models as they are released. A model is
listed as verified only after it has been run on real pages.

| Model | NVIDIA (vLLM) | Apple Silicon (MLX) |
|---|:-:|:-:|
| MonkeyOCRv2-Parsing | ✅ | — |
| PaddleOCR-VL | ✅ | ✅ |
| MinerU2.5 | ✅ | 🧪 |
| Qwen3-VL | ✅ | 🧪 |
| Unlimited-OCR | ✅ | 🧪 |
| DeepSeek-OCR-2, GLM-OCR, dots.mocr | ✅ | — |
| Any Hugging Face repo you register | ✅ | — |

✅ verified on real pages · 🧪 experimental · — not available locally (use a
remote GPU). Every model also runs remotely. The measured matrix, and the
reason behind each cell, is in
[local inference guide](LOCAL_INFERENCE_GUIDE.md); training
support per model is in the [training guide](TRAINING_GUIDE.md).
Missing a model? [Open an issue](https://github.com/nbadino/tabularium/issues/new?template=feature_request.yml).

