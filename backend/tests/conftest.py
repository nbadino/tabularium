"""Fixture globali: DB temporaneo isolato per ogni run di test.

Deve essere importato prima dei moduli app: imposta TABULARIUM_ROOT su una
directory temporanea così config.py e db.py puntano a un DB pulito.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Temp root condivisa per tutta la sessione di test.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="tabularium-tests-"))
os.environ["TABULARIUM_ROOT"] = str(_TMP_ROOT)
# I test della suite storica sono single-user: la modalità auth va disattivata
# PRIMA dell'import dei moduli app (config.AUTH_MODE viene letto a import).
os.environ["TABULARIUM_AUTH"] = "off"
