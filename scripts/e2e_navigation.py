"""Verify that every primary SPA route reaches its actual page heading."""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import requests
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait


BASE = os.environ.get("TABULARIUM_E2E_URL", "http://127.0.0.1:8787").rstrip("/")
ROUTES = {
    "/": ("Riconosci immagini", "Recognize images", "Reconnaître les images"),
    "/risultati": ("Risultati", "Results", "Résultats"),
    "/archivio": ("Archivio attivo", "Archive review", "Archives"),
    "/modelli": ("Modelli", "Models", "Modèles"),
    "/progetti": ("Progetti", "Projects", "Projets"),
    "/annotazione": ("Pagine", "Pages", "Pages"),
    "/dataset": ("Dataset",),
    "/training": ("Fine-tuning", "Training", "Fine tuning"),
    "/valutazione": ("Valutazione", "Evaluation", "Évaluation"),
    "/playground": ("Playground",),
}


def _options(profile: Path) -> Options:
    options = Options()
    binary = os.environ.get("TABULARIUM_CHROMIUM", "")
    if binary:
        options.binary_location = binary
    for flag in (
        "--headless=new",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
        "--no-first-run",
        f"--user-data-dir={profile}",
    ):
        options.add_argument(flag)
    return options


def main() -> int:
    requests.get(f"{BASE}/api/health", timeout=10).raise_for_status()
    root = Path(tempfile.mkdtemp(prefix="tabularium-navigation-e2e-"))
    driver = webdriver.Chrome(
        service=Service(os.environ.get("TABULARIUM_CHROMEDRIVER") or None),
        options=_options(root / "profile"),
    )
    wait = WebDriverWait(driver, 20)
    checked: list[str] = []
    try:
        driver.set_window_size(1440, 1000)
        for route, headings in ROUTES.items():
            driver.get(f"{BASE}{route}")

            def page_is_ready(d: webdriver.Chrome) -> bool:
                body = d.find_element(By.TAG_NAME, "body").text
                if "Errore imprevisto" in body or "Unexpected error" in body:
                    return False
                return any(label.casefold() in body.casefold() for label in headings)

            try:
                wait.until(page_is_ready)
            except Exception as exc:
                raise AssertionError(
                    f"route {route} did not reach its content; body={driver.find_element(By.TAG_NAME, 'body').text[:800]!r}"
                ) from exc
            checked.append(route)
        print(f"e2e navigation OK: {', '.join(checked)}")
        return 0
    finally:
        driver.quit()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
