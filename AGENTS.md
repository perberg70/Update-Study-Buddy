# AGENTS.md

## 1. Project Context & Stack
- **Repository:** Update-Study-Buddy
- **Purpose:** Synchronize edX course exports with Google NotebookLM.
- **Language / Runtime:** Python 3.10+
- **Dependency Management:** `pip` with `requirements.txt` (or `pyproject.toml` if present).

---

## 2. Environment & Commands
Execute commands from the repository root:

- **Install Dependencies:** `pip install -r requirements.txt`
- **Run the Application:** `python -m src.main` (or the primary entry point script)
- **Code Style & Linting:** `flake8` or `black --check .` (if installed)

---

## 3. Testing Workflows
Always run the test suite before finalizing any changes:

- **Run Full Test Suite:** `python tests/run_all.py`
- **Run Pytest (if used):** `pytest`

---

## 4. Directory Structure
```text
tests/
└── run_all.py       # Test suite runner
src/                 # Main application source code (edX sync and NotebookLM integration)
```

---

## 5. Architectural Rules & Patterns
- **Typing:** Use Python type hints (`typing` module / built-in types) for all new functions and class methods.
- **Error Handling:** Gracefully handle network timeouts and API errors when communicating with edX and NotebookLM endpoints.
- **Environment Variables:** Never hardcode credentials, API tokens, or session cookies into source files; load them via environment variables or `.env`.

---

## 6. Agent Guardrails & Constraints
- **Scope:** Only modify files directly related to the issue objective.
- **Protected Files:** Do not modify `.github/workflows/` or environment templates unless explicitly instructed.
- **Verification:** Every change must pass `python tests/run_all.py` before opening a pull request.
