"""Fresh-user install qualification for the Linux .deb, .rpm and portable tarball.

Run as root (a container, or ``sudo`` on a CI VM). Packages are installed with the
distribution's own package manager; everything after that runs as a new unprivileged
user created for the check. Covered: install, optional upgrade over an older build,
launch through ``inferbridge``, readiness, API-key enforcement, the mock OpenAI contract
(chat, streaming, embeddings, rerank, slots, lifecycle), the optional systemd --user
service lifecycle, uninstall, and that removal leaves nothing behind except user data
(and nothing at all after ``--purge``). Stdlib only; works with Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import os
import pwd
import secrets
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
USER = "ibfresh"
OPT = Path("/opt/inferbridge")


def log(message: str) -> None:
    print(f"[install-check] {message}", flush=True)


def run(command: list[str], *, check: bool = True, timeout: float = 900, **kwargs):
    log("$ " + " ".join(command))
    result = subprocess.run(command, text=True, capture_output=True, timeout=timeout, **kwargs)
    if result.stdout.strip():
        print(result.stdout[-6000:], flush=True)
    if result.returncode and check:
        print(result.stderr[-6000:], file=sys.stderr, flush=True)
        raise subprocess.CalledProcessError(result.returncode, command)
    return result


class FreshUser:
    def __init__(self, *, systemd: bool, api_key: str) -> None:
        if subprocess.run(["id", USER], capture_output=True).returncode:
            run(["useradd", "--create-home", "--shell", "/bin/bash", USER])
        self.account = pwd.getpwnam(USER)
        self.home = Path(self.account.pw_dir)
        self.api_key = api_key
        self.systemd = systemd
        self.runtime_dir = f"/run/user/{self.account.pw_uid}"
        if systemd:
            run(["loginctl", "enable-linger", USER])
            deadline = time.monotonic() + 60
            # systemctl --user talks to the manager's private socket; a D-Bus user bus
            # (dbus-user-session) is optional on servers.
            while not Path(self.runtime_dir, "systemd/private").exists():
                if time.monotonic() > deadline:
                    raise RuntimeError("The user's systemd manager did not start.")
                time.sleep(0.5)
        self.baseline = self.snapshot()

    def environment(self, extra: dict[str, str] | None = None) -> list[str]:
        values = {
            "HOME": str(self.home),
            "USER": USER,
            "LOGNAME": USER,
            "PATH": f"{self.home}/.local/bin:/usr/local/bin:/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "OV_LLM_API_KEY": self.api_key,
            "HF_HUB_OFFLINE": "1",
            "HF_HUB_DISABLE_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
        }
        if self.systemd:
            values["XDG_RUNTIME_DIR"] = self.runtime_dir
            if Path(self.runtime_dir, "bus").exists():
                values["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={self.runtime_dir}/bus"
        values.update(extra or {})
        return ["env", "-i", *(f"{key}={value}" for key, value in values.items())]

    def run(self, *command: str, check: bool = True, timeout: float = 600, extra=None):
        return run(
            ["runuser", "-u", USER, "--", *self.environment(extra), *command],
            check=check,
            timeout=timeout,
            cwd="/",
        )

    def snapshot(self) -> set[str]:
        return {
            str(path.relative_to(self.home))
            for path in self.home.rglob("*")
            if path.is_file() or path.is_symlink()
        }

    def leftovers(self) -> list[str]:
        return sorted(self.snapshot() - self.baseline)


def install_package(path: Path) -> None:
    if path.suffix == ".deb":
        run(["apt-get", "install", "-y", "--no-install-recommends", str(path)], timeout=1800)
    else:
        run(["dnf", "install", "-y", "--setopt=install_weak_deps=False", str(path)], timeout=1800)


def remove_package(kind: str) -> None:
    if kind == "deb":
        run(["apt-get", "purge", "-y", "inferbridge"], timeout=900)
    else:
        run(["dnf", "remove", "-y", "inferbridge"], timeout=900)


def package_version(kind: str) -> str:
    command = (
        ["dpkg-query", "-W", "-f=${Version}", "inferbridge"]
        if kind == "deb"
        else ["rpm", "-q", "--qf", "%{VERSION}", "inferbridge"]
    )
    return run(command).stdout.strip()


def status(user: FreshUser) -> dict:
    result = user.run("inferbridge", "status", "--json", check=False)
    return json.loads(result.stdout)


def wait_until(predicate, timeout: float, what: str):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(1)
    raise RuntimeError(f"Timed out waiting for {what}.")


def exercise_running_app(user: FreshUser, evidence: Path, python: Path) -> dict:
    user.run("inferbridge", "start", "--mock", timeout=300)
    current = status(user)
    assert current["running"] and current["ready"], current
    assert current["url"].startswith("http://127.0.0.1:"), "Must bind loopback by default"
    base = current["url"].rstrip("/")
    probe = (
        "import sys, urllib.error, urllib.request\n"
        "try:\n    urllib.request.urlopen(sys.argv[1], timeout=10)\n"
        "except urllib.error.HTTPError as error:\n    print(error.code)\n"
        "else:\n    print(200)\n"
    )
    # -B: these checks run the bundled interpreter directly (no launcher, so no
    # PYTHONDONTWRITEBYTECODE); they must not write __pycache__ into a user-owned bundle.
    unauthorized = user.run(str(python), "-I", "-B", "-c", probe, f"{base}/v1/models")
    assert unauthorized.stdout.strip() == "401", "Unauthenticated API access was accepted"
    # A second start is a no-op that reports the same instance.
    user.run("inferbridge", "start", "--mock")
    assert status(user)["server_pid"] == current["server_pid"], "A second server was started"
    # The checkout and the evidence directory may sit under a home directory the fresh
    # user cannot traverse (/home/runner is 0750): give the user its own scratch
    # directory in /tmp and copy the reports into the evidence as root afterwards.
    scratch = Path(tempfile.mkdtemp(prefix="inferbridge-contract-", dir="/tmp"))
    os.chown(scratch, user.account.pw_uid, user.account.pw_gid)
    contract = scratch / "validate_api_contract.py"
    shutil.copyfile(ROOT / "scripts/validate_api_contract.py", contract)
    os.chmod(contract, 0o644)
    user.run(
        str(python),
        "-I",
        "-B",
        str(contract),
        "--base-url",
        base,
        "--profile",
        "full",
        "--device",
        "CPU",
        "--expect-mock",
        "--include-embeddings",
        "--include-rerank",
        "--exercise-slots",
        "--exercise-lifecycle",
        "--load-timeout",
        "120",
        "--timeout",
        "60",
        "--output-json",
        str(scratch / "contract.json"),
        "--output-markdown",
        str(scratch / "contract.md"),
        timeout=600,
    )
    evidence.mkdir(parents=True, exist_ok=True)
    for name in ("contract.json", "contract.md"):
        shutil.copyfile(scratch / name, evidence / name)
    shutil.rmtree(scratch, ignore_errors=True)
    user.run("inferbridge", "logs", "-n", "5")
    user.run("inferbridge", "stop", timeout=120)
    assert not status(user)["running"], "inferbridge stop left the instance running"
    return current


def exercise_service(user: FreshUser) -> None:
    user.run("inferbridge", "service", "enable", timeout=120)
    current = wait_until(
        lambda: (lambda value: value if value.get("ready") else None)(status(user)),
        240,
        "the systemd user service to become ready",
    )
    assert current["service"]["active"] == "active", current
    assert current["service"]["enabled"] == "enabled", current
    user.run("systemctl", "--user", "restart", "inferbridge.service", timeout=120)
    wait_until(lambda: status(user).get("ready"), 240, "the restarted service")
    user.run("inferbridge", "stop", timeout=120)  # Stops the service, not a desktop owner.
    assert not status(user)["running"]
    user.run("inferbridge", "service", "disable", timeout=120)
    assert status(user)["service"]["enabled"] in {"disabled", "not-found"}


def seed_user_data(user: FreshUser) -> Path:
    data = user.home / ".local/share/InferBridge"
    sentinel = data / "models" / "user-owned-model.txt"
    user.run("mkdir", "-p", str(sentinel.parent))
    user.run("sh", "-c", f"printf preserve > '{sentinel}'")
    return sentinel


def assert_private_data(user: FreshUser) -> None:
    data = user.home / ".local/share/InferBridge"
    mode = data.stat().st_mode & 0o777
    assert mode == 0o700, f"Data directory mode is {oct(mode)}, expected 0o700"


def check_system_files(kind: str) -> None:
    launcher = OPT / "InferBridge"
    assert launcher.is_file() and os.access(launcher, os.X_OK)
    assert Path("/usr/bin/inferbridge").resolve() == launcher
    assert Path("/usr/share/applications/inferbridge.desktop").is_file()
    assert Path("/usr/share/icons/hicolor/256x256/apps/inferbridge.png").is_file()
    unit = Path("/usr/lib/systemd/user/inferbridge.service")
    assert unit.is_file() and "--service" in unit.read_text()
    if shutil.which("desktop-file-validate"):
        run(["desktop-file-validate", "/usr/share/applications/inferbridge.desktop"])
    owner = OPT.stat()
    assert owner.st_uid == 0 and not owner.st_mode & 0o022, "Install tree must be root-owned"


def tree_signature(path: Path) -> dict[str, int]:
    return {str(p.relative_to(path)): p.stat().st_mtime_ns for p in path.rglob("*") if p.is_file()}


def qualify_package(args: argparse.Namespace, user: FreshUser) -> dict:
    kind = args.package.suffix.lstrip(".")
    report: dict = {"kind": kind}
    python = OPT / "python/bin/python3.12"
    if args.previous:
        install_package(args.previous)
        report["previous_version"] = package_version(kind)
        user.run("inferbridge", "start", "--mock", timeout=300)
        sentinel = seed_user_data(user)
        user.run("inferbridge", "stop", timeout=120)
    install_package(args.package)
    report["package_version"] = package_version(kind)
    if args.previous:
        assert report["package_version"] != report["previous_version"], "Upgrade did not happen"
    check_system_files(kind)
    before = tree_signature(OPT)
    assert "InferBridge" in user.run("inferbridge", "version").stdout
    doctor = json.loads(user.run("inferbridge", "doctor", "--json").stdout)
    assert "CPU" in doctor["openvino_devices"], doctor["openvino_devices"]
    report["doctor_errors"] = doctor["errors"]
    report["instance"] = exercise_running_app(user, args.evidence, python)
    assert_private_data(user)
    if args.previous:
        assert sentinel.read_text() == "preserve", "Upgrade lost user data"
    if args.systemd:
        exercise_service(user)
        report["systemd_user_service"] = "passed"
    assert tree_signature(OPT) == before, "The application wrote inside /opt/inferbridge"
    user.run("inferbridge", "uninstall")
    leftovers = user.leftovers()
    assert leftovers and all(item.startswith(".local/share/InferBridge/") for item in leftovers), (
        f"Uninstall left files outside the data directory: {leftovers}"
    )
    user.run("inferbridge", "uninstall", "--purge", "--yes")
    remove_package(kind)
    assert not OPT.exists() and not Path("/usr/bin/inferbridge").exists(), "Package left files"
    assert not Path("/usr/lib/systemd/user/inferbridge.service").exists()
    assert user.leftovers() == [], f"Purge left files: {user.leftovers()}"
    return report


def qualify_archive(args: argparse.Namespace, user: FreshUser) -> dict:
    report: dict = {"kind": "archive"}
    applications = user.home / "Applications"
    user.run("mkdir", "-p", str(applications))

    def extract(archive: Path) -> Path:
        staged = Path("/tmp") / f"inferbridge-{secrets.token_hex(4)}.tar.gz"
        shutil.copyfile(archive, staged)
        os.chmod(staged, 0o644)
        user.run("tar", "-xzf", str(staged), "-C", str(applications), timeout=900)
        staged.unlink()
        with tarfile.open(archive) as source:
            top = source.getmembers()[0].name.split("/")[0]
        return applications / top

    if args.previous:
        old = extract(args.previous)
        # Previous builds predate `install`; register them the way their docs said.
        user.run(str(old / "InferBridge"), "--install-desktop")
        # The previous build is the *upgrade source*, not the code under test. Dev builds
        # before 575121d fail about 1 launch in 64 (a dash-leading instance nonce made the
        # server child exit 2), so the old build gets up to three attempts; the build
        # under test never does.
        for attempt in range(1, 4):
            result = user.run(
                str(old / "InferBridge"),
                "--mock",
                "--headless",
                "--headless-seconds",
                "20",
                "--no-browser",
                check=False,
                timeout=300,
            )
            if result.returncode == 0:
                break
            log(f"Previous build launch {attempt} exited {result.returncode}")
        else:
            raise RuntimeError("The previous build could not run three times in a row.")
        report["previous_launch_attempts"] = attempt
        sentinel = seed_user_data(user)
        user.run(str(old / "InferBridge"), "--uninstall-desktop")
        user.run("rm", "-rf", str(old))
        report["previous_archive"] = args.previous.name
        # Files an *older* build left outside its data directory (for example the
        # ~/intel OpenVINO telemetry IDs written before abd4cb6) are recorded, then
        # excluded: the uninstall checks below judge only the build under test.
        stray = [
            item for item in user.leftovers() if not item.startswith(".local/share/InferBridge/")
        ]
        report["left_by_previous_build"] = stray
        user.baseline |= set(stray)
    bundle = extract(args.package)
    user.run(str(bundle / "InferBridge"), "install")
    assert (user.home / ".local/bin/inferbridge").resolve() == (bundle / "InferBridge").resolve()
    entry = user.home / ".local/share/applications/inferbridge.desktop"
    if shutil.which("desktop-file-validate"):
        run(["desktop-file-validate", str(entry)])
    before = tree_signature(bundle)
    python = bundle / "python/bin/python3.12"
    report["instance"] = exercise_running_app(user, args.evidence, python)
    assert_private_data(user)
    if args.previous:
        assert sentinel.read_text() == "preserve", "Upgrade lost user data"
    if args.systemd:
        exercise_service(user)
        report["systemd_user_service"] = "passed"
    after = tree_signature(bundle)
    changed = sorted(
        name for name in before.keys() | after.keys() if before.get(name) != after.get(name)
    )
    assert not changed, f"The application wrote inside its bundle: {changed[:20]}"
    user.run("inferbridge", "autostart", "enable")
    user.run(str(bundle / "InferBridge"), "uninstall", "--remove-app")
    leftovers = user.leftovers()
    assert all(item.startswith(".local/share/InferBridge/") for item in leftovers), leftovers
    assert not bundle.exists()
    user.run("sh", "-c", "rm -rf ~/.local/share/InferBridge ~/Applications")
    remaining = user.leftovers()
    assert remaining == [], f"Stray files after uninstall: {remaining}"
    return report


def hand_over(evidence: Path) -> None:
    """Make root-written evidence (copied 0700/0600 user logs) readable by the uploader."""
    if not evidence.is_dir():
        return
    owner = os.environ.get("SUDO_UID"), os.environ.get("SUDO_GID")
    for path in [evidence, *evidence.rglob("*")]:
        if path.is_symlink():
            continue
        if all(owner):
            os.chown(path, int(owner[0]), int(owner[1]))
        os.chmod(path, 0o755 if path.is_dir() else 0o644)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True, help=".deb, .rpm or .tar.gz")
    parser.add_argument("--previous", type=Path, help="Older build to upgrade from")
    parser.add_argument("--systemd", action="store_true", help="Exercise systemctl --user")
    parser.add_argument("--evidence", type=Path, default=Path("/tmp/inferbridge-install"))
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit("Run as root: packages are installed system-wide, then tested as a new user.")
    args.package = args.package.resolve()
    args.previous = args.previous.resolve() if args.previous else None
    args.evidence = args.evidence.resolve()
    user = FreshUser(systemd=args.systemd, api_key=secrets.token_hex(24))
    try:
        if args.package.name.endswith(".tar.gz"):
            report = qualify_archive(args, user)
        else:
            report = qualify_package(args, user)
    finally:
        logs = user.home / ".local/share/InferBridge/logs"
        if logs.is_dir():
            shutil.copytree(logs, args.evidence / "application-logs", dirs_exist_ok=True)
        hand_over(args.evidence)
    report["passed"] = True
    args.evidence.mkdir(parents=True, exist_ok=True)
    (args.evidence / "install-checks.json").write_text(json.dumps(report, indent=2) + "\n")
    hand_over(args.evidence)
    log(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
