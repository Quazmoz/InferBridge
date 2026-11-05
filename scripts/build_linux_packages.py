"""Build .deb and .rpm packages from the already-qualified Linux portable archive.

The packages contain exactly the portable bundle, installed read-only at
/opt/inferbridge, plus /usr/bin/inferbridge, a menu entry, a themed icon and a systemd
*user* unit. Maintainer scripts only refresh desktop caches: nothing runs as root at
runtime, no service is enabled or started, and user data under ~/.local/share is never
touched by install, upgrade or removal. Versions come from the bundle's build-info.json
(itself from app/version.py), so all three formats always agree.

Usage:
    python scripts/build_linux_packages.py --archive dist/linux/<name>-portable.tar.gz \\
        --output dist/linux [--formats deb,rpm]

Needs dpkg-deb (deb) and rpmbuild (rpm, e.g. ``apt install rpm``). Neither needs root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

PACKAGE = "inferbridge"
INSTALL_ROOT = Path("opt/inferbridge")
MAINTAINER = "Quinn Favo <32716387+Quazmoz@users.noreply.github.com>"
HOMEPAGE = "https://github.com/Quazmoz/InferBridge"
SUMMARY = "Local OpenVINO AI server with an OpenAI-compatible API and browser UI"
DESCRIPTION = (
    "InferBridge runs OpenVINO GenAI models on Intel CPU, GPU and NPU behind an\n"
    "OpenAI-compatible API on 127.0.0.1, with a browser UI for downloading, converting\n"
    "and chatting with models. It runs as a normal user; models and settings live in\n"
    "~/.local/share/InferBridge. GPU and NPU need Intel's separate Linux drivers."
)
# Host libraries the bundle links against (ldd evidence of the CI build; everything else is
# private to the bundle). Debian names, then RPM soname capabilities (distro-neutral).
DEB_DEPENDS = (
    "libc6 (>= 2.35), libstdc++6, libgcc-s1, zlib1g, libgomp1, libnuma1, libtbb12, "
    "libatomic1, libcrypt1"
)
DEB_RECOMMENDS = "xdg-utils, ocl-icd-libopencl1, libsecret-tools"
DEB_SUGGESTS = "intel-opencl-icd, libze-intel-gpu1, libze1, systemd"
RPM_REQUIRES = (
    "libc.so.6(GLIBC_2.35)(64bit)",
    "libstdc++.so.6()(64bit)",
    "libgcc_s.so.1()(64bit)",
    "libz.so.1()(64bit)",
    "libgomp.so.1()(64bit)",
    "libnuma.so.1()(64bit)",
    "libtbb.so.12()(64bit)",
    "libatomic.so.1()(64bit)",
    "libcrypt.so.1()(64bit)",
)
RPM_RECOMMENDS = ("xdg-utils", "ocl-icd", "libsecret")
RPM_SUGGESTS = ("intel-compute-runtime", "intel-npu-driver")
_SEMVER = re.compile(r"^(\d+\.\d+\.\d+)(?:-([0-9A-Za-z.-]+))?$")


def package_versions(version: str) -> dict[str, str]:
    """1.1.1-beta.2 -> deb 1.1.1~beta.2, rpm 1.1.1~beta.2: '~' sorts before 1.1.1."""
    match = _SEMVER.fullmatch(version)
    if not match:
        raise ValueError(f"Not a semantic version: {version!r}")
    base, prerelease = match.groups()
    native = f"{base}~{prerelease.replace('-', '.')}" if prerelease else base
    return {"deb": native, "rpm": native}


def artifact_names(version: str) -> dict[str, str]:
    stem = f"InferBridge-{version}-linux-x86_64"
    return {
        "portable": f"{stem}-portable.tar.gz",
        "deb": f"{stem}.deb",
        "rpm": f"{stem}.rpm",
        "checksums": f"InferBridge-{version}-linux-SHA256SUMS.txt",
        "sbom": f"InferBridge-{version}-linux-sbom.spdx.json",
    }


def desktop_file() -> str:
    return (
        "[Desktop Entry]\nType=Application\nName=InferBridge\n"
        "Comment=Local OpenVINO AI server and model manager\n"
        "Exec=inferbridge\nIcon=inferbridge\nTerminal=false\nStartupNotify=false\n"
        "Categories=Development;\nKeywords=AI;LLM;OpenVINO;OpenAI;chat;\n"
        "Actions=Open;Quit;\n\n"
        "[Desktop Action Open]\nName=Open in Browser\nExec=inferbridge open\n\n"
        "[Desktop Action Quit]\nName=Quit InferBridge\nExec=inferbridge stop\n"
    )


def user_unit() -> str:
    return (
        "[Unit]\nDescription=InferBridge local AI server\n"
        f"Documentation={HOMEPAGE}/blob/main/docs/LINUX.md\n"
        "StartLimitIntervalSec=120\nStartLimitBurst=3\n\n"
        "[Service]\nType=exec\nExecStart=/opt/inferbridge/InferBridge --service\n"
        "Restart=on-failure\nRestartSec=5\nTimeoutStopSec=60\nKillMode=mixed\nUMask=0077\n\n"
        "[Install]\nWantedBy=default.target\n"
    )


CACHE_REFRESH = (
    "if command -v update-desktop-database >/dev/null 2>&1; then\n"
    "    update-desktop-database -q /usr/share/applications || true\n"
    "fi\n"
    "if command -v gtk-update-icon-cache >/dev/null 2>&1; then\n"
    "    gtk-update-icon-cache -q -t /usr/share/icons/hicolor || true\n"
    "fi\n"
)


def stage(archive: Path, work: Path) -> tuple[Path, dict]:
    """Lay out the package tree; returns (root, build-info)."""
    extracted = work / "extract"
    with tarfile.open(archive) as source:
        source.extractall(extracted, filter="data")
    bundles = list(extracted.iterdir())
    if len(bundles) != 1 or not (bundles[0] / "InferBridge").is_file():
        raise RuntimeError("The archive must contain exactly one InferBridge bundle.")
    bundle = bundles[0]
    info = json.loads((bundle / "resources/build-info.json").read_text(encoding="utf-8"))
    root = work / "root"
    (root / INSTALL_ROOT).parent.mkdir(parents=True)
    shutil.move(bundle, root / INSTALL_ROOT)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/inferbridge").symlink_to("/opt/inferbridge/InferBridge")
    files = {
        "usr/share/applications/inferbridge.desktop": desktop_file(),
        "usr/lib/systemd/user/inferbridge.service": user_unit(),
    }
    for relative, contents in files.items():
        (root / relative).parent.mkdir(parents=True, exist_ok=True)
        (root / relative).write_text(contents, encoding="utf-8")
    icon = root / "usr/share/icons/hicolor/256x256/apps/inferbridge.png"
    icon.parent.mkdir(parents=True)
    shutil.copyfile(root / INSTALL_ROOT / "resources/InferBridge.png", icon)
    docs = root / "usr/share/doc" / PACKAGE
    docs.mkdir(parents=True)
    shutil.copyfile(root / INSTALL_ROOT / "README.md", docs / "README.md")
    shutil.copyfile(root / INSTALL_ROOT / "resources/LICENSE", docs / "copyright")
    for path in root.rglob("*"):
        if not path.is_symlink():
            executable = path.is_dir() or path.stat().st_mode & 0o111
            path.chmod(0o755 if executable else 0o644)
    return root, info


def _epoch(info: dict) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(info["build_date"]).timestamp())


def build_deb(root: Path, info: dict, output: Path, work: Path) -> Path:
    version = package_versions(info["application_version"])["deb"]
    control_dir = root / "DEBIAN"
    control_dir.mkdir()
    size_kib = sum(p.lstat().st_size for p in root.rglob("*") if not p.is_dir()) // 1024
    (control_dir / "control").write_text(
        f"Package: {PACKAGE}\nVersion: {version}\nArchitecture: amd64\n"
        f"Maintainer: {MAINTAINER}\nInstalled-Size: {size_kib}\n"
        f"Depends: {DEB_DEPENDS}\nRecommends: {DEB_RECOMMENDS}\nSuggests: {DEB_SUGGESTS}\n"
        f"Section: science\nPriority: optional\nHomepage: {HOMEPAGE}\n"
        f"Description: {SUMMARY}\n"
        + "".join(f" {line or '.'}\n" for line in DESCRIPTION.splitlines()),
        encoding="utf-8",
    )
    for script in ("postinst", "postrm"):
        path = control_dir / script
        path.write_text("#!/bin/sh\nset -e\n" + CACHE_REFRESH + "exit 0\n", encoding="utf-8")
        path.chmod(0o755)
    sums = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and not path.is_symlink() and control_dir not in path.parents:
            digest = hashlib.md5(path.read_bytes(), usedforsecurity=False).hexdigest()
            sums.append(f"{digest}  {path.relative_to(root)}\n")
    (control_dir / "md5sums").write_text("".join(sums), encoding="utf-8")
    target = output / artifact_names(info["application_version"])["deb"]
    environment = {**os.environ, "SOURCE_DATE_EPOCH": str(_epoch(info))}
    subprocess.run(
        ["dpkg-deb", "--root-owner-group", "-Zgzip", "-z6", "--build", str(root), str(target)],
        check=True,
        env=environment,
        timeout=3600,
    )
    shutil.rmtree(control_dir)
    return target


def build_rpm(root: Path, info: dict, output: Path, work: Path) -> Path:
    version = package_versions(info["application_version"])["rpm"]
    topdir = work / "rpmbuild"
    for name in ("BUILD", "RPMS", "SPECS", "SOURCES", "SRPMS", "BUILDROOT"):
        (topdir / name).mkdir(parents=True, exist_ok=True)
    scriptlet = CACHE_REFRESH + "exit 0\n"
    spec = topdir / "SPECS/inferbridge.spec"
    spec.write_text(
        f"Name: {PACKAGE}\nVersion: {version}\nRelease: 1\nSummary: {SUMMARY}\n"
        f"License: MIT\nURL: {HOMEPAGE}\nBuildArch: x86_64\nAutoReqProv: no\n"
        + "".join(f"Requires: {item}\n" for item in RPM_REQUIRES)
        + "".join(f"Recommends: {item}\n" for item in RPM_RECOMMENDS)
        + "".join(f"Suggests: {item}\n" for item in RPM_SUGGESTS)
        + f"\n%description\n{DESCRIPTION}\n\n"
        "%install\nmkdir -p %{buildroot}\ncp -a %{ib_root}/. %{buildroot}/\n\n"
        f"%post\n{scriptlet}\n%postun\n{scriptlet}\n"
        "%files\n/opt/inferbridge\n/usr/bin/inferbridge\n"
        "/usr/share/applications/inferbridge.desktop\n"
        "/usr/share/icons/hicolor/256x256/apps/inferbridge.png\n"
        "/usr/lib/systemd/user/inferbridge.service\n"
        "%doc /usr/share/doc/inferbridge/README.md\n"
        "%license /usr/share/doc/inferbridge/copyright\n",
        encoding="utf-8",
    )
    epoch = str(_epoch(info))
    defines = {
        "_topdir": str(topdir),
        "ib_root": str(root),
        # The bundle is already qualified: no stripping, byte-compiling or debuginfo.
        "__os_install_post": "%{nil}",
        "__arch_install_post": "%{nil}",
        "debug_package": "%{nil}",
        "_build_id_links": "none",
        "_binary_payload": "w6.gzdio",
        "source_date_epoch_from_changelog": "0",
        "use_source_date_epoch_as_buildtime": "1",
        "clamp_mtime_to_source_date_epoch": "1",
        "_buildhost": "reproducible",
    }
    command = ["rpmbuild", "-bb", "--quiet"]
    for key, value in defines.items():
        command += ["--define", f"{key} {value}"]
    subprocess.run(
        [*command, str(spec)],
        check=True,
        env={**os.environ, "SOURCE_DATE_EPOCH": epoch},
        timeout=3600,
    )
    built = list((topdir / "RPMS").rglob("*.rpm"))
    if len(built) != 1:
        raise RuntimeError("rpmbuild did not produce exactly one package.")
    target = output / artifact_names(info["application_version"])["rpm"]
    shutil.move(built[0], target)
    return target


def _member_text(archive: Path, suffix: str) -> str:
    with tarfile.open(archive) as source:
        for member in source.getmembers():
            if member.isfile() and member.name.endswith(suffix):
                return source.extractfile(member).read().decode("utf-8")
    raise RuntimeError(f"{suffix} is missing from the archive.")


def sbom(archive: Path, artifacts: list[Path]) -> dict:
    """SPDX 2.3 document: the release files, the private CPython and every bundled wheel."""
    build = json.loads(_member_text(archive, "resources/linux-build.json"))
    version = build["version"]
    licenses = {}
    for line in _member_text(archive, "resources/THIRD-PARTY-NOTICES.txt").splitlines():
        pin, separator, license_text = line.partition(": ")
        if separator and "==" in pin:
            licenses[pin.split("==")[0].lower()] = license_text.strip()
    packages = [
        {
            "SPDXID": "SPDXRef-InferBridge",
            "name": "InferBridge",
            "versionInfo": version,
            "downloadLocation": f"git+{HOMEPAGE}.git@{build['commit']}",
            "licenseDeclared": "MIT",
            "licenseConcluded": "MIT",
            "copyrightText": "NOASSERTION",
            "supplier": f"Person: {MAINTAINER}",
        },
        {
            "SPDXID": "SPDXRef-CPython",
            "name": "CPython (python-build-standalone)",
            "versionInfo": build["python"],
            "downloadLocation": "https://github.com/astral-sh/python-build-standalone",
            "licenseDeclared": "PSF-2.0",
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        },
    ]
    for index, line in enumerate(_member_text(archive, "resources/dependencies.txt").splitlines()):
        name, separator, pinned = line.strip().partition("==")
        if not separator:
            continue
        declared = licenses.get(name.lower(), "")
        spdx_like = re.fullmatch(r"[A-Za-z0-9.+-]+(?: (?:AND|OR|WITH) [A-Za-z0-9.+-]+)*", declared)
        packages.append(
            {
                "SPDXID": f"SPDXRef-PyPI-{index}",
                "name": name,
                "versionInfo": pinned,
                "downloadLocation": f"https://pypi.org/project/{name}/{pinned}/",
                "licenseDeclared": declared if spdx_like else "NOASSERTION",
                "licenseConcluded": "NOASSERTION",
                "copyrightText": "NOASSERTION",
                "externalRefs": [
                    {
                        "referenceCategory": "PACKAGE-MANAGER",
                        "referenceType": "purl",
                        "referenceLocator": f"pkg:pypi/{name.lower()}@{pinned}",
                    }
                ],
            }
        )
    files = [
        {
            "SPDXID": f"SPDXRef-Artifact-{index}",
            "fileName": f"./{path.name}",
            "checksums": [{"algorithm": "SHA256", "checksumValue": _sha256(path)}],
            "licenseConcluded": "NOASSERTION",
            "copyrightText": "NOASSERTION",
        }
        for index, path in enumerate(artifacts)
    ]
    relationships = [
        {
            "spdxElementId": "SPDXRef-DOCUMENT",
            "relationshipType": "DESCRIBES",
            "relatedSpdxElement": "SPDXRef-InferBridge",
        }
    ]
    relationships += [
        {
            "spdxElementId": "SPDXRef-InferBridge",
            "relationshipType": "CONTAINS",
            "relatedSpdxElement": package["SPDXID"],
        }
        for package in packages[1:]
    ]
    relationships += [
        {
            "spdxElementId": item["SPDXID"],
            "relationshipType": "GENERATED_FROM",
            "relatedSpdxElement": "SPDXRef-InferBridge",
        }
        for item in files
    ]
    from datetime import UTC, datetime

    created = datetime.fromtimestamp(int(os.environ.get("SOURCE_DATE_EPOCH", "0")) or 0, UTC)
    return {
        "spdxVersion": "SPDX-2.3",
        "dataLicense": "CC0-1.0",
        "SPDXID": "SPDXRef-DOCUMENT",
        "name": f"InferBridge-{version}-linux-x86_64",
        "documentNamespace": f"{HOMEPAGE}/spdx/{version}/{build['commit']}",
        "creationInfo": {
            "created": created.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "creators": ["Tool: scripts/build_linux_packages.py"],
        },
        "packages": packages,
        "files": files,
        "relationships": relationships,
    }


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def finalize(archive: Path, output: Path) -> Path:
    """Write the SPDX SBOM, then one SHA256SUMS covering every Linux release file."""
    version = json.loads(_member_text(archive, "resources/linux-build.json"))["version"]
    names = artifact_names(version)
    artifacts = [output / names[key] for key in ("portable", "deb", "rpm")]
    artifacts = [path for path in artifacts if path.is_file()]
    sbom_path = output / names["sbom"]
    sbom_path.write_text(json.dumps(sbom(archive, artifacts), indent=2) + "\n", encoding="utf-8")
    sums = output / names["checksums"]
    sums.write_text(
        "".join(f"{_sha256(path)}  {path.name}\n" for path in [*artifacts, sbom_path]),
        encoding="utf-8",
    )
    print(sums)
    return sums


def build(
    archive: Path, output: Path, formats: list[str], *, version_override: str | None = None
) -> list[Path]:
    output.mkdir(parents=True, exist_ok=True)
    built = []
    for kind in formats:
        with tempfile.TemporaryDirectory(prefix=f"inferbridge-{kind}-") as temporary:
            work = Path(temporary)
            root, info = stage(archive, work)
            if version_override:
                # CI only: an older-numbered package of the same payload exercises the
                # package manager's real upgrade path (maintainer scripts, file replacement).
                package_versions(version_override)
                info = {**info, "application_version": version_override}
            builder = {"deb": build_deb, "rpm": build_rpm}[kind]
            built.append(builder(root, info, output, work))
            print(built[-1])
    return built


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--formats", default="deb,rpm", help="deb,rpm or an empty string")
    parser.add_argument(
        "--finalize", action="store_true", help="Also write the SPDX SBOM and SHA256SUMS"
    )
    parser.add_argument("--version-override", help=argparse.SUPPRESS)
    args = parser.parse_args()
    kinds = [item.strip() for item in args.formats.split(",") if item.strip()]
    if not set(kinds) <= {"deb", "rpm"}:
        sys.exit("--formats accepts deb and rpm")
    # Package timestamps come from the source commit, as the archive's already do.
    info = json.loads(_member_text(args.archive, "resources/build-info.json"))
    os.environ["SOURCE_DATE_EPOCH"] = str(_epoch(info))
    build(
        args.archive.resolve(), args.output.resolve(), kinds, version_override=args.version_override
    )
    if args.finalize:
        finalize(args.archive.resolve(), args.output.resolve())
