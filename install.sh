#!/usr/bin/env bash
# voice2ee installer: checks dependencies on any Linux distro, offers to install missing ones with your
# package manager, and installs voice2ee for your user (no root needed for the tool itself).
#
#   ./install.sh              check, offer to install missing packages, install voice2ee
#   ./install.sh --check      only report what's installed / missing (changes nothing)
#   ./install.sh --no-deps    install voice2ee without touching system packages
#   ./install.sh --venv       put numpy/scipy in a private virtualenv (if your distro's packages are missing/old)
#   ./install.sh --yes        don't ask before installing packages
#   ./install.sh --uninstall  remove voice2ee (your presets and recordings are kept)
#
# Env: PREFIX (default ~/.local)  ->  files in $PREFIX/share/voice2ee, command in $PREFIX/bin/voice2ee
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/voice2ee"
BIN_DIR="$PREFIX/bin"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS_RELEASE="${VOICE2EE_OS_RELEASE:-/etc/os-release}" # overridable for testing

CHECK_ONLY=0 NO_DEPS=0 USE_VENV=0 ASSUME_YES=0 UNINSTALL=0
for arg in "$@"; do
  case "$arg" in
  --check) CHECK_ONLY=1 ;;
  --no-deps) NO_DEPS=1 ;;
  --venv) USE_VENV=1 ;;
  -y | --yes) ASSUME_YES=1 ;;
  --uninstall) UNINSTALL=1 ;;
  -h | --help)
    sed -n '2,12p' "$0" | sed 's/^# \{0,1\}//'
    exit 0
    ;;
  *)
    echo "Unknown option: $arg (see --help)"
    exit 2
    ;;
  esac
done

if [ -t 1 ]; then
  B=$'\e[1m'
  G=$'\e[32m'
  Y=$'\e[33m'
  R=$'\e[31m'
  N=$'\e[0m'
else B="" G="" Y="" R="" N=""; fi
ok() { printf '  %s✔%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
bad() { printf '  %s✘%s %s\n' "$R" "$N" "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
  rm -rf "$APP_DIR" "$BIN_DIR/voice2ee"
  echo "Removed $APP_DIR and $BIN_DIR/voice2ee."
  echo "Kept: your EasyEffects presets (~/.local/share/easyeffects/input) and ~/.cache/voice2ee (delete it if you like)."
  exit 0
fi

# ------------------------------------------------------------------------------------------------ distro
ID="" ID_LIKE="" PRETTY_NAME="Linux"
# shellcheck disable=SC1090
[ -r "$OS_RELEASE" ] && . "$OS_RELEASE"
FAMILY=""
for id in $ID $ID_LIKE; do
  case "$id" in
  arch | cachyos | manjaro | endeavouros | garuda | artix)
    FAMILY=arch
    break
    ;;
  debian | ubuntu | linuxmint | pop | elementary | zorin | kali | raspbian | neon)
    FAMILY=debian
    break
    ;;
  fedora | rhel | centos | rocky | almalinux | nobara | ultramarine)
    FAMILY=fedora
    break
    ;;
  opensuse* | suse | sles)
    FAMILY=suse
    break
    ;;
  void)
    FAMILY=void
    break
    ;;
  alpine)
    FAMILY=alpine
    break
    ;;
  gentoo | funtoo)
    FAMILY=gentoo
    break
    ;;
  nixos)
    FAMILY=nixos
    break
    ;;
  solus)
    FAMILY=solus
    break
    ;;
  esac
done

# package names per family:  python  numpy  scipy  pulse-tools(pactl,parec)  pipewire-tools  easyeffects  ffmpeg
case "$FAMILY" in
arch)
  PM="pacman"
  INSTALL=(sudo pacman -S --needed)
  PKG=(python python-numpy python-scipy libpulse pipewire easyeffects ffmpeg)
  ;;
debian)
  PM="apt"
  INSTALL=(sudo apt-get install -y)
  PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-bin easyeffects ffmpeg)
  ;;
fedora)
  PM="dnf"
  INSTALL=(sudo dnf install -y)
  PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-utils easyeffects ffmpeg)
  ;;
suse)
  PM="zypper"
  INSTALL=(sudo zypper install -y)
  PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-tools easyeffects ffmpeg)
  ;;
void)
  PM="xbps"
  INSTALL=(sudo xbps-install -S)
  PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire easyeffects ffmpeg)
  ;;
alpine)
  PM="apk"
  INSTALL=(sudo apk add)
  PKG=(python3 py3-numpy py3-scipy pulseaudio-utils pipewire-tools easyeffects ffmpeg)
  ;;
