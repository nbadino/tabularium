"""Read Tabularium's gold table crops with one checkpoint, on the GPU host that trained it.

Written by `backend/app/services/gold_eval.py` into `<remote run>/gold/` with
`requests.json` (protocol + samples) and the crops, and run with the interpreter the
recipe declares (`gold_eval.python`). It only generates: scoring happens in the
backend. Outputs are raw, one JSON line per crop, and nothing is repaired here.

Protocol, from `requests.json`: the recipe's table prompt after the image, the
recipe's image-size bounds (processor `size`), greedy decoding with
`max_new_tokens`, a per-crop time limit, and a total budget after which no new crop
is started. `environment.json` records the stack it ran with.
"""
import argparse
import hashlib
import json
import platform
import signal
import subprocess
import sys
import time
from pathlib import Path


def sha256(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--requests", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--environment", required=True)
    parser.add_argument("--budget-s", type=int, required=True)
    args = parser.parse_args()
    started = time.monotonic()
    root = Path(args.requests).resolve().parent
    requests = json.loads(Path(args.requests).read_text(encoding="utf-8"))
    protocol = requests["protocol"]
    model_dir = Path(args.model)
    environment = {"python": sys.version.split()[0], "platform": platform.platform(),
                   "model": str(model_dir),
                   "chat_template_sha256": sha256(model_dir / "chat_template.jinja"),
                   "config_sha256": sha256(model_dir / "config.json")}
    try:
        import torch
        import transformers
        from PIL import Image
        from transformers import AutoModelForImageTextToText, AutoProcessor

        environment.update(torch=torch.__version__, transformers=transformers.__version__,
                           cuda=torch.version.cuda, gpu=torch.cuda.get_device_name(0))
        try:
            environment["driver"] = subprocess.run(
                ["nvidia-smi", "--query-gpu=driver_version", "--format=csv,noheader"],
                capture_output=True, text=True, timeout=30, check=False).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            environment["driver"] = None
        processor = AutoProcessor.from_pretrained(str(model_dir))
        model = AutoModelForImageTextToText.from_pretrained(
            str(model_dir), dtype=torch.bfloat16, attn_implementation="sdpa").to("cuda").eval()
    except Exception as exc:  # noqa: BLE001 - reported to the backend, which records it
        environment["error"] = f"{type(exc).__name__}: {exc}"[:2000]
        Path(args.environment).write_text(json.dumps(environment, indent=1), encoding="utf-8")
        return 2
    Path(args.environment).write_text(json.dumps(environment, indent=1), encoding="utf-8")
    eos = model.generation_config.eos_token_id
    eos = [eos] if isinstance(eos, int) else list(eos or [])
    limit = int(protocol["max_new_tokens"])

    def alarm(_signum, _frame):
        raise TimeoutError(f"crop exceeded {protocol['page_timeout_s']} s")

    signal.signal(signal.SIGALRM, alarm)
    with open(args.out, "a", encoding="utf-8") as out:
        for sample in requests["samples"]:
            if time.monotonic() - started > args.budget_s:
                print("time budget reached; remaining crops not started", flush=True)
                break
            path = root / sample["image"]
            record = {"id": sample["id"], "image_sha256": sha256(path)}
            t0 = time.monotonic()
            try:
                signal.alarm(int(protocol["page_timeout_s"]))
                image = Image.open(path).convert("RGB")
                messages = [{"role": "user", "content": [{"type": "image", "image": image},
                                                         {"type": "text", "text": sample["prompt"]}]}]
                inputs = processor.apply_chat_template(
                    messages, tokenize=True, add_generation_prompt=True, return_dict=True,
                    return_tensors="pt", processor_kwargs={"images_kwargs": {"size": protocol["image_size"]}},
                    **protocol.get("chat_template_kwargs", {}),
                ).to("cuda")
                with torch.inference_mode():
                    generated = model.generate(**inputs, max_new_tokens=limit, do_sample=False, use_cache=True)
                tokens = generated[0, inputs["input_ids"].shape[-1]:]
                reached = bool(len(tokens)) and int(tokens[-1]) in eos
                record.update(state="finished", prediction=processor.decode(tokens, skip_special_tokens=True),
                              input_tokens=int(inputs["input_ids"].shape[-1]), generated_tokens=int(len(tokens)),
                              eos_reached=reached, truncated=(not reached) and len(tokens) >= limit)
            except Exception as exc:  # noqa: BLE001 - one crop failing is a scored failure
                record.update(state="failed", error=f"{type(exc).__name__}: {exc}"[:1000])
            finally:
                signal.alarm(0)
            record["seconds"] = round(time.monotonic() - t0, 2)
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(sample["id"], record["state"], record.get("generated_tokens"), record["seconds"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
