# Repository Guidelines

## Project Structure & Module Organization
- `main.py` is the CLI entry point that loads menu state and runs batch image processing.
- `web_app.py` is the Streamlit Web entry point.
- Core logic is organized into packages:
  - `core/` for data models (config, container), constants, and processor chain.
  - `services/` for functional services (color, annotation, compose, cover, processing, etc.).
  - `ai/` for AI capabilities (preset suggestion, style pack, caption generation).
  - `cli/` for CLI interaction (menu system, setup, xiaohongshu CLI commands).
  - `web/` for Streamlit Web frontend (app, preview).
  - `utils_pkg/` for shared utility functions (ExifTool, image manipulation, file ops).
- Legacy shim files at root level (e.g., `color_service.py`, `processing_service.py`) re-export from new locations for backward compatibility.
- Runtime resources live in `fonts/`, `logos/`, `images/`, `input/`, and `output/`.
- Build/packaging files: `main.spec`, `build_win_pkg.spec`, and GitHub workflows in `.github/workflows/`.
- User-editable behavior is primarily controlled by `config.yaml`.

## Build, Test, and Development Commands
- Install dependencies:
  - `pip3 install -r requirements.txt`
- Local initialization (downloads ExifTool + deps):
  - `chmod +x scripts/install.sh && ./scripts/install.sh`
- Run locally (CLI):
  - `python3 main.py`
- Run locally (Web):
  - `streamlit run web_app.py`
- Build executable with PyInstaller:
  - `pyinstaller scripts/main.spec`
  - `pyinstaller scripts/build_win_pkg.spec` (Windows release package layout)

## Coding Style & Naming Conventions
- Use Python style with 4-space indentation and PEP 8-friendly naming.
- Prefer `snake_case` for functions/variables and `UPPER_SNAKE_CASE` for constants.
- Keep module names lowercase (matching current layout like `entity/image_processor.py`).
- Preserve existing config key names in `config.yaml`; treat them as public interface.

## Testing Guidelines
- There is currently no automated test suite in this repository.
- Validate changes with a manual run:
  - Place sample photos in `input/`, run `python3 main.py`, verify outputs in `output/`.
- For processing changes, test at least one case for each affected layout or option (e.g., white margin, logo, shadow).
- If adding tests, place them under `tests/` and use `pytest` naming (`test_*.py`).

## Commit & Pull Request Guidelines
- Follow the existing commit style seen in history: `feat: ...`, `fix: ...`, `chore: ...`.
- Keep commits focused (one logical change per commit) and include config/resource changes with related code.
- PRs should include:
  - What changed and why.
  - Manual verification steps (input used + expected output behavior).
  - Screenshots/sample output images for UI/watermark layout changes.
