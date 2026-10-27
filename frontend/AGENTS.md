# frontend — local rules

The root [`AGENTS.md`](../AGENTS.md) is the contract; this file adds only what is local.

- **The API payload is the source of truth for behaviour**: if a screen needs to know what
  a model supports, ask the payload (`mlx_available`, `generation_unsupported`,
  `workflow_fields`, `local.*`), do not compare `adapter_id` with a literal. The settings
  panel is generated from `workflow_fields`; a new model must not need a JSX change.
- **Every string goes through i18n** (`src/i18n/it.ts` is the shape; `en.ts`/`fr.ts` must
  match exactly — `src/i18n/i18n.test.ts` fails otherwise).
- **API paths** go through `src/lib/api.ts` or `src/lib/endpoints.ts`; `/api/...` written by
  hand breaks a sub-path deployment.
- **Run** `npm run typecheck && npm test` (or `../scripts/check.sh --fast` for the backend
  invariants only; the full `check.sh` includes the build).