gentoo)
  PM="emerge"
  INSTALL=(sudo emerge --ask=n --noreplace)
  PKG=(dev-lang/python dev-python/numpy dev-python/scipy media-libs/libpulse media-video/pipewire
    media-sound/easyeffects media-video/ffmpeg)
  ;;
solus)
  PM="eopkg"
  INSTALL=(sudo eopkg install -y)
  PKG=(python3 numpy scipy pulseaudio pipewire easyeffects ffmpeg)
  ;;
nixos)
  PM="nix"
  INSTALL=()
  PKG=(python3 python3Packages.numpy python3Packages.scipy pulseaudio pipewire easyeffects ffmpeg)
  ;;
*)
  PM=""
  INSTALL=()
  PKG=(python3 numpy scipy "pactl/parec (PulseAudio utils)" "pw-loopback/pw-play (PipeWire tools)" easyeffects ffmpeg)
  ;;
esac

echo "${B}voice2ee installer${N}  -  $PRETTY_NAME${FAMILY:+ ($FAMILY family, $PM)}"
echo

# ------------------------------------------------------------------------------------------------ checks
MISSING_REQ=() MISSING_OPT=() PY=""
PY_OK=0 NUMPY_OK=0 SCIPY_OK=0

echo "${B}Required${N}"
for c in python3 python; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then
    PY="$c"
    break
  fi
done
if [ -n "$PY" ]; then
  PY_OK=1
  ok "Python $("$PY" -c 'import platform; print(platform.python_version())')"
  if "$PY" -c 'import numpy' 2>/dev/null; then
    NUMPY_OK=1
    ok "numpy $("$PY" -c 'import numpy; print(numpy.__version__)')"
  else
    bad "numpy"
    MISSING_REQ+=("${PKG[1]}")
  fi
  if "$PY" -c 'import scipy' 2>/dev/null; then
    SCIPY_OK=1
    ok "scipy $("$PY" -c 'import scipy; print(scipy.__version__)')"
  else
    bad "scipy"
    MISSING_REQ+=("${PKG[2]}")
  fi
else
  bad "Python 3.9 or newer"
  MISSING_REQ+=("${PKG[0]}" "${PKG[1]}" "${PKG[2]}")
fi
if have pactl && have parec; then
  ok "pactl + parec (recording)"
else
  bad "pactl / parec (recording)"
  MISSING_REQ+=("${PKG[3]}")
fi

EE_KIND="" EE_VER=""
if have easyeffects; then
  EE_KIND="native"
  EE_OUT="$(timeout 5 easyeffects --version 2>/dev/null || true)"
  EE_VER="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <<<"$EE_OUT" | head -1 || true)"
elif have flatpak && flatpak info com.github.wwmm.easyeffects >/dev/null 2>&1; then
  EE_KIND="flatpak"
  FP_OUT="$(flatpak info com.github.wwmm.easyeffects 2>/dev/null || true)"
  EE_VER="$(awk -F': *' '/Version/{print $2; exit}' <<<"$FP_OUT")"
fi
if [ -n "$EE_KIND" ]; then
  if [ -n "$EE_VER" ] && [ "$(printf '%s\n8.0.9\n' "$EE_VER" | sort -V | head -1)" != "8.0.9" ]; then
    warn "EasyEffects $EE_VER ($EE_KIND) - 8.0.9 or newer is recommended (local server: presets load without"
    warn "  closing the window, settings are read back, raw voice recorded via bypass)"
  else ok "EasyEffects ${EE_VER:-(version unknown)} ($EE_KIND)"; fi
else
  bad "EasyEffects (or install the Flatpak: flatpak install flathub com.github.wwmm.easyeffects)"
  MISSING_REQ+=("${PKG[5]}")
fi

echo
echo "${B}Recommended${N}"
if have pw-loopback && { have pw-play || have paplay; }; then
  ok "pw-loopback + pw-play (record-once mode)"
else
  warn "pw-loopback / pw-play missing - record-once mode falls back to live rounds"
  MISSING_OPT+=("${PKG[4]}")
fi
if have pactl; then
  PA_INFO="$(pactl info 2>/dev/null || true)"
  if grep -qi pipewire <<<"$PA_INFO"; then
    ok "PipeWire is the running sound server"
  else warn "PipeWire doesn't seem to be running (EasyEffects needs PipeWire)"; fi
