#!/usr/bin/env bash
set -euo pipefail

detect_linux_id() {
    if [ -r /etc/os-release ]; then
        (
            # shellcheck disable=SC1091
            . /etc/os-release
            printf '%s\n' "${ID:-unknown}"
        )
    else
        printf 'unknown\n'
    fi
}

detect_linux_pretty_name() {
    if [ -r /etc/os-release ]; then
        (
            # shellcheck disable=SC1091
            . /etc/os-release
            printf '%s\n' "${PRETTY_NAME:-Linux}"
        )
    else
        printf 'Linux\n'
    fi
}

linux_platform_name() {
    case "$1" in
        ubuntu) printf 'Ubuntu\n' ;;
        fedora) printf 'Fedora\n' ;;
        *) printf 'Linux\n' ;;
    esac
}

linux_is_supported_distro() {
    case "$1" in
        ubuntu|fedora) return 0 ;;
        *) return 1 ;;
    esac
}

print_python_install_hint() {
    case "$1" in
        ubuntu)
            cat >&2 <<'EOF'
On Ubuntu, install Python and venv support first:
  sudo apt update
  sudo apt install -y python3 python3-venv python3-pip git
EOF
            ;;
        fedora)
            cat >&2 <<'EOF'
On Fedora, install Python and basic build tooling first:
  sudo dnf install -y python3 python3-pip python3-devel git
EOF
            ;;
        *)
            cat >&2 <<'EOF'
Install Python 3.11, 3.12, 3.13, or 3.14 plus venv/pip support with your distro package manager.
EOF
            ;;
    esac
}

print_supported_python_hint() {
    case "$1" in
        ubuntu)
            cat >&2 <<'EOF'
On Ubuntu 22.04, you may need to install Python 3.11 and python3.11-venv.
EOF
            ;;
        fedora)
            cat >&2 <<'EOF'
On Fedora, use the distro python3 when it is 3.11-3.14, or install a supported python3.x package and pass --python.
EOF
            ;;
        *)
            cat >&2 <<'EOF'
Install Python 3.11, 3.12, 3.13, or 3.14, then pass it with --python if python3 points elsewhere.
EOF
            ;;
    esac
}

print_venv_install_hint() {
    case "$1" in
        ubuntu)
            cat >&2 <<'EOF'
On Ubuntu, install venv support for your Python version, for example:
  sudo apt install -y python3-venv
or:
  sudo apt install -y python3.11-venv
EOF
            ;;
        fedora)
            cat >&2 <<'EOF'
On Fedora, venv support normally ships with python3. If it is missing, reinstall Python tooling:
  sudo dnf install -y python3 python3-pip python3-devel
EOF
            ;;
        *)
            cat >&2 <<'EOF'
Install venv support for your Python package, then run setup again.
EOF
            ;;
    esac
}

print_pciutils_hint() {
    case "$1" in
        ubuntu) printf 'Guidance only: sudo apt install -y pciutils\n' ;;
        fedora) printf 'Guidance only: sudo dnf install -y pciutils\n' ;;
        *) printf 'Guidance only: install pciutils with your distro package manager\n' ;;
    esac
}
