"""Fixture globali: DB temporaneo isolato per ogni run di test.

Deve essere importato prima dei moduli app: imposta LLOYDS_ROOT su una
directory temporanea così config.py e db.py puntano a un DB pulito.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

# Temp root condivisa per tutta la sessione di test.
_TMP_ROOT = Path(tempfile.mkdtemp(prefix="lloyds-lab-tests-"))
os.environ["LLOYDS_ROOT"] = str(_TMP_ROOT)
