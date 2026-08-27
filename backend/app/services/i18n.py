"""i18n dei messaggi che il backend espone nell'interfaccia.

Il frontend invia `Accept-Language: it|en|fr`; questo modulo localizza i
messaggi che finiscono sullo schermo (dettagli degli errori HTTP, warning del
dataset, errori di readiness, reason della coda di annotazione, azioni di
valutazione e controlli del training). Il fallback è l'italiano, la lingua
storica delle stringhe utente di questo prodotto.

I cataloghi sono chiave -> template con segnaposto `{nome}`. La chiave è il
codice logico del messaggio; per i dettagli HTTPException il modulo offre
anche un adattatore inverso che, dato il testo italiano corrente, riconosce il
codice ed emette la traduzione richiesta.
"""
from __future__ import annotations

import re
from typing import Any

SUPPORTED = ("it", "en", "fr")
DEFAULT = "it"

_IT: dict[str, str] = {
    # --- dettagli API -------------------------------------------------------
    "page_not_found": "pagina non trovata",
    "project_not_found": "progetto non trovato",
    "block_not_found": "blocco non trovato",
    "source_not_found": "file sorgente non presente",
    "page_not_in_project": "pagina {id} non nel progetto",
    "page_not_found_in_project": "pagina non trovata nel progetto",
    "image_unavailable": "immagine sorgente non disponibile",
    "no_fields": "nessun campo da aggiornare",
    "invalid_status": "status non valido: {status}",
    "archive_dir_missing": "la cartella archivio non esiste",
    "archive_dir_invalid": "archive_dir non valida",
    "destructive": "operazione distruttiva: passa ?confirm=true per eliminare il progetto",
    "no_file_selected": "nessun file selezionato",
    "file_unsupported_fmt": "{name}: formato non supportato",
    "format_unsupported": "formato non supportato: internal|coco|html|page|alto",
    "block_no_points": "blocco senza punti",
    "block_no_points_valid": "blocco senza punti validi",
    "crop_empty": "crop vuoto",
    "tile_invalid": "tile non valido",
    "tile_out_image": "tile fuori immagine",
    "vllm_unreachable": "vLLM non raggiungibile ({url}): {exc}",
    "ocr_unavailable": (
        "Nessun motore OCR disponibile: installare rapidocr-onnxruntime o paddleocr "
        "(o impostare LLOYDS_OCR_ENGINE)."
    ),
    "model_unavailable": (
        "Modello base non raggiungibile su {url}: avvia il server di inferenza "
        "(./scripts/serve_model.sh) oppure usa il motore OCR."
    ),
    "ocr_engine_failed": (
        "Il motore OCR «{engine}» è installato ma non riesce a partire: {exc}. "
        "Installa rapidocr-onnxruntime nell'ambiente che esegue il backend, "
        "oppure forza il motore con LLOYDS_OCR_ENGINE."
    ),
    "deskew_has_blocks": (
        "la pagina ha {n} blocchi: il deskew cambia le coordinate. "
        "Passa ?confirm=true per eliminarli e ripartire."
    ),
    "page_not_ready": "pagina non pronta",
    # --- readiness -----------------------------------------------------------
    "no_blocks": "nessun blocco annotato",
    "geometry_missing": "blocco {id}: geometria mancante",
    "not_confirmed": "blocco {id}: non confermato",
    "empty_transcription": "blocco {id}: trascrizione vuota",
    "table_grid_missing": "blocco {id}: griglia tabella assente",
    "table_grid_invalid": "blocco {id}: griglia non valida ({exc})",
    "gold_review": "gold set: seconda revisione indipendente obbligatoria",
    # --- coda di annotazione --------------------------------------------------
    "review_low_conf": "rivedi {n} bozze OCR a bassa confidenza",
    "confirm_ocr": "conferma o correggi le bozze OCR",
    "annotate_structure": "annota struttura e ordine di lettura",
    "quality_check": "esegui il controllo qualità",
    "already_worked": "campione già lavorato",
    # --- scansione -------------------------------------------------------------
    "no_pages_rendered": "nessuna pagina renderizzata",
    # --- dataset builder -------------------------------------------------------
    "gold_excluded": "{n} pagine gold escluse da train/validation",
    "page_image_unavailable_w": "pagina {id}: immagine sorgente non disponibile",
    "crop_failed": "blocco {id}: crop fallito ({exc})",
    "points_missing": "blocco {id}: punti mancanti, escluso dal layout",
    "bbox_invalid": "blocco {id}: bbox non valida ({exc}), escluso dal layout",
    "empty_transcription_skip": "blocco {id} ({label}): trascrizione vuota, saltato",
    "table_grid_missing_skip": "blocco {id} (Table): griglia assente, saltato",
    "table_error": "blocco {id} (Table): {exc}",
    "table_band_failed": "blocco {id} (Table): generazione banda fallita ({exc})",
    "table_band_no_boundaries": "blocco {id} (Table): bande non generate perché mancano confini di riga verificati",
    "formula_empty": "blocco {id} (Formula): contenuto vuoto",
    # --- valutazione ------------------------------------------------------------
    "inference_failed": "pagina {id}: inferenza fallita ({exc})",
    "table_error_w": "tabella blocco {id}: {exc}",
    "text_error_w": "testo blocco {id}: {exc}",
    "vllm_ping": (
        "server vLLM non raggiungibile a {url}/models — i risultati saranno "
        "invisibili finché il modello non è servito"
    ),
    "no_pages_annotated": "nessuna pagina annotata",
    "no_val_pages": "nessuna pagina nel val split (aumenta la dimensione del dataset)",
    "otsl_unparsable": "OTSL non parsabile",
    "action_add_similar": "aggiungi esempi simili: layout non rilevato",
    "action_review_order": "rivedi ordine di lettura",
    "action_fix_cer": "correggi trascrizioni con CER alta",
    "action_review_tables": "rivedi queste tabelle complesse",
    # --- training preflight -------------------------------------------------------
    "missing_train": "manca {name}: eseguire prima la build del dataset",
    "unreadable_train": "impossibile leggere {name}: {exc}",
    "min_train": "servono almeno 2 campioni di training",
    "empty_val": "validation vuota: serve almeno un campione indipendente prima del training",
    "empty_val_w": "validation vuota: le metriche di generalizzazione non saranno affidabili",
    "no_table_train_w": "nessun campione tabella nel training: il run non migliorerà l'estrazione OTSL",
    "repo_not_configured": "LLOYDS_TRAIN_REPO non configurato",
    "repo_invalid": "repo MonkeyOCRv2 non valido: {path}",
    "python_not_found": "python di training non trovato: {path}",
    "conda_not_found": "conda non trovato e LLOYDS_TRAIN_PYTHON non configurato",
    "no_gpu_w": "nessuna GPU NVIDIA rilevata: il training MonkeyOCRv2 richiede CUDA",
    "gpu_missing": "GPU richieste non disponibili: {list}",
    "gpu_busy": "GPU {index} occupata: liberi {free} GB su {total} GB; arresta vLLM o gli altri processi CUDA",
    "gpu_low_vram_w": "GPU {index}: solo {free} GB liberi; riduci batch/pixel o libera la GPU",
    "disk_blocking": "spazio disco insufficiente per un run riproducibile: {gb} GB liberi",
    "low_disk": "spazio disco residuo basso: {gb} GB",
    "disk_unknown_w": "impossibile verificare lo spazio disco",
    # --- OCR / prelabel ------------------------------------------------------------
    "image_not_available": "immagine non disponibile",
}

