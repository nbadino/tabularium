"""Smoke e2e del percorso archivio -> dataset -> preflight training."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

import requests
from PIL import Image
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

BASE = os.environ.get("TABULARIUM_E2E_URL", "http://127.0.0.1:8787").rstrip("/")


def wait_for(driver, condition, timeout: float = 15):
    return WebDriverWait(driver, timeout).until(condition)


def _chrome_options(root: Path) -> Options:
    options = Options()
    # Il binario si risolve così: TABULARIUM_CHROMIUM esplicito, poi i percorsi
    # noti di installazione Linux, poi il default di Selenium Manager (che
    # trova Chrome/Chromium su macOS, Windows e nei runner di CI).
    binary = os.environ.get("TABULARIUM_CHROMIUM", "")
    if not binary:
        for candidate in (
            "/usr/bin/chromium-browser",
            "/usr/bin/chromium",
            "/snap/bin/chromium",
            # macOS: nessun `chromium` nel PATH, i browser stanno nei bundle.
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        ):
            if Path(candidate).exists():
                binary = candidate
                break
    if binary:
        options.binary_location = binary
    for flag in (
        "--headless=new", "--no-sandbox", "--disable-setuid-sandbox",
        "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run",
        f"--user-data-dir={root / 'browser-profile'}",
    ):
        options.add_argument(flag)
    return options


def main() -> int:
    root = Path(tempfile.mkdtemp(prefix="tabularium-e2e-"))
    project_id: int | None = None
    driver = None
    stage = "bootstrap"
    try:
        Image.new("RGB", (900, 1300), (230, 230, 230)).save(root / "page.png")
        options = _chrome_options(root)
        driver = webdriver.Chrome(
            service=Service(os.environ.get("TABULARIUM_CHROMEDRIVER") or None),
            options=options,
        )
        driver.set_window_size(1440, 1000)

        # Il progetto sintetico si crea via API per evitare dipendenze dalla
        # tastiera del browser headless; il lavoro operativo successivo passa
        # dalla UI reale.
        stage = "project setup"
        created = requests.post(f"{BASE}/api/projects", json={"name": "E2E temporary project", "archive_dir": str(root)}, timeout=10)
        assert created.status_code == 201, created.text
        project_id = int(created.json()["id"])
        driver.get(f"{BASE}/progetti/{project_id}")
        wait_for(driver, EC.presence_of_element_located((By.XPATH, "//button[contains(., 'Scansiona archivio')]")))
        wait_for(driver, EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Scansiona archivio')]"))).click()
        wait_for(driver, EC.presence_of_element_located((By.XPATH, "//*[contains(., 'Registrati') or contains(., 'Registered') or contains(., 'Enregistrés')]")))

        # Annotazione minima via API per rendere il test deterministico.
        pages = requests.get(f"{BASE}/api/projects/{project_id}/pages", timeout=10).json()["items"]
        assert len(pages) == 1, pages
        page = pages[0]
        _put_ann = requests.put(
            f"{BASE}/api/pages/{page['id']}/annotations",
            json={"items": [
                {"label": "Table", "kind": "rect", "points": [[20, 20], [850, 600]], "content": "", "order_idx": 0, "confirmed": True},
                # La riga di un blocco `Table` mostra l'editor della griglia al
                # posto della textarea di trascrizione (v. `ContentPane`): per
                # verificare l'autosave del contenuto serve un blocco non
                # tabellare, altrimenti si scrive in una cella della griglia.
                # Contenuto non vuoto: l'approvazione più sotto pretende una
                # trascrizione per ogni blocco (`compute_readiness`). La UI lo
                # sostituisce comunque nel passo successivo.
                {"label": "Text", "kind": "rect", "points": [[20, 620], [850, 700]], "content": "Riga di prova.", "order_idx": 1, "confirmed": True},
            ]},
            timeout=10,
        )
        assert _put_ann.ok, _put_ann.text
        items = requests.get(f"{BASE}/api/pages/{page['id']}/annotations", timeout=10).json()["items"]
        block = next(item for item in items if item["label"] == "Table")
        text_block = next(item for item in items if item["label"] == "Text")
        _put_table = requests.put(
            f"{BASE}/api/blocks/{block['id']}/table",
            json={"rows": 2, "cols": 2, "cells": [{"r": 0, "c": 0, "rowspan": 1, "colspan": 1, "text": "Vessel"}]},
            timeout=10,
        )
        assert _put_table.ok, _put_table.text
        # page_type si imposta con la PATCH; lo stato va in approvazione con
        # l'endpoint dedicato: la transizione di stato è protetta (self-hosted).
        _patch_page = requests.patch(f"{BASE}/api/pages/{page['id']}", json={"page_type": "shipping"}, timeout=10)
        assert _patch_page.ok, _patch_page.text
        _approve = requests.post(f"{BASE}/api/pages/{page['id']}/approve", timeout=10)
        assert _approve.ok, _approve.text

        # Snapshot dataset via API (la UI viene comunque caricata e verificata
        # subito dopo; evita una dipendenza dal timing del select React).
        stage = "dataset UI"
        built = requests.post(f"{BASE}/api/projects/{project_id}/datasets/build", json={"split_ratio": 0.5, "approved_only": True}, timeout=30)
        assert built.status_code == 200, built.text
        driver.get(f"{BASE}/dataset")
        wait_for(driver, EC.presence_of_element_located((By.XPATH, "//*[contains(., 'Dataset')]")))
        report = requests.get(f"{BASE}/api/projects/{project_id}/datasets", timeout=10).json()
        assert report.get("built") is True and report["report"]["pages"]["with_blocks"] == 1

        # Editor tabellare e preview OTSL nel DOM.
        stage = "table editor DOM"
        driver.get(f"{BASE}/annotazione")
        wait_for(driver, lambda d: len(d.find_elements(By.CSS_SELECTOR, "select")) >= 2)
        Select(driver.find_elements(By.CSS_SELECTOR, "select")[1]).select_by_value(str(project_id))
        wait_for(driver, EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'page.png')]"))).click()
        wait_for(driver, EC.element_to_be_clickable((By.CSS_SELECTOR, "[data-block]"))).click()
        # Modifica controllata del contenuto: l'indicatore Salvato prova il
        # debounce autosave, non solo il salvataggio esplicito.
        # `aria-label` e non il primo `textarea` della pagina: le celle della
        # griglia sono textarea anch'esse e in DOM vengono prima. L'etichetta
        # del blocco non è tradotta, quindi il selettore regge in ogni lingua.
        content_box = (By.XPATH, "//textarea[contains(@aria-label, 'Text')]")
        textarea = wait_for(driver, EC.presence_of_element_located(content_box))
        textarea.click()
        textarea.clear()
        textarea.send_keys("Synthetic maritime record.")
        wait_for(driver, lambda d: "Synthetic maritime record." in d.find_element(*content_box).get_attribute("value"))
        # L'indicatore può sparire rapidamente; il contratto forte è il dato
        # persistito letto dal backend dopo il debounce.
        import time
        time.sleep(2)
        persisted = requests.get(f"{BASE}/api/pages/{page['id']}/annotations", timeout=10).json()["items"]
        assert any(
            item["id"] == text_block["id"] and item["content"] == "Synthetic maritime record."
            for item in persisted
        ), persisted
        # Nessun pulsante "apri editor tabella": per un blocco `Table` la riga
        # di `ContentPane` monta l'editor della griglia direttamente. La chiave
        # i18n `annotate.openTableEditor` è rimasta senza componente che la usi,
        # e questa attesa cercava un controllo che non esiste più.
        wait_for(driver, EC.element_to_be_clickable((By.XPATH, "//button[contains(., 'Salva griglia') or contains(., 'Save grid') or contains(., 'Enregistrer')]"))).click()
        wait_for(driver, EC.presence_of_element_located((By.XPATH, "//*[contains(., 'OTSL generato') or contains(., 'OTSL generated') or contains(., 'OTSL généré')]")))

        # Training center e preflight.
        stage = "training UI"
        driver.get(f"{BASE}/training")
        wait_for(driver, EC.presence_of_element_located((By.XPATH, "//*[contains(., 'Training')]")))
        preflight = requests.post(f"{BASE}/api/projects/{project_id}/training/preflight", json={}, timeout=10)
        assert preflight.status_code == 200, preflight.text
        print("e2e workflow OK: UI import/scan -> annotation -> dataset snapshot -> training preflight")
        return 0
    except Exception as exc:  # noqa: BLE001
        if driver is not None:
            buttons = [(b.text, b.is_enabled()) for b in driver.find_elements(By.TAG_NAME, "button")]
            inputs = [(i.get_attribute("value"), i.get_attribute("placeholder")) for i in driver.find_elements(By.CSS_SELECTOR, "input")]
            print(f"url={driver.current_url} inputs={inputs} buttons={buttons} body={driver.find_element(By.TAG_NAME, 'body').text[:400]!r}", file=sys.stderr)
        print(f"e2e workflow FAILED at {stage}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    finally:
        if driver is not None:
            driver.quit()
        if project_id is not None:
            try:
                requests.delete(f"{BASE}/api/projects/{project_id}?confirm=true", timeout=10)
            except requests.RequestException:
                pass
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
