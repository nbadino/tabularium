# Changelog

Notable changes to Tabularium. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions use
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-10-18

### Added
- **Releases with the interface prebuilt.** Installing from a GitHub release needs
  Python only: no Node.js, no git. Archives come with SHA-256 checksums.
- **Documentation site** at <https://nbadino.github.io/tabularium/>, in English:
  installation, configuration, supported models, local inference, cloud GPUs,
  fine-tuning, teams, troubleshooting and development.
- **Annotator / reviewer workflow.** Each page can be assigned to a distinct annotator
  and reviewer, with personal "to annotate" and "to review" queues, submission,
  change requests and approval checked by the server. An edit after approval
  invalidates it.
- **Team features.** A Share dialog for people and permissions (edit or read only),
  a read-only project view, ownership transfer and an activity feed. Admins can
  invite people by link; invited users choose their own password, and accounts
  created or reset by an admin must change it at first login. Users can edit their
  own profile.
- **Live presence** in the studio: see who else is on the page and who is editing,
  and load their changes without reloading. Unsaved work is never discarded.
- **Multi-model benchmark** on a dedicated page: pick models and approved pages, and
  compare layout (IoU 0.5 / 0.75), reading order, table structure and CER/WER side
  by side. Local models are started and stopped one at a time without touching the
  active inference profile; results appear as each model finishes, and a
  tables-only mode ignores layout and labels. Reports are written to disk, never to
  the database. Pages can be approved in bulk.
- **Training recipes per model**: every open-weight model declares its toolchain,
  official documentation, training types, data format and maturity. Wired through
  ms-swift: MonkeyOCRv2, Qwen3-VL, Unlimited-OCR, DeepSeek-OCR-2, GLM-OCR,
  MinerU2.5, dots.ocr. A setup script prepares a GPU host for training, and on
  Vast.ai renting an instance can prepare it automatically.
- **Gemini 4 Argon** through the OpenCode gateway, as an external endpoint: one
  END2END generation returns header, issue number, date and the table in OTSL, so
  it works in Recognize and in the Playground.
- **Unlimited-OCR on Apple Silicon** through MLX (`mlx-vlm` ≥ 0.6.4), with `bf16`
  and `8bit` variants, an anti-loop guard and a dedicated MLX server.
- **Native folder picker**: Browse opens the system folder dialog when the server
  runs on your computer, and falls back to the web browser otherwise.
- Table schema hints per project, passed to models that accept them.
- A single version for backend and frontend, managed by `scripts/version.py`.

### Changed
- README rewritten for researchers; detailed guides moved to the documentation site.
  Italian-only guides were replaced by English ones.
- Studio: views always reachable, Recognize always visible, secondary commands under
  Options; changing page or project waits for canvas and drafts to be saved. The
  table workspace now runs on Univer.
- Models hub: choosing "This Mac" for a model with several MLX variants asks which
  quantization to use; the choice can also be changed later in Settings → Models.
- Studio, Results and Projects: clearer page status at a glance; the transcription
  view no longer traps you without a way back.

### Fixed
- MLX variants and memory: the "runs on this Mac" verdict only looked at the default
  variant, so on an 8 GB Mac Unlimited-OCR was reported as not fitting even though
  the 8-bit variant does. Each variant now declares its own size, and without a
  saved choice the largest one that fits is used.
- Unlimited-OCR on 8 GB Macs: MLX's buffer cache held on to freed memory and the GPU
  timed out on the first page. Up to 12 GB the server now starts with the cache
  off and clears it after each page.
- Recognition with a local model: if the server dies mid-page, the session restarts
  it on the same port and retries that page once, instead of failing it and every
  page after it.
- Approved pages and model drafts: re-running recognition on an approved page added
  unconfirmed drafts that evaluation and export treated as ground truth. On approved
  pages only confirmed blocks now count.
- The backend setup script no longer warns that OCR is missing when it is installed.

## [0.1.0]

### Added
- Recognition, annotation, dataset export, training and evaluation workflow.
