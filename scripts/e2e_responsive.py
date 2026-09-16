"""Check primary routes for horizontal overflow at supported viewport sizes."""
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
ROUTES = ("/", "/risultati", "/archivio", "/modelli", "/progetti", "/annotazione", "/dataset", "/training", "/valutazione", "/playground")
VIEWPORTS = ((390, 844), (768, 900), (1440, 1000))


def main() -> int:
    requests.get(f"{BASE}/api/health", timeout=10).raise_for_status()
    root = Path(tempfile.mkdtemp(prefix="tabularium-responsive-e2e-"))
    options = Options()
    if binary := os.environ.get("TABULARIUM_CHROMIUM"):
        options.binary_location = binary
    for flag in ("--headless=new", "--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-first-run", f"--user-data-dir={root / 'profile'}"):
        options.add_argument(flag)
    driver = webdriver.Chrome(service=Service(os.environ.get("TABULARIUM_CHROMEDRIVER") or None), options=options)
    try:
        for width, height in VIEWPORTS:
            driver.set_window_size(width, height)
            for route in ROUTES:
                driver.get(f"{BASE}{route}")
                WebDriverWait(driver, 20).until(lambda d: d.find_element(By.TAG_NAME, "body").text.strip() != "")
                WebDriverWait(driver, 10).until(lambda d: d.execute_script("return document.readyState") == "complete")
                dimensions = driver.execute_script("return {width: innerWidth, scroll: document.documentElement.scrollWidth, body: document.body.scrollWidth}")
                assert dimensions["scroll"] <= dimensions["width"] + 2, (width, route, dimensions)
            print(f"{width}px responsive audit OK ({len(ROUTES)} routes)")
        return 0
    finally:
        driver.quit()
        shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
