#!/usr/bin/env python3
"""CLI utility to test connection and inference speed of a remote Cloud vLLM server.

Usage:
    python scripts/cloud/test_cloud_connection.py [--url http://127.0.0.1:8888/v1] [--api-key KEY] [--sample]
"""
from __future__ import annotations

import argparse
import sys
import time
from PIL import Image, ImageDraw

import requests


def parse_args():
    parser = argparse.ArgumentParser(description="Test vLLM Cloud Inference Endpoint for Tabularium")
    parser.add_argument("--url", default="http://127.0.0.1:8888/v1", help="vLLM endpoint URL (default: http://127.0.0.1:8888/v1)")
    parser.add_argument("--api-key", default="", help="Optional API key / Bearer token")
    parser.add_argument("--model", default="MonkeyOCRv2", help="Model name (default: MonkeyOCRv2)")
    parser.add_argument("--sample", action="store_true", help="Run a real sample image inference test")
    parser.add_argument("--timeout", type=int, default=30, help="HTTP timeout in seconds")
    return parser.parse_args()


def make_sample_image() -> Image.Image:
    img = Image.new("RGB", (800, 400), color=(250, 248, 245))
    d = ImageDraw.Draw(img)
    d.rectangle([(20, 20), (780, 80)], outline=(50, 50, 50), width=2)
    d.text((40, 40), "HISTORIC SHIPPING INDEX — CLOUD TEST", fill=(0, 0, 0))
    d.rectangle([(20, 100), (780, 360)], outline=(100, 100, 100), width=1)
    d.text((40, 120), "Vessel: Antigravity | Port: London | Status: Arrived", fill=(0, 0, 0))
    return img


def main():
    args = parse_args()
    url = args.url.rstrip("/")
    headers = {"Content-Type": "application/json"}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"

    print("=" * 60)
    print(">> [Tabularium] Verifica Connessione Cloud / Remote vLLM")
    print(f">> Target URL: {url}")
    if args.api_key:
        print(f">> API Key:    {'*' * (len(args.api_key) - 4) + args.api_key[-4:] if len(args.api_key) > 4 else '***'}")
    print("=" * 60)

    # 1. Ping /models
    print("\n[1/2] Test disponibilità endpoint (/models)...")
    start = time.perf_counter()
    try:
        resp = requests.get(f"{url}/models", headers=headers, timeout=args.timeout)
        resp.raise_for_status()
        latency_ms = (time.perf_counter() - start) * 1000
        data = resp.json()
        models = [m.get("id") for m in data.get("data", []) if isinstance(m, dict) and "id" in m]
        if not models and "id" in data:
            models = [data["id"]]
        print(f"  ✓ Connessione riuscita! Latenza ping: {latency_ms:.1f} ms")
        print(f"  ✓ Modelli disponibili sul server: {', '.join(models) if models else 'N/A'}")
    except Exception as exc:
        print(f"  ✗ ERRORE: Impossibile raggiungere il server: {exc}", file=sys.stderr)
        print("\nSuggerimenti:")
        print("  - Se usi Vast.ai / RunPod con SSH Tunnel, assicurati che il tunnel sia attivo:")
        print("      ./scripts/cloud/ssh_tunnel.sh <ssh_command>")
        print("  - Se usi un IP pubblico diretto, verifica che la porta sia aperta nel firewall.")
        print("  - Se il server richiede autenticazione, passa --api-key <token>.")
        sys.exit(1)

    # 2. Sample Inference
    if args.sample:
        print("\n[2/2] Esecuzione inferenza di test con ritaglio sintetico...")
        import base64
        import io

        sample_img = make_sample_image()
        buf = io.BytesIO()
        sample_img.save(buf, format="PNG")
        data_uri = "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")

        payload = {
            "model": args.model or (models[0] if models else "MonkeyOCRv2"),
            "temperature": 0,
            "max_tokens": 1024,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": data_uri}},
                        {"type": "text", "text": "Please output the text content from the image."},
                    ],
                }
            ],
        }

        inf_start = time.perf_counter()
        try:
            inf_resp = requests.post(f"{url}/chat/completions", json=payload, headers=headers, timeout=args.timeout)
            inf_resp.raise_for_status()
            inf_dur = (time.perf_counter() - inf_start) * 1000
            inf_data = inf_resp.json()
            content = inf_data["choices"][0]["message"]["content"]
            usage = inf_data.get("usage", {})
            tokens = usage.get("completion_tokens", 0)
            tps = (tokens / (inf_dur / 1000.0)) if inf_dur > 0 and tokens > 0 else 0

            print(f"  ✓ Inferenza completata in {inf_dur:.1f} ms!")
            if tokens:
                print(f"  ✓ Token generati: {tokens} (~{tps:.1f} token/s)")
            print(f"\n>> Testo estratto dal cloud:\n{content.strip()}")
        except Exception as exc:
            print(f"  ✗ Errore durante l'inferenza: {exc}", file=sys.stderr)
            sys.exit(1)

    print("\n" + "=" * 60)
    print(">> [SUCCESS] Il server Cloud è pronto e compatibile con Tabularium!")
    print("=" * 60)


if __name__ == "__main__":
    main()
