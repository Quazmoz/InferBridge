from pathlib import Path

path = Path("browser_tests/conftest.py")
text = path.read_text(encoding="utf-8")
old = """import os
import socket
from pathlib import Path
import threading
import time
"""
new = """import os
import socket
import threading
import time
from pathlib import Path
"""
if text.count(old) != 1:
    raise SystemExit(f"Expected one browser import block, found {text.count(old)}")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
