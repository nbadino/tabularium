# Changelog

Le modifiche rilevanti di Tabularium sono raccolte qui. Il formato segue
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) e le versioni usano
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Quickstart più chiaro per macOS, Linux e Windows.
- Versione unica per backend e frontend, aggiornata da `scripts/version.py`.
- Unlimited-OCR ha un percorso locale su Apple Silicon: da `mlx-vlm` 0.6.4 esiste il
  modello `unlimited_ocr` con la R-SWA di upstream. Due varianti selezionabili per
  modello (`bf16`, `8bit`) e un guard anti-loop (`repetition_penalty` 1.1, finestra
  128), perché il processore n-gram ufficiale non è in `mlx-vlm`.
- Server MLX dedicato per Unlimited-OCR: il server `mlx_vlm.server` non inietta
  l'immagine per questa architettura, quindi Tabularium espone la stessa interfaccia
  OpenAI ma sull'API `mlx_vlm.generate` che funziona.

### Changed
- README riorganizzato con istruzioni di primo avvio e comandi di release.
- Hub Modelli: scegliendo «Questo Mac» per un modello con più varianti MLX si chiede
  quale quantizzazione usare; la scelta è editabile anche nel passo Pesi e in
  Impostazioni → Modelli.

## [0.1.0]

### Added
- Flusso di riconoscimento, annotazione, esportazione dataset, training e valutazione.
