# 1. Mission

> Extracted from `AGENTS.md` (October 2026). This is a record of verified
> facts, not a rule book: the rules are in `AGENTS.md`. If this file disagrees
> with the code or with a test, the code wins — fix this file in the same
> commit. Sections are loaded on demand; do not paste them into a prompt.

One tool that takes the user from an archive of images to structured text and, when needed,
to a fine-tuned model. The primary UX does not depend on infrastructure: model and provider are
chosen once, then recognition, review and export work the same locally, on Vast.ai, on Modal or
on any compatible endpoint.

1. **Register** archive pages (scans, multi-page PDFs) with metadata (date, issue, page).
2. **Recognize** one or many pages in persistent sessions, exportable or reviewable later,
   even with inference switched off.
3. **Annotate** the document structure in a guided, very detailed way: semantic blocks +
   **complex tables with merged cells** + **reading order** + **transcription**.
4. **Export** results and datasets in the exact format each model's official training accepts
   (for MonkeyOCRv2: ms-swift JSONL, coordinates normalized to 0–1000, tables in OTSL).
5. **Train** open-weight models (LoRA or full SFT) by generating and launching the official
   scripts of each model, with live logs, loss and GPU monitoring.
6. **Evaluate** on unseen pages (layout/IoU, reading order, TEDS for tables, CER/WER for text),
   compare models side by side, and **iterate**.

Cross-cutting constraint: **cross-platform** (Windows/Linux/macOS) for the UI and data
preparation. Training needs an NVIDIA CUDA GPU (Linux; WSL2 on Windows), local or rented.

---
