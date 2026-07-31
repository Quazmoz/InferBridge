from pathlib import Path


def replace_once(path: str, old: str, new: str) -> None:
    target = Path(path)
    text = target.read_text(encoding="utf-8")
    count = text.count(old)
    if count != 1:
        raise SystemExit(f"Expected one match in {path}, found {count}")
    target.write_text(text.replace(old, new, 1), encoding="utf-8")


replace_once(
    "app/config.py",
    "from app.chat_queue_ui import install_chat_queue_extension\n",
    "from app.chat_queue_ui import install_chat_queue_extension\n"
    "from app.cancellation_ui import install_cancellation_ui_extension\n",
)
replace_once(
    "app/config.py",
    "from app.model_library_routes import install_model_library_routes_extension\n",
    "from app.model_cancellation import install_model_cancellation_routes_extension\n"
    "from app.model_library_routes import install_model_library_routes_extension\n",
)
replace_once(
    "app/config.py",
    "install_model_library_routes_extension()\ninstall_engine_handoff_routes_extension()\n",
    "install_model_library_routes_extension()\n"
    "install_model_cancellation_routes_extension()\n"
    "install_engine_handoff_routes_extension()\n",
)
replace_once(
    "app/config.py",
    "install_progress_operation_ui_extension()\ninstall_onboarding_ui_extension()\n",
    "install_progress_operation_ui_extension()\n"
    "install_cancellation_ui_extension()\n"
    "install_onboarding_ui_extension()\n",
)
replace_once(
    "app/config.py",
    "        from app.model_load_target import install_model_load_target_routing\n",
    "        from app.model_cancellation import install_model_cancellation_manager_extension\n"
    "        from app.model_load_target import install_model_load_target_routing\n",
)
replace_once(
    "app/config.py",
    "        install_model_lifecycle_safety()\n        install_desktop_shutdown_safety()\n",
    "        install_model_lifecycle_safety()\n"
    "        install_model_cancellation_manager_extension()\n"
    "        install_desktop_shutdown_safety()\n",
)

replace_once(
    "pyproject.toml",
    "dev = [\n    \"pytest>=8.0.0\",\n    \"httpx>=0.27.0\",\n    \"ruff==0.16.0\",\n]\ndistribution = [\n",
    "dev = [\n    \"pytest>=8.0.0\",\n    \"httpx>=0.27.0\",\n    \"ruff==0.16.0\",\n]\n"
    "browser = [\n    \"playwright>=1.49,<2\",\n]\n"
    "distribution = [\n",
)

replace_once(
    "app/model_cancellation.py",
    "import contextlib\n",
    "",
)

replace_once(
    "app/cancellation_ui.py",
    """    function removeControls(dock) {
        dock.querySelector('.ovrp-cancel-control')?.remove();
        dock.querySelector('.ovrp-cancel-note')?.remove();
        if (!feedback) dock.querySelector('.ovrp-cancel-feedback')?.remove();
    }
""",
    """    function removeControls(dock) {
        dock.querySelector('.ovrp-cancel-control')?.remove();
        dock.querySelector('.ovrp-cancel-note')?.remove();
        dock.querySelector('.ovrp-cancel-feedback')?.remove();
    }
""",
)

replace_once(
    "app/cancellation_ui.py",
    """        dock.querySelector('.ovrp-cancel-note')?.remove();
        let control = metadata.querySelector('.ovrp-cancel-control');
        if (!operation.canCancel) {
            control?.remove();
            if (operation.cancelReason) {
                const note = document.createElement('span');
                note.className = 'ovrp-cancel-note';
                note.textContent = operation.cancelReason;
                metadata.appendChild(note);
            }
        } else {
            if (!control) {
                control = document.createElement('span');
                control.className = 'ovrp-cancel-control';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ovrp-cancel-button';
                control.appendChild(button);
                metadata.appendChild(control);
            }
            const button = control.querySelector('.ovrp-cancel-button');
            const busy = cancellationInFlight === operation.operationId;
            button.disabled = busy;
            button.textContent = busy
                ? 'Cancelling…'
                : (operation.cancelMode === 'conversion' ? 'Cancel conversion' : 'Cancel preparation');
            button.setAttribute('aria-label', `${button.textContent} for ${operation.modelName}`);
            button.onclick = () => void cancelOperation(operation);
        }

        metadata.querySelector('.ovrp-cancel-feedback')?.remove();
        if (feedback && feedback.operationId === operation.operationId) {
            const item = document.createElement('span');
            item.className = `ovrp-cancel-feedback${feedback.isError ? ' error' : ''}`;
            item.setAttribute('role', 'status');
            item.setAttribute('aria-live', 'polite');
            item.textContent = feedback.message;
            metadata.appendChild(item);
        }
""",
    """        let control = metadata.querySelector('.ovrp-cancel-control');
        let note = metadata.querySelector('.ovrp-cancel-note');
        if (!operation.canCancel) {
            control?.remove();
            if (operation.cancelReason) {
                if (!note) {
                    note = document.createElement('span');
                    note.className = 'ovrp-cancel-note';
                    metadata.appendChild(note);
                }
                if (note.textContent !== operation.cancelReason) {
                    note.textContent = operation.cancelReason;
                }
            } else {
                note?.remove();
            }
        } else {
            note?.remove();
            if (!control) {
                control = document.createElement('span');
                control.className = 'ovrp-cancel-control';
                const button = document.createElement('button');
                button.type = 'button';
                button.className = 'ovrp-cancel-button';
                control.appendChild(button);
                metadata.appendChild(control);
            }
            const button = control.querySelector('.ovrp-cancel-button');
            const busy = cancellationInFlight === operation.operationId;
            const label = busy
                ? 'Cancelling…'
                : (operation.cancelMode === 'conversion' ? 'Cancel conversion' : 'Cancel preparation');
            if (button.disabled !== busy) button.disabled = busy;
            if (button.textContent !== label) button.textContent = label;
            const ariaLabel = `${label} for ${operation.modelName}`;
            if (button.getAttribute('aria-label') !== ariaLabel) {
                button.setAttribute('aria-label', ariaLabel);
            }
            button.onclick = () => void cancelOperation(operation);
        }

        let item = metadata.querySelector('.ovrp-cancel-feedback');
        if (feedback && feedback.operationId === operation.operationId) {
            if (!item) {
                item = document.createElement('span');
                item.setAttribute('role', 'status');
                item.setAttribute('aria-live', 'polite');
                metadata.appendChild(item);
            }
            const className = `ovrp-cancel-feedback${feedback.isError ? ' error' : ''}`;
            if (item.className !== className) item.className = className;
            if (item.textContent !== feedback.message) item.textContent = feedback.message;
        } else {
            item?.remove();
        }
""",
)

