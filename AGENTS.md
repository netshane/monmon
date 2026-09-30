# CLAUDE.md

## Architecture
- `docs/architecture.md` - read this first when implementing a feature or
  reviewing the application.  It maps the layers, the `run-scheduled` control
  flow, the data model, and names the exact files to change for common
  extensions (new test type, new notification channel, new cli command, report
  data).

## Coding Practices
- `locallib` - all application modules should be stored here
- `locallib\dependencies.py` - a service factory that should be used for generating all internal classes and modules
- prefer building classes for containting code
- use a dependency injection model where all required dependencies for a class are defined in the constructor. Classes should not construct other classes outside of factory classes and methods.

## Environment
- `uv` is used for dependency management and virtual environments.  Do not use `pip` or `venv`      directly.
- use `uv run python` to run python commands within the uv environment.
- use pytest for all unit tests
- use `uv run ruff format` to autoformat files
- use `uv run ruff check --fix` to lint all files
- run `uv run mypy` to perform static type checking on the codebase

## Configuration
- `settings.toml` - Connection string and other settings are stored here
- `secrets.toml` - location for secrets that are excluded from source control

## Test Markers
Configured in `pyproject.toml`:
- `unit` - Standard unit tests
- `slow` - Long-running tests
- `unit_external` - Tests requiring external resources
- `integration` - Tests requiring full environment setup

## git
- commit messages should be short and succinct and follow the format of `<type>: <message>`.  For example, `feat: add new feature` or `fix: fix a bug`.
- use bullets in commit messages to describe details of the changes
- use conventional commit types: `feat`, `fix`, `docs`, `style`, `refactor`, `test`, `chore`,)
- use commit types `feat!` and `fix!` to indicate breaking changes
- commit messages should be suitable for a public changelog.

## claude behavior
- when implementing a new feature from a file specification, add a summary of the final implmentation to the end of the file.  Include the claude session id in the summary.

 ## Memory
 - create notes and lessons learned in the `docs/memory/` directory.  Use MEMORY.md as the main file.
 - notes in this file should be very short and concise, capturing only the essential information and key takeaways.
 - Larger notes or explanations should be stored in separate files within the `docs/memory/` directory, and referenced from MEMORY.md as needed.

 - If a spec file is provided, write a summary of the implementation in the spec upon completion of implementation.