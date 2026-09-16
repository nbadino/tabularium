"""Browser test for the model-first -> execution-provider flow.

The server must already be running.  The test intentionally uses the UI for
the whole interaction; only the health check is done outside the browser.
"""
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
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait


BASE = os.environ.get("TABULARIUM_E2E_URL", "http://127.0.0.1:8787").rstrip("/")


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


def _first_visible(driver: webdriver.Chrome, xpath: str):
    for element in driver.find_elements(By.XPATH, xpath):
        if element.is_displayed():
            return element
    return None


def main() -> int:
    requests.get(f"{BASE}/api/health", timeout=10).raise_for_status()
    root = Path(tempfile.mkdtemp(prefix="tabularium-model-provider-e2e-"))
    driver = webdriver.Chrome(
        service=Service(os.environ.get("TABULARIUM_CHROMEDRIVER") or None),
        options=_options(root / "profile"),
    )
    wait = WebDriverWait(driver, 20)
    try:
        driver.set_window_size(1440, 1000)
        driver.get(f"{BASE}/modelli")
        wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))

        open_catalog = wait.until(
            lambda d: _first_visible(
                d,
                "//button[contains(., 'Scegli il modello') or contains(., 'Choose model') or contains(., 'Choisir')]",
            )
        )
        open_catalog.click()
        wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//*[contains(., 'Catalogo modelli') or contains(., 'Model catalog') or contains(., 'Catalogue')]",
                )
            )
        )

        # In the neutral catalog the user is selecting an identity, not
        # installing anything.  Download controls and local status are hidden.
        visible_buttons = [
            button.text.strip()
            for button in driver.find_elements(By.TAG_NAME, "button")
            if button.is_displayed()
        ]
        assert not any(
            label in text
            for text in visible_buttons
            for label in ("Scarica", "Download", "Télécharger", "Installa", "Install")
        ), visible_buttons

        continue_button = wait.until(
            lambda d: _first_visible(
                d,
                "//button[contains(., 'Continua con questo modello') or contains(., 'Continue with this model') or contains(., 'Continuer avec ce modèle') or contains(., 'Scegli questo modello')]",
            )
        )
        # The model card is the first card in the catalog list.  Its title is
        # read before opening the destination dialog and checked again later.
        card = driver.find_element(By.XPATH, "//div[contains(@class, 'divide-y')]/div[1]")
        model_name = card.find_element(By.XPATH, ".//*[self::span or self::div][normalize-space()][1]").text.strip().splitlines()[0]
        assert model_name, card.text
        continue_button.click()

        wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//*[contains(., 'Dove vuoi eseguirlo?') or contains(., 'Where do you want to run it?') or contains(., 'Où voulez-vous')]",
                )
            )
        )
        destination_text = driver.find_element(By.TAG_NAME, "body").text
        for label in ("Locale", "Vast.ai", "RunPod", "Modal"):
            assert label in destination_text, destination_text

        vast = wait.until(
            lambda d: _first_visible(
                d, "//button[contains(., 'Vast.ai') or contains(., 'Vast')]"
            )
        )
        vast.click()
        wait.until(
            EC.presence_of_element_located(
                (
                    By.XPATH,
                    "//*[contains(., 'Deploy guidato') or contains(., 'Guided deploy') or contains(., 'Déploiement guidé')]",
                )
            )
        )
        provider_text = driver.find_element(By.TAG_NAME, "body").text
        assert model_name in provider_text, (model_name, provider_text[:2500])
        provider_text_folded = provider_text.casefold()
        assert any(label.casefold() in provider_text_folded for label in ("Modello GPU", "GPU model", "Modèle GPU")), provider_text[:2500]
        assert any(label in provider_text for label in ("Modello già scelto", "Model already selected", "Modèle déjà choisi")), provider_text[:2500]
        print(f"e2e model provider OK: {model_name} -> Vast.ai")
        return 0
    finally:
        try:
            driver.quit()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