replace_once(
    "browser_tests/conftest.py",
    "import socket\n",
    "import os\nimport socket\nfrom pathlib import Path\n",
)
replace_once(
    "browser_tests/conftest.py",
    "from app.server import create_app\n\n\n@pytest.fixture(scope=\"session\")\n",
    "from app.server import create_app\n\n\n"
    "@pytest.hookimpl(hookwrapper=True)\n"
    "def pytest_runtest_makereport(item, call):\n"
    "    outcome = yield\n"
    "    report = outcome.get_result()\n"
    "    if report.when != \"call\" or not report.failed or \"page\" not in item.funcargs:\n"
    "        return\n"
    "    page = item.funcargs[\"page\"]\n"
    "    root = Path(os.environ.get(\"INFERBRIDGE_BROWSER_ARTIFACTS\", \"browser-artifacts\"))\n"
    "    root.mkdir(parents=True, exist_ok=True)\n"
    "    safe_name = item.nodeid.replace(\"/\", \"_\").replace(\"::\", \"__\")\n"
    "    page.screenshot(path=str(root / f\"{safe_name}.png\"), full_page=True)\n"
    "    (root / f\"{safe_name}.html\").write_text(page.content(), encoding=\"utf-8\")\n\n\n"
    "@pytest.fixture(scope=\"session\")\n",
)

replace_once(
    ".github/workflows/ci.yml",
    "          from app.progress_operation_ui import PROGRESS_OPERATION_JS\n"
    "          from app.progress_reliability import PROGRESS_RELIABILITY_JS\n",
    "          from app.cancellation_ui import CANCELLATION_UI_JS\n"
    "          from app.progress_operation_ui import PROGRESS_OPERATION_JS\n"
    "          from app.progress_reliability import PROGRESS_RELIABILITY_JS\n",
)
replace_once(
    ".github/workflows/ci.yml",
    "          Path('/tmp/inferbridge-progress-operation.js').write_text(\n"
    "              PROGRESS_OPERATION_JS,\n"
    "              encoding='utf-8',\n"
    "          )\n",
    "          Path('/tmp/inferbridge-cancellation.js').write_text(\n"
    "              CANCELLATION_UI_JS,\n"
    "              encoding='utf-8',\n"
    "          )\n"
    "          Path('/tmp/inferbridge-progress-operation.js').write_text(\n"
    "              PROGRESS_OPERATION_JS,\n"
    "              encoding='utf-8',\n"
    "          )\n",
)
replace_once(
    ".github/workflows/ci.yml",
    "          node --check /tmp/inferbridge-progress-operation.js\n",
    "          node --check /tmp/inferbridge-cancellation.js\n"
    "          node --check /tmp/inferbridge-progress-operation.js\n",
)
replace_once(
    ".github/workflows/ci.yml",
    "          app/model_lifecycle_ui.py\n",
    "          app/cancellation_ui.py\n"
    "          app/model_cancellation.py\n"
    "          app/model_lifecycle_ui.py\n",
)
replace_once(
    ".github/workflows/ci.yml",
    "          tests/test_model_lifecycle_ui.py\n",
    "          tests/test_cancellation_ui.py\n"
    "          tests/test_model_cancellation.py\n"
    "          tests/test_model_lifecycle_ui.py\n",
)

ci = Path(".github/workflows/ci.yml")
text = ci.read_text(encoding="utf-8")
if "  browser-behavior:\n" in text:
    raise SystemExit("Browser behavior job already exists")
text += """

  browser-behavior:
    name: Chromium Browser Behavior
    runs-on: ubuntu-latest
    timeout-minutes: 25

    steps:
      - name: Checkout repository
        uses: actions/checkout@v4

      - name: Set up Python 3.11
        uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
          cache-dependency-path: pyproject.toml

      - name: Install browser test dependencies
        run: |
          python -m pip install --upgrade pip
          pip install -e .[dev,browser]
          python -m playwright install --with-deps chromium

      - name: Run real Chromium behavior tests
        env:
          OV_LLM_MOCK: "true"
          INFERBRIDGE_BROWSER_ARTIFACTS: browser-artifacts
        run: pytest browser_tests -q

      - name: Upload browser diagnostics on failure
        if: failure()
        uses: actions/upload-artifact@v4
        with:
          name: browser-behavior-diagnostics
          path: browser-artifacts
          if-no-files-found: ignore
          retention-days: 3
"""
ci.write_text(text, encoding="utf-8")
