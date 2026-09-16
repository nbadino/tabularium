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
        # Repeat the complete model-first interaction for every destination.
        # A fresh navigation prevents state from a previous provider from
        # silently making the next case pass.
        destinations = (
            ("Locale", "//button[contains(., 'Locale') or contains(., 'Local') or contains(., 'Local')]") ,
            ("Vast.ai", "//button[contains(., 'Vast.ai') or contains(., 'Vast')]") ,
            ("RunPod", "//button[contains(., 'RunPod')]") ,
            ("Modal", "//button[contains(., 'Modal')]") ,
            ("MANUALE", "//button[contains(., 'endpoint') or contains(., 'Manual') or contains(., 'MANUALE') or contains(., 'MANUAL')]") ,
        )
        checked = []
        for expected_label, destination_xpath in destinations:
            driver.get(f"{BASE}/modelli")
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
            wait.until(
                lambda d: _first_visible(
                    d,
                    "//button[contains(., 'Scegli il modello') or contains(., 'Choose model') or contains(., 'Choisir')]",
                )
            ).click()
            wait.until(
                lambda d: _first_visible(
                    d,
                    "//button[contains(., 'Continua con questo modello') or contains(., 'Continue with this model') or contains(., 'Continuer avec ce modèle') or contains(., 'Scegli questo modello')]",
                )
            ).click()
            wait.until(
                EC.presence_of_element_located(
                    (By.XPATH, "//*[contains(., 'Dove vuoi eseguirlo?') or contains(., 'Where do you want to run it?') or contains(., 'Où voulez-vous')]")
                )
            )
            destination = wait.until(lambda d: _first_visible(d, destination_xpath))
            assert destination is not None, (expected_label, d.find_element(By.TAG_NAME, "body").text[:1500])
            destination.click()
            wait.until(lambda d: model_name in d.find_element(By.TAG_NAME, "body").text)
            body = driver.find_element(By.TAG_NAME, "body").text
            if expected_label == "Locale":
                assert any(label.casefold() in body.casefold() for label in ("Porta locale", "Local port", "Port locale")), body[:2500]
            else:
                assert expected_label.casefold() in body.casefold(), (expected_label, body[:2500])
                body_folded = body.casefold()
                assert any(label.casefold() in body_folded for label in ("Modello già scelto", "Modello da deployare", "Model already selected", "Model to deploy", "Modèle déjà choisi", "Modèle à déployer")), body[:2500]
            checked.append(expected_label)
        print(f"e2e model provider OK: {model_name} -> {', '.join(checked)}")
        return 0
    finally:
        try:
            driver.quit()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
