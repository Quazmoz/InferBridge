# Contributing to InferBridge

Thank you for your interest in contributing to InferBridge! We welcome all contributions, including bug fixes, documentation improvements, feature requests, and code enhancements.

This document provides guidelines for contributing to this project.

---

## Code of Conduct

Please be respectful and welcoming to all community members in issue trackers, pull requests, and discussion areas.

---

## Getting Started

### 1. Fork and Clone
First, fork this repository on GitHub, and then clone your fork locally:

```powershell
git clone https://github.com/<your-username>/InferBridge.git
cd InferBridge
```

### 2. Set Up Your Environment
Run the first-time setup script for your platform:

```powershell
# Windows: runtime and conversion dependencies
.\setup.bat

# Windows: runtime dependencies only
.\setup.bat -Minimal
```

```bash
# Linux: Ubuntu/Fedora runtime dependencies only
./setup.sh --minimal

# Linux: include model-conversion tools
./setup.sh
```

This will create a Python virtual environment under `.venv/` and install the requested packages.

### 3. Run the Development Server
You can run the server in **Mock Mode** on any operating system (including macOS and Linux) to develop or test the API/UI without requiring Intel hardware:

```powershell
.\start_server.bat --mock
```

On Linux:

```bash
./start_server.sh --mock
```

To run with a real OpenVINO model on Intel hardware (CPU/GPU/NPU):

```powershell
.\start_server.bat --model <model-id> --device NPU
```

---

## Development Workflow

### Testing
We use [pytest](https://docs.pytest.org/) for automated testing. Our test suite runs against the mock engine, meaning you do not need Intel hardware to run and pass the tests.

1. Install development dependencies:
   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
   ```
2. Run the tests:
   ```powershell
   .\.venv\Scripts\python.exe -m pytest
   ```

All tests should be green before submitting a pull request.

### Changing the browser UI
The browser client has no Node toolchain: it is `web/index.html` plus CSS/JavaScript payloads
declared in `app/*_ui.py` modules and composed by `app/ui_composition.py`. Read
[docs/BROWSER_UI_ARCHITECTURE.md](docs/BROWSER_UI_ARCHITECTURE.md) before adding or reordering
a surface — document order, request-pipeline order, and the page's Content-Security-Policy are
all enforced by tests.

Two extra gates apply to UI changes, because nothing else compiles the injected JavaScript:

```powershell
# Syntax-check every JavaScript payload the browser loads (requires Node.js)
.\.venv\Scripts\python.exe scripts\check_injected_javascript.py

# Drive the composed page in a real browser (requires playwright + chromium)
.\.venv\Scripts\python.exe -m pip install -e .[browser]
.\.venv\Scripts\python.exe -m playwright install chromium
.\.venv\Scripts\python.exe -m pytest browser_tests
```

### Code Style and Formatting
We follow PEP 8 and enforce clean formatting.
We use **ruff** for formatting and linting. Make sure to check and format your code before pushing:

```powershell
# Run lint check
.\.venv\Scripts\ruff check .

# Apply automatic fixes / formatting
.\.venv\Scripts\ruff format .
```

---

## Submitting Pull Requests

1. **Create a branch** for your work:
   ```bash
   git checkout -b feature/my-cool-feature
   ```
2. **Implement changes** and add unit tests under the `tests/` directory if you're writing new functionality.
3. **Verify** that `ruff check .` runs clean and `pytest` passes.
4. **Commit your changes** with a clear commit message.
5. **Push** to your fork and **open a Pull Request** against the main repository.

---

## Reporting Issues

- **Bug Reports**: If you find a bug, please use our **Bug Report** template to submit an issue. Include detailed instructions on how to reproduce the issue, your operating system version, and python version.
- **Feature Requests**: If you have an idea for a feature, please submit it using our **Feature Request** template. Describe the use case and proposed solution.