_EN: dict[str, str] = {
    "page_not_found": "page not found",
    "project_not_found": "project not found",
    "block_not_found": "block not found",
    "source_not_found": "source file not found",
    "page_not_in_project": "page {id} not in project",
    "page_not_found_in_project": "page not found in project",
    "image_unavailable": "source image not available",
    "no_fields": "no fields to update",
    "invalid_status": "invalid status: {status}",
    "archive_dir_missing": "the archive folder does not exist",
    "archive_dir_invalid": "invalid archive_dir",
    "destructive": "destructive operation: pass ?confirm=true to delete the project",
    "no_file_selected": "no file selected",
    "file_unsupported_fmt": "{name}: unsupported format",
    "format_unsupported": "unsupported format: internal|coco|html|page|alto",
    "block_no_points": "block without points",
    "block_no_points_valid": "block without valid points",
    "crop_empty": "empty crop",
    "tile_invalid": "invalid tile",
    "tile_out_image": "tile outside the image",
    "vllm_unreachable": "vLLM unreachable ({url}): {exc}",
    "ocr_unavailable": (
        "No OCR engine available: install rapidocr-onnxruntime or paddleocr "
        "(or set LLOYDS_OCR_ENGINE)."
    ),
    "model_unavailable": (
        "Base model unreachable at {url}: start the inference server "
        "(./scripts/serve_model.sh) or use the OCR engine."
    ),
    "ocr_engine_failed": (
        "The OCR engine “{engine}” is installed but fails to start: {exc}. "
        "Install rapidocr-onnxruntime in the environment running the backend, "
        "or force the engine with LLOYDS_OCR_ENGINE."
    ),
    "deskew_has_blocks": (
        "the page has {n} blocks: deskew changes the coordinates. "
        "Pass ?confirm=true to delete them and start over."
    ),
    "page_not_ready": "page not ready",
    "no_blocks": "no annotated blocks",
    "geometry_missing": "block {id}: missing geometry",
    "not_confirmed": "block {id}: not confirmed",
    "empty_transcription": "block {id}: empty transcription",
    "table_grid_missing": "block {id}: missing table grid",
    "table_grid_invalid": "block {id}: invalid grid ({exc})",
    "gold_review": "gold set: a second independent review is required",
    "review_low_conf": "review {n} low-confidence OCR drafts",
    "confirm_ocr": "confirm or correct the OCR drafts",
    "annotate_structure": "annotate structure and reading order",
    "quality_check": "run the quality check",
    "already_worked": "sample already done",
    "no_pages_rendered": "no page rendered",
    "gold_excluded": "{n} gold pages excluded from train/validation",
    "page_image_unavailable_w": "page {id}: source image not available",
    "crop_failed": "block {id}: crop failed ({exc})",
    "points_missing": "block {id}: missing points, excluded from layout",
    "bbox_invalid": "block {id}: invalid bbox ({exc}), excluded from layout",
    "empty_transcription_skip": "block {id} ({label}): empty transcription, skipped",
    "table_grid_missing_skip": "block {id} (Table): missing grid, skipped",
    "table_error": "block {id} (Table): {exc}",
    "table_band_failed": "block {id} (Table): band generation failed ({exc})",
    "table_band_no_boundaries": "block {id} (Table): bands not generated because verified row boundaries are missing",
    "formula_empty": "block {id} (Formula): empty content",
    "inference_failed": "page {id}: inference failed ({exc})",
    "table_error_w": "table block {id}: {exc}",
    "text_error_w": "text block {id}: {exc}",
    "vllm_ping": (
        "vLLM server unreachable at {url}/models — results will be invisible "
        "until the model is served"
    ),
    "no_pages_annotated": "no annotated pages",
    "no_val_pages": "no pages in the val split (increase the dataset size)",
    "otsl_unparsable": "OTSL not parseable",
    "action_add_similar": "add similar examples: layout not detected",
    "action_review_order": "review reading order",
    "action_fix_cer": "fix transcriptions with high CER",
    "action_review_tables": "review these complex tables",
    "missing_train": "missing {name}: build the dataset first",
    "unreadable_train": "cannot read {name}: {exc}",
    "min_train": "at least 2 training samples are required",
    "empty_val": "validation is empty: at least one independent sample is required before training",
    "empty_val_w": "empty validation: generalization metrics will be unreliable",
    "no_table_train_w": "no table samples in training: this run will not improve OTSL extraction",
    "repo_not_configured": "LLOYDS_TRAIN_REPO is not configured",
    "repo_invalid": "invalid MonkeyOCRv2 repo: {path}",
    "python_not_found": "training python not found: {path}",
    "conda_not_found": "conda not found and LLOYDS_TRAIN_PYTHON is not configured",
    "no_gpu_w": "no NVIDIA GPU detected: MonkeyOCRv2 training requires CUDA",
    "gpu_missing": "requested GPUs not available: {list}",
    "gpu_busy": "GPU {index} is busy: {free} GB free out of {total} GB; stop vLLM or other CUDA processes",
    "gpu_low_vram_w": "GPU {index}: only {free} GB free; reduce batch/pixels or free the GPU",
    "disk_blocking": "insufficient disk space for a reproducible run: {gb} GB free",
    "low_disk": "low remaining disk space: {gb} GB",
    "disk_unknown_w": "cannot check the disk space",
    "image_not_available": "image not available",
}

