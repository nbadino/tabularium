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
    """Verifica che la destinazione si scelga in pagina e che l'app dica cosa
    può fare *questa* macchina.

    Il test è indipendente dallo stato: non presuppone che nessun modello sia
    già configurato. Dipendere da «nessuna destinazione scelta» lo rendeva
    verde solo su una macchina appena installata, e rosso su quella di chi ci
    lavora.
    """
    requests.get(f"{BASE}/api/health", timeout=10).raise_for_status()
    info = requests.get(f"{BASE}/api/system/info", timeout=10).json()
    compute = info["capabilities"]["local_compute"]
    usable = compute["usable_runtimes"]

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

        # La libreria è la pagina, non una modale: il catalogo è già presente.
        assert wait.until(
            lambda d: _first_visible(
                d,
                "//*[contains(., 'Catalogo modelli') or contains(., 'Model catalog') or contains(., 'Catalogue des modèles')]",
            )
        ) is not None

        # La scelta della destinazione si apre dalla testata, in un modulo in
        # pagina, e dichiara prima cosa può fare questa macchina.
        wait.until(
            lambda d: _first_visible(
                d,
                "//button[contains(., 'Cambia destinazione') or contains(., 'Change destination') or contains(., 'Changer la destination')]",
            )
        ).click()
        wait.until(
            EC.presence_of_element_located(
                (By.XPATH, "//*[contains(., 'Dove vuoi eseguirlo?') or contains(., 'Where do you want to run it?') or contains(., 'Où voulez-vous')]")
            )
        )
        body = driver.find_element(By.TAG_NAME, "body").text
        assert compute["arch"] in body or compute["platform"] in body, body[:1200]

        local = wait.until(
            lambda d: _first_visible(
                d,
                "//button[contains(., 'Locale') or contains(., 'Local')]",
            )
        )
        assert local is not None
        if not usable:
            # Nessun runtime locale: la voce è disabilitata e la causa è
            # scritta, invece di far fallire il click.
            assert not local.is_enabled(), "«Locale» offerto su una macchina che non può servire"
            assert any(
                fragment.casefold() in body.casefold()
                for fragment in ("non può servire modelli in locale", "cannot serve models locally", "ne peut pas servir")
            ), body[:1200]
        else:
            assert local.is_enabled()

        checked = [f"scegli_destinazione (locale={'sì' if usable else 'no'})"]

        # Le destinazioni remote: la scheda del provider si apre con il
        # modello già scelto, senza rifare la selezione.
        for expected_label, destination_xpath in (
            ("Vast.ai", "//button[contains(., 'Vast.ai') or contains(., 'Vast')]"),
            ("RunPod", "//button[contains(., 'RunPod')]"),
            ("Modal", "//button[contains(., 'Modal')]"),
            ("MANUALE", "//button[contains(., 'endpoint') or contains(., 'Manual') or contains(., 'MANUALE') or contains(., 'MANUAL')]"),
        ):
            driver.get(f"{BASE}/modelli")
            wait.until(EC.presence_of_element_located((By.TAG_NAME, "h1")))
            wait.until(
                lambda d: _first_visible(
                    d,
                    "//button[contains(., 'Cambia destinazione') or contains(., 'Change destination') or contains(., 'Changer la destination')]",
                )
            ).click()
            destination = wait.until(lambda d: _first_visible(d, destination_xpath))
            assert destination is not None, (
                expected_label,
                driver.find_element(By.TAG_NAME, "body").text[:1500],
            )
            destination.click()
            wait.until(
                lambda d: expected_label.casefold()
                in d.find_element(By.TAG_NAME, "body").text.casefold()
            )
            body = driver.find_element(By.TAG_NAME, "body").text
            assert any(
                label.casefold() in body.casefold()
                for label in (
                    "Modello già scelto",
                    "Modello da deployare",
                    "Model already selected",
                    "Model to deploy",
                    "Modèle déjà choisi",
                    "Modèle à déployer",
                )
            ), body[:2500]
            checked.append(expected_label)

        print(f"e2e model provider OK: {compute['platform']}/{compute['arch']} -> {', '.join(checked)}")
        return 0
    finally:
        try:
            driver.quit()
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
