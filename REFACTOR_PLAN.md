# Update Study Buddy Refactor Plan

## Goals
- Improve reliability and maintainability of the end-to-end update pipeline.
- Reduce config drift across scripts.
- Keep existing behavior stable while making failures easier to diagnose.

## Guiding Principles
- Small, reversible changes in priority order.
- Preserve current user workflow (`python run_full_update.py` + manual review pause).
- Prefer centralized configuration and shared utility functions over duplicated logic.

## Phase 0 — Baseline and Safety (Quick Wins)
1. **Create a central config module** (`config.py`) used by all scripts.
   - Move `PROJECT_URL`, `CDP_URL`, `MAX_UPLOAD_SIZE_MB`, and default tarball pattern here.
   - Support environment variable overrides (e.g., `NOTEBOOKLM_PROJECT_URL`).
2. **Normalize naming in docs and code**.
   - Standardize on `comparison_review.json` everywhere.
   - Remove references to legacy `comparison_report.json`.
3. **Add a preflight checker script** (`preflight.py`).
   - Validate Python version, Playwright availability, ffmpeg availability, CDP connectivity, and required input files.
   - Print actionable fixes.
4. **Add safer tar extraction utility**.
   - Replace direct `extractall` with path-validated extraction helper.

**Acceptance criteria:**
- No behavior changes in happy path.
- All scripts read core settings from one place.
- Docs and runtime outputs consistently mention the same review file name.

## Phase 1 — CLI and Workflow Reliability
1. **Add argparse-based CLI to each script**.
   - `extract_edx.py`: accept `--tar`, `--out`.
   - `organize_content.py`: accept `--extract-dir`, `--out-dir`.
   - `compare_sources.py`: accept `--review`, `--manifest`, `--current-sources`, `--apply`.
2. **Make tarball selection deterministic**.
   - If `--tar` absent, auto-detect latest `course*.tar.gz`; fail clearly on ambiguity.
3. **Improve orchestrator (`run_full_update.py`) output**.
   - Add elapsed time per step.
   - Print a concise summary table at end.
4. **Formalize exit codes**.
   - Distinguish configuration errors vs runtime processing errors.

**Acceptance criteria:**
- Workflow can be run with defaults or explicit arguments.
- Error messages specify exactly what to fix.

## Phase 2 — Shared Playwright Layer (High ROI)
1. **Introduce `notebooklm_client.py`** shared helper.
   - CDP connect/launch fallback.
   - Notebook readiness checks.
   - Common selectors and wait utilities.
2. **Unify localization-aware selectors**.
   - Centralize English/Swedish regex labels.
3. **Consolidate overlay/menu handling**.
   - One robust utility for dismissing overlays and selecting menu actions.
4. **Add structured logging hooks**.
   - Include source name, action, retry count, and failure reason.

**Acceptance criteria:**
- `export_current_sources.py`, `delete_agent.py`, and `upload_agent.py` share core browser logic.
- Reduced duplicated selector code and fewer brittle branches.

## Phase 3 — Data Model and Matching Quality
1. **Define typed schemas** (dataclasses or Pydantic-lite optional) for:
   - manifest entries,
   - review entries,
   - export source lists.
2. **Add schema validation** before apply.
   - Prevent malformed review files from causing partial destructive actions.
3. **Refine match scoring transparency**.
   - Persist per-feature score breakdown (name similarity, overlap, content boost).
4. **Optional safety mode**.
   - `--dry-run` for apply path to print intended delete/upload actions without executing.

**Acceptance criteria:**
- Review/apply pipeline fails fast on malformed JSON.
- Users can audit why each match was suggested.

## Phase 4 — Test Coverage and Tooling
1. **Add unit tests** for pure logic:
   - normalization,
   - word overlap,
   - scoring,
   - review generation.
2. **Add fixture-based tests** for extraction and organize transforms.
3. **Add lightweight integration checks**:
   - run compare on sample manifest/current_sources fixture.
4. **Add lint/format/type checks**:
   - `ruff`/`flake8`, `black`, optional `mypy`.

**Acceptance criteria:**
- CI-style local command validates core logic quickly.
- Regression risk reduced before UI automation runs.

## Phase 5 — Documentation and Operations
1. **Update README runbook** with:
   - preflight step,
   - CLI examples,
   - troubleshooting matrix by failure mode.
2. **Add `.env.example`** for configurable settings.
3. **Add `.gitignore` recommendations** for generated artifacts and large tarballs.
4. **Create `OPERATIONS.md`** for repeated course refresh workflow.

**Acceptance criteria:**
- New operator can run pipeline with minimal tribal knowledge.
- Generated artifacts are not accidentally committed.

## Suggested Implementation Order (2-week practical track)
- **Day 1–2:** Phase 0
- **Day 3–5:** Phase 1
- **Week 2 (first half):** Phase 2
- **Week 2 (second half):** Phase 3 + targeted tests from Phase 4
- **After stabilization:** Phase 5 docs polish

## Risks and Mitigations
- **Risk:** selector regressions in NotebookLM UI.
  - **Mitigation:** keep old selectors as fallback during transition.
- **Risk:** behavior drift while centralizing config.
  - **Mitigation:** migrate one script at a time with side-by-side constants until stable.
- **Risk:** accidental destructive apply actions.
  - **Mitigation:** add schema validation and `--dry-run` before broad rollout.

## Definition of Done for Refactor Program
- Single configuration source.
- Deterministic CLI behavior for all scripts.
- Shared browser automation layer.
- Validated review/apply data schemas.
- Baseline automated tests for core logic.
- Updated docs reflecting actual runtime behavior.