_FR: dict[str, str] = {
    "page_not_found": "page introuvable",
    "project_not_found": "projet introuvable",
    "block_not_found": "bloc introuvable",
    "source_not_found": "fichier source introuvable",
    "page_not_in_project": "page {id} absente du projet",
    "page_not_found_in_project": "page introuvable dans le projet",
    "image_unavailable": "image source indisponible",
    "no_fields": "aucun champ à mettre à jour",
    "invalid_status": "statut invalide : {status}",
    "archive_dir_missing": "le dossier d'archives n'existe pas",
    "archive_dir_invalid": "archive_dir invalide",
    "destructive": "opération destructive : passez ?confirm=true pour supprimer le projet",
    "no_file_selected": "aucun fichier sélectionné",
    "file_unsupported_fmt": "{name} : format non pris en charge",
    "format_unsupported": "format non pris en charge : internal|coco|html|page|alto",
    "block_no_points": "bloc sans points",
    "block_no_points_valid": "bloc sans points valides",
    "crop_empty": "recadrage vide",
    "tile_invalid": "tuile invalide",
    "tile_out_image": "tuile hors de l'image",
    "vllm_unreachable": "vLLM injoignable ({url}) : {exc}",
    "ocr_unavailable": (
        "Aucun moteur OCR disponible : installez rapidocr-onnxruntime ou "
        "paddleocr (ou définissez LLOYDS_OCR_ENGINE)."
    ),
    "model_unavailable": (
        "Modèle de base injoignable sur {url} : démarrez le serveur d'inférence "
        "(./scripts/serve_model.sh) ou utilisez le moteur OCR."
    ),
    "ocr_engine_failed": (
        "Le moteur OCR « {engine} » est installé mais ne démarre pas : {exc}. "
        "Installez rapidocr-onnxruntime dans l'environnement qui exécute le "
        "backend, ou forcez le moteur avec LLOYDS_OCR_ENGINE."
    ),
    "deskew_has_blocks": (
        "la page contient {n} blocs : le redressement change les coordonnées. "
        "Passez ?confirm=true pour les supprimer et repartir de zéro."
    ),
    "page_not_ready": "page non prête",
    "no_blocks": "aucun bloc annoté",
    "geometry_missing": "bloc {id} : géométrie manquante",
    "not_confirmed": "bloc {id} : non confirmé",
    "empty_transcription": "bloc {id} : transcription vide",
    "table_grid_missing": "bloc {id} : grille de tableau absente",
    "table_grid_invalid": "bloc {id} : grille invalide ({exc})",
    "gold_review": "gold set : une seconde révision indépendante est obligatoire",
    "review_low_conf": "revoyez {n} brouillons OCR à faible confiance",
    "confirm_ocr": "confirmez ou corrigez les brouillons OCR",
    "annotate_structure": "annotez la structure et l'ordre de lecture",
    "quality_check": "effectuez le contrôle qualité",
    "already_worked": "échantillon déjà traité",
    "no_pages_rendered": "aucune page rendue",
    "gold_excluded": "{n} pages gold exclues de train/validation",
    "page_image_unavailable_w": "page {id} : image source indisponible",
    "crop_failed": "bloc {id} : recadrage impossible ({exc})",
    "points_missing": "bloc {id} : points manquants, exclu du layout",
    "bbox_invalid": "bloc {id} : bbox invalide ({exc}), exclue du layout",
    "empty_transcription_skip": "bloc {id} ({label}) : transcription vide, ignoré",
    "table_grid_missing_skip": "bloc {id} (Table) : grille absente, ignoré",
    "table_error": "bloc {id} (Table) : {exc}",
    "table_band_failed": "bloc {id} (Table) : échec de génération de la bande ({exc})",
    "table_band_no_boundaries": "bloc {id} (Table) : bandes non générées car les limites de lignes vérifiées sont absentes",
    "formula_empty": "bloc {id} (Formule) : contenu vide",
    "inference_failed": "page {id} : inférence impossible ({exc})",
    "table_error_w": "tableau bloc {id} : {exc}",
    "text_error_w": "texte bloc {id} : {exc}",
    "vllm_ping": (
        "serveur vLLM injoignable à {url}/models — les résultats resteront "
        "invisibles tant que le modèle n'est pas servi"
    ),
    "no_pages_annotated": "aucune page annotée",
    "no_val_pages": "aucune page dans le split de validation (augmentez la taille du dataset)",
    "otsl_unparsable": "OTSL non analysable",
    "action_add_similar": "ajoutez des exemples similaires : layout non détecté",
    "action_review_order": "revoyez l'ordre de lecture",
    "action_fix_cer": "corrigez les transcriptions à CER élevé",
    "action_review_tables": "revoyez ces tableaux complexes",
    "missing_train": "fichier manquant {name} : construisez d'abord le dataset",
    "unreadable_train": "impossible de lire {name} : {exc}",
    "min_train": "il faut au moins 2 échantillons d'entraînement",
    "empty_val": "validation vide : au moins un échantillon indépendant est requis avant l'entraînement",
    "empty_val_w": "validation vide : les métriques de généralisation ne seront pas fiables",
    "no_table_train_w": "aucun échantillon de tableau dans l'entraînement : ce run n'améliorera pas l'extraction OTSL",
    "repo_not_configured": "LLOYDS_TRAIN_REPO n'est pas configuré",
    "repo_invalid": "dépôt MonkeyOCRv2 invalide : {path}",
    "python_not_found": "python d'entraînement introuvable : {path}",
    "conda_not_found": "conda introuvable et LLOYDS_TRAIN_PYTHON non configuré",
    "no_gpu_w": "aucune GPU NVIDIA détectée : l'entraînement MonkeyOCRv2 nécessite CUDA",
    "gpu_missing": "GPU demandées indisponibles : {list}",
    "gpu_busy": "GPU {index} occupée : {free} Go libres sur {total} Go ; arrêtez vLLM ou les autres processus CUDA",
    "gpu_low_vram_w": "GPU {index} : seulement {free} Go libres ; réduisez le batch/les pixels ou libérez la GPU",
    "disk_blocking": "espace disque insuffisant pour un run reproductible : {gb} Go libres",
    "low_disk": "espace disque résiduel faible : {gb} Go",
    "disk_unknown_w": "impossible de vérifier l'espace disque",
    "image_not_available": "image indisponible",
}