fi
SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/EasyEffectsServer"
if [ -S "$SOCK" ] || [ -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/app/com.github.wwmm.easyeffects/EasyEffectsServer" ]; then
  ok "EasyEffects local server is running"
else warn "EasyEffects local server not found right now (start EasyEffects before using voice2ee)"; fi

echo
echo "${B}Optional${N} (only for running the tests)"
if have ffmpeg; then
  FF_FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
  if grep -q ' flite ' <<<"$FF_FILTERS"; then
    ok "ffmpeg with flite (test speech)"
  else warn "ffmpeg without flite - tests that need synthetic speech will be skipped"; fi
else
  warn "ffmpeg - tests that need synthetic speech will be skipped"
  MISSING_OPT+=("${PKG[6]}")
fi

echo
if [ "$CHECK_ONLY" = 1 ]; then
  if [ ${#MISSING_REQ[@]} -eq 0 ]; then
    echo "${G}All required dependencies are installed.${N}"
  else echo "Missing required packages: ${B}${MISSING_REQ[*]}${N}"; fi
  [ ${#MISSING_OPT[@]} -gt 0 ] && echo "Missing recommended/optional packages: ${MISSING_OPT[*]}"
  exit 0
fi

# ------------------------------------------------------------------------------------------------ install deps
confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  local a
  read -r -p "$1 [Y/n] " a || true
  case "${a,,}" in "" | y | yes) return 0 ;; *) return 1 ;; esac
}

# with --venv, numpy/scipy come from pip instead of the distro
if [ "$USE_VENV" = 1 ]; then
  keep=()
  for p in "${MISSING_REQ[@]}"; do
    [ "$p" = "${PKG[1]}" ] || [ "$p" = "${PKG[2]}" ] || keep+=("$p")
  done
  MISSING_REQ=("${keep[@]}")
fi

if [ "$NO_DEPS" = 0 ] && [ $((${#MISSING_REQ[@]} + ${#MISSING_OPT[@]})) -gt 0 ]; then
  TO_INSTALL=("${MISSING_REQ[@]}" "${MISSING_OPT[@]}")
  if [ ${#INSTALL[@]} -gt 0 ]; then
    echo "Missing packages: ${B}${TO_INSTALL[*]}${N}"
    if confirm "Install them now with: ${INSTALL[*]} ...?"; then
      "${INSTALL[@]}" "${TO_INSTALL[@]}" || warn "The package manager reported a problem - check the output above."
    else
      warn "Skipped. Install them yourself, then run ./install.sh again."
    fi
  elif [ "$FAMILY" = nixos ]; then
    echo "On NixOS, add these to environment.systemPackages (or use nix-shell -p ...):"
    echo "  ${PKG[*]}"
  else
    echo "Couldn't recognise your distro's package manager. Please install: ${B}${TO_INSTALL[*]}${N}"
  fi
  echo
fi

# re-check what the tool can't run without
[ -z "$PY" ] && for c in python3 python; do have "$c" && PY="$c" && break; done
if [ -z "$PY" ]; then
  bad "Python 3.9+ is still missing - can't install voice2ee."
  exit 1
fi

# ------------------------------------------------------------------------------------------------ install tool
echo "${B}Installing voice2ee${N} to $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR"
rm -rf "$APP_DIR/v2ee"
cp -r "$SRC_DIR/v2ee" "$APP_DIR/"
cp "$SRC_DIR/voice2ee.py" "$SRC_DIR/README.md" "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -prune -exec rm -rf {} +

RUN_PY="$PY"
if [ "$USE_VENV" = 1 ]; then
  echo "  creating virtualenv with numpy + scipy (needs internet)..."
  if "$PY" -m venv "$APP_DIR/venv" && "$APP_DIR/venv/bin/pip" install --quiet --upgrade pip numpy scipy; then
    RUN_PY="$APP_DIR/venv/bin/python"
    ok "virtualenv ready"
  else
    bad "couldn't create the virtualenv (is python3-venv / python3-pip installed?)"
    exit 1
  fi
fi

cat >"$BIN_DIR/voice2ee" <<EOF
#!/bin/sh
# voice2ee launcher (installed by install.sh)
exec "$RUN_PY" "$APP_DIR/voice2ee.py" "\$@"
EOF
chmod +x "$BIN_DIR/voice2ee"
ok "command installed: $BIN_DIR/voice2ee"

if ! "$RUN_PY" -c 'import numpy, scipy' 2>/dev/null; then
  warn "numpy/scipy still can't be imported by $RUN_PY - install them (or re-run with --venv) before using voice2ee."
fi
case ":$PATH:" in
*":$BIN_DIR:"*) ;;
*)
  warn "$BIN_DIR is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
  echo "      export PATH=\"$BIN_DIR:\$PATH\""
  ;;
esac
echo
echo "Done. Start EasyEffects, then run:  ${B}voice2ee${N}   (voice2ee --help for options)"