CATALOG: dict[str, dict[str, str]] = {"it": _IT, "en": _EN, "fr": _FR}


def parse_lang(header: str | None) -> str:
    """Estrae `it|en|fr` da un header Accept-Language (fallback: italiano)."""
    if not header:
        return DEFAULT
    for part in header.split(","):
        code = part.strip().split(";")[0].strip().lower()
        base = code.split("-")[0]
        if base in SUPPORTED:
            return base
    return DEFAULT


def msg(key: str, lang: str | None = None, **kwargs: Any) -> str:
    """Rende il messaggio localizzato, interpolando i segnaposto `{nome}`."""
    lang = lang if lang in SUPPORTED else DEFAULT
    template = CATALOG[lang].get(key) or _IT.get(key) or key
    if not kwargs:
        return template
    try:
        return template.format(**kwargs)
    except (KeyError, IndexError, ValueError):
        return template


# --- adattatore inverso per i dettagli HTTP --------------------------------
# Dato il `detail` italiano corrente (stringa), riconosce il codice e produce
# la traduzione per la lingua della richiesta. Usato dal handler di main.py.
def _template_pattern(template: str) -> re.Pattern[str] | None:
    try:
        parts = re.split(r"\{(\w+)\}", template)
    except re.error:
        return None
    if len(parts) == 1:
        return re.compile(re.escape(parts[0]) + "$")
    pattern = ""
    groups: list[str] = []
    for i, part in enumerate(parts):
        if i % 2 == 0:  # testo letterale
            pattern += re.escape(part)
        else:  # nome del gruppo
            groups.append(part)
            pattern += rf"(?P<{part}>.+?)"
    try:
        return re.compile(pattern + "$")
    except re.error:
        return None


_COMPILED: list[tuple[str, re.Pattern[str]]] = [
    (code, pat)
    for code, template in _IT.items()
    if (pat := _template_pattern(template)) is not None
]


def localize_detail(detail: str, lang: str | None = None) -> str:
    """Traduce un `detail` HTTPException scritto in italiano."""
    lang = lang if lang in SUPPORTED else DEFAULT
    if lang == DEFAULT:
        return detail
    for code, pattern in _COMPILED:
        match = pattern.match(detail)
        if match:
            return msg(code, lang, **match.groupdict())
    return detail
