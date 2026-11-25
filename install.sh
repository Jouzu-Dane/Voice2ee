#!/usr/bin/env bash
# voice2ee installer: checks dependencies on any Linux distro, offers to install missing ones with your
# package manager, and installs voice2ee for your user (no root needed for the tool itself).
#
#   ./install.sh              check, ask Minimal or Full, offer to install missing packages, install voice2ee
#   ./install.sh --minimal    Python + numpy + scipy + PipeWire tools + your real plugins offline (python-lilv)
#   ./install.sh --builtin    Minimal with the built-in plugin model only (nothing extra to install)
#   ./install.sh --full       Minimal + the speech-quality model (onnxruntime and DNSMOS)
#   ./install.sh --check      only report what's installed / missing (changes nothing)
#   ./install.sh --no-deps    install voice2ee without touching system packages
#   ./install.sh --venv       put numpy/scipy in a private virtualenv (if your distro's packages are missing/old)
#   ./install.sh --yes        don't ask before installing packages
#   ./install.sh --uninstall  remove voice2ee; offers to remove system packages it installed that nothing else needs
#
# Env: PREFIX (default ~/.local)  ->  files in $PREFIX/share/voice2ee, command in $PREFIX/bin/voice2ee
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/voice2ee"
BIN_DIR="$PREFIX/bin"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OS_RELEASE="${VOICE2EE_OS_RELEASE:-/etc/os-release}"   # overridable for testing

CHECK_ONLY=0 NO_DEPS=0 USE_VENV=0 ASSUME_YES=0 UNINSTALL=0 EDITION="" PLUGINS=""
for arg in "$@"; do
  case "$arg" in
    --minimal) EDITION=minimal ;;
    --builtin) EDITION="${EDITION:-minimal}"; PLUGINS=builtin ;;
    --full) EDITION=full ;;
    --check) CHECK_ONLY=1 ;;
    --no-deps) NO_DEPS=1 ;;
    --venv) USE_VENV=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "Unknown option: $arg (see --help)"; exit 2 ;;
  esac
done

if [ -t 1 ]; then B=$'\e[1m'; G=$'\e[32m'; Y=$'\e[33m'; R=$'\e[31m'; N=$'\e[0m'; else B="" G="" Y="" R="" N=""; fi
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
warn() { printf '  %s! %s%s\n' "$Y$B" "$*" "$N"; }      # whole line in bold yellow: easy to spot
bad()  { printf '  %s✗ %s%s\n' "$R$B" "$*" "$N"; }      # whole line in bold red
have() { command -v "$1" >/dev/null 2>&1; }

# ------------------------------------------------------------------------------------------------ uninstall
# (--uninstall runs further down, once the distro and its package names are known)

# ------------------------------------------------------------------------------------------------ distro
ID="" ID_LIKE="" PRETTY_NAME="Linux"
# shellcheck disable=SC1090
[ -r "$OS_RELEASE" ] && . "$OS_RELEASE"
FAMILY=""
for id in $ID $ID_LIKE; do
  case "$id" in
    arch|cachyos|manjaro|endeavouros|garuda|artix) FAMILY=arch; break ;;
    debian|ubuntu|linuxmint|pop|elementary|zorin|kali|raspbian|neon) FAMILY=debian; break ;;
    fedora|rhel|centos|rocky|almalinux|nobara|ultramarine) FAMILY=fedora; break ;;
    opensuse*|suse|sles) FAMILY=suse; break ;;
    void) FAMILY=void; break ;;
    alpine) FAMILY=alpine; break ;;
    gentoo|funtoo) FAMILY=gentoo; break ;;
    nixos) FAMILY=nixos; break ;;
    solus) FAMILY=solus; break ;;
  esac
done

# package names per family:  python  numpy  scipy  pulse-tools(pactl,parec)  pipewire-tools  easyeffects  ffmpeg
case "$FAMILY" in
  arch)   PM="pacman"; INSTALL=(sudo pacman -S --needed)
          PKG=(python python-numpy python-scipy libpulse pipewire easyeffects ffmpeg) ;;
  debian) PM="apt"; INSTALL=(sudo apt-get install -y)
          PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-bin easyeffects ffmpeg) ;;
  fedora) PM="dnf"; INSTALL=(sudo dnf install -y)
          PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-utils easyeffects ffmpeg) ;;
  suse)   PM="zypper"; INSTALL=(sudo zypper install -y)
          PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire-tools easyeffects ffmpeg) ;;
  void)   PM="xbps"; INSTALL=(sudo xbps-install -S)
          PKG=(python3 python3-numpy python3-scipy pulseaudio-utils pipewire easyeffects ffmpeg) ;;
  alpine) PM="apk"; INSTALL=(sudo apk add)
          PKG=(python3 py3-numpy py3-scipy pulseaudio-utils pipewire-tools easyeffects ffmpeg) ;;
  gentoo) PM="emerge"; INSTALL=(sudo emerge --ask=n --noreplace)
          PKG=(dev-lang/python dev-python/numpy dev-python/scipy media-libs/libpulse media-video/pipewire
               media-sound/easyeffects media-video/ffmpeg) ;;
  solus)  PM="eopkg"; INSTALL=(sudo eopkg install -y)
          PKG=(python3 numpy scipy pulseaudio pipewire easyeffects ffmpeg) ;;
  nixos)  PM="nix"; INSTALL=()
          PKG=(python3 python3Packages.numpy python3Packages.scipy pulseaudio pipewire easyeffects ffmpeg) ;;
  *)      PM=""; INSTALL=(); PKG=(python3 numpy scipy "pactl/parec (PulseAudio utils)" "pw-loopback/pw-play (PipeWire tools)" easyeffects ffmpeg) ;;
esac

# Real plugins (Minimal and Full): lilv python bindings + the LV2 plugins EasyEffects uses (LSP, Calf). Best-effort names outside
# Arch/Debian/Fedora/openSUSE; --check always shows what's really missing.
case "$FAMILY" in
  arch)   FULL_PKG=(python-lilv lsp-plugins-lv2 calf) ;;
  debian) FULL_PKG=(python3-lilv lsp-plugins-lv2 calf-plugins) ;;
  fedora) FULL_PKG=(python3-lilv lsp-plugins-lv2 calf) ;;
  suse)   FULL_PKG=(python3-lilv lsp-plugins-lv2 calf) ;;
  void)   FULL_PKG=(python3-lilv lsp-plugins calf) ;;
  alpine) FULL_PKG=(py3-lilv lsp-plugins-lv2 calf) ;;
  gentoo) FULL_PKG=("media-libs/lilv[python]" media-plugins/lsp-plugins media-plugins/calf) ;;
  solus)  FULL_PKG=(python-lilv lsp-plugins calf) ;;
  nixos)  FULL_PKG=(python3Packages.lilv lsp-plugins calf) ;;
  *)      FULL_PKG=("lilv python bindings" "LSP plugins (LV2)" "Calf plugins (LV2)") ;;
esac
DNSMOS_URL="https://github.com/microsoft/DNS-Challenge/raw/master/DNSMOS/DNSMOS/sig_bak_ovr.onnx"
MODEL="$APP_DIR/models/sig_bak_ovr.onnx"
RECORD="$APP_DIR/installed-packages.txt"      # packages this installer installed (and nobody had before)

# ------------------------------------------------------------------------------------------------ packages
# pkg_installed NAME: 0 = installed, 1 = not installed, 2 = can't tell on this distro
pkg_installed() {
  case "$PM" in
    pacman) pacman -Q "$1" >/dev/null 2>&1 ;;
    apt)    dpkg-query -W -f='${Status}' "$1" 2>/dev/null | grep -q "install ok installed" ;;
    dnf|zypper) rpm -q "$1" >/dev/null 2>&1 ;;
    xbps)   xbps-query "$1" >/dev/null 2>&1 ;;
    apk)    apk info -e "$1" >/dev/null 2>&1 ;;
    *)      return 2 ;;
  esac
}
# needed_by NAME: prints the installed packages that depend on NAME ("" = nothing, "?" = can't tell)
needed_by() {
  set +o pipefail
  case "$PM" in
    pacman) pacman -Qi "$1" 2>/dev/null | awk '
              /^Required By/ {on=1; sub(/^Required By *: */, ""); print; next}
              on && /^[^ ]/  {exit}
              on             {sub(/^ +/, ""); print}' | tr -s ' \n' ' ' | sed 's/^None *$//; s/ *$//' ;;
    apt)    apt-cache rdepends --installed --no-recommends --no-suggests --no-conflicts --no-breaks \
              --no-replaces --no-enhances "$1" 2>/dev/null | tail -n +3 | sed 's/^[ |]*//' | sort -u | tr '\n' ' ' |
              sed 's/ *$//' ;;
    dnf)    dnf repoquery --installed --whatrequires "$1" -q 2>/dev/null | tr '\n' ' ' | sed 's/ *$//' ;;
    zypper) rpm -q --whatrequires "$1" 2>/dev/null | grep -v "no package requires" | tr '\n' ' ' | sed 's/ *$//' ;;
    xbps)   xbps-query -X "$1" 2>/dev/null | tr '\n' ' ' | sed 's/ *$//' ;;
    *)      echo "?" ;;
  esac
  set -o pipefail
}
case "$PM" in
  pacman) REMOVE=(sudo pacman -Rs) ;;
  apt)    REMOVE=(sudo apt-get remove -y) ;;
  dnf)    REMOVE=(sudo dnf remove -y) ;;
  zypper) REMOVE=(sudo zypper remove -y) ;;
  xbps)   REMOVE=(sudo xbps-remove -R) ;;
  apk)    REMOVE=(sudo apk del) ;;
  *)      REMOVE=() ;;
esac
# packages you may well use yourself, outside voice2ee: offered separately, default "no"
is_shared() {
  case "$1" in
    "${PKG[0]}"|"${PKG[1]}"|"${PKG[2]}"|"${PKG[3]}"|"${PKG[4]}"|"${PKG[5]}"|"${PKG[6]}"|deepfilternet-ladspa) return 0 ;;
    *) return 1 ;;
  esac
}
ask_default_no() {
  [ "$ASSUME_YES" = 1 ] && return 1                  # --yes never removes shared packages
  local a; read -r -p "$1 [y/N] " a || true
  case "${a,,}" in y|yes) return 0 ;; *) return 1 ;; esac
}
ask_default_yes() {
  [ "$ASSUME_YES" = 1 ] && return 0
  local a; read -r -p "$1 [Y/n] " a || true
  case "${a,,}" in ""|y|yes) return 0 ;; *) return 1 ;; esac
}

# ------------------------------------------------------------------------------------------------ uninstall
if [ "$UNINSTALL" = 1 ]; then
  RECORDED=()
  if [ -f "$RECORD" ]; then mapfile -t RECORDED < <(grep -v '^[[:space:]]*$' "$RECORD" | sort -u || true); fi
  rm -rf "$APP_DIR" "$BIN_DIR/voice2ee"
  echo "Removed $APP_DIR (incl. the Full version's virtualenv and model) and $BIN_DIR/voice2ee."
  echo "Kept: your EasyEffects presets (~/.local/share/easyeffects/input), your recording (~/.cache/voice2ee)"
  echo "      and your taste/locks (~/.config/voice2ee) - delete those folders if you like."
  echo
  OWN=() SHARED=() KEEP=()
  if [ ${#RECORDED[@]} -eq 0 ]; then
    echo "No record of system packages installed by voice2ee (none were, or they were installed by an older"
    echo "version of this installer). Nothing else to remove."
    for p in "${FULL_PKG[@]}"; do              # older installs: at least point at the Full extras
      if pkg_installed "$p" && [ -z "$(needed_by "$p" || true)" ]; then KEEP+=("$p"); fi
    done
    if [ ${#KEEP[@]} -gt 0 ] && [ ${#REMOVE[@]} -gt 0 ]; then
      echo "If an older voice2ee installed the Full extras and nothing else needs them, you can remove them with:"
      echo "  ${REMOVE[*]} ${KEEP[*]}"
    fi
    exit 0
  fi
  echo "${B}System packages voice2ee installed:${N}"
  for p in "${RECORDED[@]}"; do
    st=0; pkg_installed "$p" || st=$?
    if [ "$st" = 1 ]; then printf '  %-24s already removed\n' "$p"; continue; fi
    users="$(needed_by "$p" || true)"
    if [ "$users" = "?" ]; then printf '  %-24s %s\n' "$p" "can't check what else uses it here - kept"; KEEP+=("$p")
    elif [ -n "$users" ]; then printf '  %-24s %s\n' "$p" "kept - needed by: $users"; KEEP+=("$p")
    elif is_shared "$p"; then printf '  %-24s %s\n' "$p" "not needed by other packages, but you may use it yourself"; SHARED+=("$p")
    else printf '  %-24s %s\n' "$p" "only voice2ee needed it"; OWN+=("$p"); fi
  done
  TO_REMOVE=()
  if [ ${#OWN[@]} -gt 0 ] && ask_default_yes "Remove the packages only voice2ee needed (${OWN[*]})?"; then
    TO_REMOVE+=("${OWN[@]}")
  fi
  if [ ${#SHARED[@]} -gt 0 ]; then
    echo "  ${SHARED[*]}: nothing else depends on them, but they're things like EasyEffects or ffmpeg you may use."
    if ask_default_no "Remove them too?"; then TO_REMOVE+=("${SHARED[@]}"); fi
  fi
  if [ ${#TO_REMOVE[@]} -gt 0 ]; then
    if [ ${#REMOVE[@]} -gt 0 ]; then
      "${REMOVE[@]}" "${TO_REMOVE[@]}" || warn "The package manager reported a problem - check the output above."
    else
      echo "Remove them with your package manager: ${TO_REMOVE[*]}"
    fi
  else
    echo "System packages left in place."
  fi
  exit 0
fi

echo "${B}voice2ee installer${N}  -  $PRETTY_NAME${FAMILY:+ ($FAMILY family, $PM)}"
echo

# ------------------------------------------------------------------------------------------------ edition
explain_editions() {
cat <<TXT
${B}Which version do you want?${N}

  ${B}1) Minimal${N}  - Python + numpy + scipy + the PipeWire tools, and your ${B}real${N} EasyEffects plugins
     for Optimise and A/B picks (needs python-lilv, about 240 KB, plus the LSP/Calf plugins a native
     EasyEffects usually has already).
     ${G}+${N} Everything works: measuring, presets, tuning rounds, record-once, A/B picks and Optimise
     ${G}+${N} Optimise and A/B use the same plugins EasyEffects uses: what it picks is what you hear
     ${G}+${N} Small: nothing big to download
     ${Y}-${N} No speech-quality model helping Optimise choose
     ${B}Choose it if:${N} you want a great preset without big extras. (Next you can choose to skip the
       plugins and use voice2ee's built-in model instead, e.g. with the EasyEffects Flatpak.)

  ${B}2) Full${N}  - Minimal + Microsoft's DNSMOS speech-quality model: onnxruntime in a private
     virtualenv (about 60 MB) and the model itself (a few MB). Needs internet for the download.
     ${G}+${N} A quality score trained on human listening ratings helps Optimise choose between candidates
     ${Y}-${N} Bigger; onnxruntime comes from pip, not your distro
     ${Y}-${N} Optimising takes a little longer per try
     ${B}Choose it if:${N} you want the best possible fit, re-tune often, or use several mics or rooms.

  Switch any time:  ./install.sh --minimal   ./install.sh --full   (or --builtin for the built-in model only)
TXT
}
explain_plugins() {
cat <<TXT
${B}How should Minimal process your recording for Optimise and A/B picks?${N}

  ${B}1) Your real EasyEffects plugins${N} (recommended) - exact: the very plugins EasyEffects runs.
     Installs python-lilv (about 240 KB) and, if missing, the LSP and Calf LV2 plugins.
  ${B}2) Built-in model only${N} - nothing extra to install. Close, not exact: every result is checked and
     corrected through EasyEffects afterwards. Pick it with the EasyEffects ${B}Flatpak${N} (its plugins live
     inside the Flatpak), or if you don't want extra packages.

  (voice2ee always falls back to the built-in model by itself if the real plugins don't work.)
TXT
}
if [ -z "$EDITION" ] && [ "$CHECK_ONLY" = 0 ] && [ "$UNINSTALL" = 0 ]; then
  if [ -t 0 ] && [ "$ASSUME_YES" = 0 ]; then
    explain_editions
    while :; do
      read -r -p "Choose [1 = Minimal / 2 = Full, Enter = 1]: " a || a=""
      case "$a" in ""|1) EDITION=minimal; break ;; 2) EDITION=full; break ;; q|Q|quit|exit) exit 0 ;; esac
    done
    echo
  else
    EDITION=minimal
    echo "(Not interactive: installing the Minimal version. Use --full for the Full version.)"
  fi
fi
if [ "$EDITION" = full ]; then PLUGINS=real; fi
if [ "$EDITION" = minimal ] && [ -z "$PLUGINS" ] && [ "$CHECK_ONLY" = 0 ] && [ "$UNINSTALL" = 0 ]; then
  if [ -t 0 ] && [ "$ASSUME_YES" = 0 ]; then
    explain_plugins
    while :; do
      read -r -p "Choose [1 = real plugins / 2 = built-in model only, Enter = 1]: " a || a=""
      case "$a" in ""|1) PLUGINS=real; break ;; 2) PLUGINS=builtin; break ;; q|Q|quit|exit) exit 0 ;; esac
    done
    echo
  else
    PLUGINS=real
  fi
fi

# ------------------------------------------------------------------------------------------------ checks
MISSING_REQ=() MISSING_OPT=() PY=""
PY_OK=0 NUMPY_OK=0 SCIPY_OK=0

echo "${B}Required${N}"
for c in python3 python; do
  if have "$c" && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' 2>/dev/null; then PY="$c"; break; fi
done
if [ -n "$PY" ]; then
  PY_OK=1; ok "Python $("$PY" -c 'import platform; print(platform.python_version())')"
  if "$PY" -c 'import numpy' 2>/dev/null; then NUMPY_OK=1; ok "numpy $("$PY" -c 'import numpy; print(numpy.__version__)')"
  else bad "numpy"; MISSING_REQ+=("${PKG[1]}"); fi
  if "$PY" -c 'import scipy' 2>/dev/null; then SCIPY_OK=1; ok "scipy $("$PY" -c 'import scipy; print(scipy.__version__)')"
  else bad "scipy"; MISSING_REQ+=("${PKG[2]}"); fi
else
  bad "Python 3.9 or newer"; MISSING_REQ+=("${PKG[0]}" "${PKG[1]}" "${PKG[2]}")
fi
if have pactl && have parec; then ok "pactl + parec (recording)"
else bad "pactl / parec (recording)"; MISSING_REQ+=("${PKG[3]}"); fi

EE_KIND="" EE_VER=""
if have easyeffects; then
  EE_KIND="native"; EE_OUT="$(timeout 5 easyeffects --version 2>/dev/null || true)"
  EE_VER="$(grep -oE '[0-9]+\.[0-9]+\.[0-9]+' <<< "$EE_OUT" | head -1 || true)"
elif have flatpak && flatpak info com.github.wwmm.easyeffects >/dev/null 2>&1; then
  EE_KIND="flatpak"; FP_OUT="$(flatpak info com.github.wwmm.easyeffects 2>/dev/null || true)"
  EE_VER="$(awk -F': *' '/Version/{print $2; exit}' <<< "$FP_OUT")"
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
if have pw-loopback && { have pw-play || have paplay; }; then ok "pw-loopback + pw-play (record-once mode)"
else warn "pw-loopback / pw-play missing - record-once mode falls back to live rounds"; MISSING_OPT+=("${PKG[4]}"); fi
if have pactl; then
  PA_INFO="$(pactl info 2>/dev/null || true)"
  if grep -qi pipewire <<< "$PA_INFO"; then ok "PipeWire is the running sound server"
  else warn "PipeWire doesn't seem to be running (EasyEffects needs PipeWire)"; fi
fi
SOCK="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/EasyEffectsServer"
if [ -S "$SOCK" ] || [ -S "${XDG_RUNTIME_DIR:-/run/user/$(id -u)}/app/com.github.wwmm.easyeffects/EasyEffectsServer" ]; then
  ok "EasyEffects local server is running"
else warn "EasyEffects local server not found right now (start EasyEffects before using voice2ee)"; fi

# DeepFilterNet: the neural noise remover behind EasyEffects' "Deep noise remover" (optional, better than RNNoise
# on keyboard clicks). Only Arch packages it in the main repos; elsewhere it's one file from the upstream releases.
DEEP_FOUND=0
LP="${LADSPA_PATH:-}"
for d in ${LP//:/ } /usr/lib/ladspa /usr/lib64/ladspa /usr/local/lib/ladspa /usr/local/lib64/ladspa \
         /usr/lib/x86_64-linux-gnu/ladspa /usr/lib/aarch64-linux-gnu/ladspa "$HOME/.ladspa"; do
  [ -f "$d/libdeep_filter_ladspa.so" ] && DEEP_FOUND=1 && break
done
if [ "$DEEP_FOUND" = 1 ]; then ok "DeepFilterNet (EasyEffects' Deep noise remover)"
elif [ "$EE_KIND" = flatpak ]; then
  warn "DeepFilterNet: can't check inside the Flatpak - if EasyEffects says the Deep noise remover is"
  warn "  unavailable, see 'DeepFilterNet' in the README"
elif [ "$FAMILY" = arch ]; then
  warn "DeepFilterNet missing (optional: EasyEffects' Deep noise remover) - package deepfilternet-ladspa"
  MISSING_OPT+=(deepfilternet-ladspa)
else
  warn "DeepFilterNet missing (optional: EasyEffects' Deep noise remover). Install it by hand:"
  warn "  download libdeep_filter_ladspa-<version>-x86_64-unknown-linux-gnu.so from"
  warn "  https://github.com/Rikorose/DeepFilterNet/releases and copy it to"
  warn "  /usr/lib64/ladspa/libdeep_filter_ladspa.so (Fedora/openSUSE) or /usr/lib/ladspa/ (others),"
  warn "  then restart EasyEffects"
fi

echo
echo "${B}Real plugins and quality model${N}${EDITION:+ (you chose: $EDITION${PLUGINS:+, ${PLUGINS} plugins})}"
# Full-version parts. full_checks PYTHON [quiet]: sets LILV_OK LV2_OK ORT_OK MODEL_OK and FULL_MISSING.
full_checks() {
  local py="$1" q="${2:-}"
  FULL_MISSING=() LILV_OK=0 LV2_OK=0 ORT_OK=0 MODEL_OK=0
  say_ok()   { [ -n "$q" ] || ok "$@"; }
  say_warn() { [ -n "$q" ] || warn "$@"; }
  if [ -n "$py" ] && "$py" -c 'import lilv' 2>/dev/null; then LILV_OK=1; say_ok "lilv python bindings"
  else say_warn "lilv python bindings (${FULL_PKG[0]}) - needed for your real plugins offline"; FULL_MISSING+=("${FULL_PKG[0]}"); fi
  local LSP_FOUND=0 CALF_FOUND=0 LV="${LV2_PATH:-}" d
  for d in ${LV//:/ } /usr/lib/lv2 /usr/lib64/lv2 /usr/local/lib/lv2 /usr/local/lib64/lv2 \
           /usr/lib/x86_64-linux-gnu/lv2 /usr/lib/aarch64-linux-gnu/lv2 "$HOME/.lv2"; do
    compgen -G "$d/lsp-plugins*.lv2" >/dev/null 2>&1 && LSP_FOUND=1
    [ -d "$d/calf.lv2" ] && CALF_FOUND=1
  done
  if [ "$LSP_FOUND" = 1 ]; then say_ok "LSP plugins (LV2)"; else say_warn "LSP plugins (LV2) (${FULL_PKG[1]})"; FULL_MISSING+=("${FULL_PKG[1]}"); fi
  if [ "$CALF_FOUND" = 1 ]; then say_ok "Calf plugins (LV2) - de-esser, exciter"
  else say_warn "Calf plugins (LV2) - de-esser, exciter (${FULL_PKG[2]})"; FULL_MISSING+=("${FULL_PKG[2]}"); fi
  [ "$LSP_FOUND" = 1 ] && [ "$CALF_FOUND" = 1 ] && LV2_OK=1
  if { [ -x "$APP_DIR/venv/bin/python" ] && "$APP_DIR/venv/bin/python" -c 'import onnxruntime' 2>/dev/null; } ||
     { [ -n "$py" ] && "$py" -c 'import onnxruntime' 2>/dev/null; }; then ORT_OK=1; say_ok "onnxruntime"
  else say_warn "onnxruntime - Full version's quality model (pip, voice2ee's own virtualenv)"; fi
  if [ -s "$MODEL" ]; then MODEL_OK=1; say_ok "DNSMOS model"; else say_warn "DNSMOS model - Full version (downloaded by --full)"; fi
  return 0
}
full_checks "$PY"
[ "$EE_KIND" = flatpak ] && warn "EasyEffects is a Flatpak: offline plugins are your system's, which may differ in version"

echo
echo "${B}Optional${N} (only for running the tests)"
if have ffmpeg; then
  FF_FILTERS="$(ffmpeg -hide_banner -filters 2>/dev/null || true)"
  if grep -q ' flite ' <<< "$FF_FILTERS"; then ok "ffmpeg with flite (test speech)"
  else warn "ffmpeg without flite - tests that need synthetic speech will be skipped"; fi
else warn "ffmpeg - tests that need synthetic speech will be skipped"; MISSING_OPT+=("${PKG[6]}"); fi

echo
if [ "$CHECK_ONLY" = 1 ]; then
  if [ ${#MISSING_REQ[@]} -eq 0 ]; then echo "${G}All required dependencies are installed.${N}"
  else echo "Missing required packages: ${B}${MISSING_REQ[*]}${N}"; fi
  [ ${#MISSING_OPT[@]} -gt 0 ] && echo "Missing recommended/optional packages: ${MISSING_OPT[*]}"
  if [ "$LILV_OK$LV2_OK" = 11 ]; then echo "Real plugins (Minimal and Full): ${G}ready${N}"
  else echo "Real plugins (Minimal and Full): missing ${FULL_MISSING[*]:-}"; fi
  if [ "$ORT_OK$MODEL_OK" = 11 ]; then echo "Quality model (Full): ${G}ready${N}"
  else echo "Quality model (Full): missing$([ $ORT_OK = 0 ] && echo ' onnxruntime')$([ $MODEL_OK = 0 ] && echo ' DNSMOS-model')"; fi
  echo "  (run ./install.sh to see which version fits you)"
  exit 0
fi

# ------------------------------------------------------------------------------------------------ install deps
reason_for() {
  case "$1" in
    "${PKG[0]}") echo "Python (required)" ;;
    "${PKG[1]}") echo "numpy (required)" ;;
    "${PKG[2]}") echo "scipy (required)" ;;
    "${PKG[3]}") echo "pactl + parec: recording (required)" ;;
    "${PKG[4]}") echo "pw-loopback + pw-play: record-once mode (recommended)" ;;
    "${PKG[5]}") echo "EasyEffects (required)" ;;
    "${PKG[6]}") echo "ffmpeg: test speech for the test suite (optional)" ;;
    "${FULL_PKG[0]}") echo "lilv python bindings: your real plugins offline" ;;
    "${FULL_PKG[1]}") echo "LSP plugins (LV2): your real plugins offline" ;;
    "${FULL_PKG[2]}") echo "Calf plugins (LV2): de-esser and exciter offline" ;;
    deepfilternet-ladspa) echo "DeepFilterNet: EasyEffects' Deep noise remover (optional)" ;;
    *) echo "" ;;
  esac
}

confirm() {
  [ "$ASSUME_YES" = 1 ] && return 0
  local a; read -r -p "$1 [Y/n] " a || true
  case "${a,,}" in ""|y|yes) return 0 ;; *) return 1 ;; esac
}

# with --venv, numpy/scipy come from pip instead of the distro
if [ "$USE_VENV" = 1 ]; then
  keep=()
  for p in "${MISSING_REQ[@]}"; do
    [ "$p" = "${PKG[1]}" ] || [ "$p" = "${PKG[2]}" ] || keep+=("$p")
  done
  MISSING_REQ=("${keep[@]}")
fi

[ "$PLUGINS" = real ] && MISSING_OPT+=("${FULL_MISSING[@]}")
if [ "$NO_DEPS" = 0 ] && [ $(( ${#MISSING_REQ[@]} + ${#MISSING_OPT[@]} )) -gt 0 ]; then
  TO_INSTALL=("${MISSING_REQ[@]}" "${MISSING_OPT[@]}")
  if [ ${#INSTALL[@]} -gt 0 ]; then
    echo "${B}These packages are missing:${N}"
    for p in "${TO_INSTALL[@]}"; do printf '  %-24s %s\n' "$p" "$(reason_for "$p")"; done
    if confirm "Install them now with: ${INSTALL[*]} ...?"; then
      BEFORE=()
      for p in "${TO_INSTALL[@]}"; do st=0; pkg_installed "$p" || st=$?; [ "$st" = 1 ] && BEFORE+=("$p"); done
      "${INSTALL[@]}" "${TO_INSTALL[@]}" || warn "The package manager reported a problem - check the output above."
      for p in "${BEFORE[@]}"; do                 # remember only what this run actually added
        if pkg_installed "$p"; then mkdir -p "$APP_DIR"; echo "$p" >> "$RECORD"; fi
      done
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
if [ -z "$PY" ]; then bad "Python 3.9+ is still missing - can't install voice2ee."; exit 1; fi

# ------------------------------------------------------------------------------------------------ install tool
echo "${B}Installing voice2ee${N} to $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR"
rm -rf "$APP_DIR/v2ee"                          # (keeps installed-packages.txt, venv, models)
cp -r "$SRC_DIR/v2ee" "$APP_DIR/"
cp "$SRC_DIR/voice2ee.py" "$SRC_DIR/README.md" "$APP_DIR/"
find "$APP_DIR" -name '__pycache__' -prune -exec rm -rf {} +

RUN_PY="$PY"
VENV_PKGS=()
[ "$USE_VENV" = 1 ] && VENV_PKGS+=(numpy scipy)
[ "$EDITION" = full ] && VENV_PKGS+=(onnxruntime)
# a virtualenv made with an older Python (e.g. before a system update) can't see the new system packages
if [ -x "$APP_DIR/venv/bin/python" ]; then
  VV="$("$APP_DIR/venv/bin/python" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo broken)"
  SV="$("$PY" -c 'import sys; print(sys.version_info[:2])' 2>/dev/null || echo none)"
  if [ "$VV" != "$SV" ]; then rm -rf "$APP_DIR/venv"; warn "rebuilding voice2ee's virtualenv (Python changed: $VV -> $SV)"; fi
fi
if [ "$EDITION" = full ] && [ "$USE_VENV" = 0 ] && [ -x "$APP_DIR/venv/bin/python" ] &&
   "$APP_DIR/venv/bin/python" -c 'import onnxruntime' 2>/dev/null; then
  VENV_PKGS=(); RUN_PY="$APP_DIR/venv/bin/python"; ok "virtualenv with onnxruntime already set up"
fi
if [ "$EDITION" = full ] && [ "$USE_VENV" = 0 ] && [ ${#VENV_PKGS[@]} -gt 0 ] &&
   ! confirm "Install onnxruntime (about 15 MB download, ~60 MB on disk) with pip into voice2ee's own virtualenv?"; then
  VENV_PKGS=(); warn "skipped onnxruntime - the quality model stays off"
fi
if [ ${#VENV_PKGS[@]} -gt 0 ]; then
  # --system-site-packages: the venv still sees your distro's numpy/scipy and lilv
  echo "  setting up voice2ee's own virtualenv with: ${VENV_PKGS[*]} (needs internet)..."
  if "$PY" -m venv --system-site-packages "$APP_DIR/venv" &&
     "$APP_DIR/venv/bin/pip" install --quiet --disable-pip-version-check "${VENV_PKGS[@]}"; then
    RUN_PY="$APP_DIR/venv/bin/python"; ok "virtualenv ready"
  else
    if [ "$USE_VENV" = 1 ]; then bad "couldn't create the virtualenv (is python3-venv / python3-pip installed?)"; exit 1; fi
    warn "couldn't install onnxruntime (no internet, no python3-venv/pip, or no wheel for Python $("$PY" -c 'import platform; print(platform.python_version())'))"
    warn "  -> the quality model stays off; everything else in the Full version still works"
    [ -x "$APP_DIR/venv/bin/python" ] && RUN_PY="$APP_DIR/venv/bin/python"
  fi
elif [ "$EDITION" = minimal ] && [ -d "$APP_DIR/venv" ]; then
  rm -rf "$APP_DIR/venv" "$APP_DIR/models"; ok "removed the Full version's extras (virtualenv, model)"
fi

if [ "$EDITION" = full ] && [ ! -s "$MODEL" ] &&
   confirm "Download the DNSMOS speech-quality model (a few MB, from Microsoft's DNS Challenge)?"; then
  echo "  downloading the DNSMOS speech-quality model..."
  mkdir -p "$APP_DIR/models"
  if { have curl && curl -fsSL "$DNSMOS_URL" -o "$MODEL.part"; } || { have wget && wget -q "$DNSMOS_URL" -O "$MODEL.part"; }; then
    mv "$MODEL.part" "$MODEL"; ok "DNSMOS model ready"
  else
    rm -f "$MODEL.part"; warn "couldn't download the model - the quality score stays off (run ./install.sh --full again later)"
  fi
fi

cat > "$BIN_DIR/voice2ee" <<EOF
#!/bin/sh
# voice2ee launcher (installed by install.sh)
VOICE2EE_DATA="$APP_DIR" exec "$RUN_PY" "$APP_DIR/voice2ee.py" "\$@"
EOF
chmod +x "$BIN_DIR/voice2ee"
ok "command installed: $BIN_DIR/voice2ee"

if ! "$RUN_PY" -c 'import numpy, scipy' 2>/dev/null; then
  warn "numpy/scipy still can't be imported by $RUN_PY - install them (or re-run with --venv) before using voice2ee."
fi
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on your PATH. Add this to ~/.bashrc or ~/.zshrc:"
     echo "      export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac
echo
STATUS=""
if [ "$PLUGINS" = real ]; then
  echo "Checking your setup (runs your LV2 plugins once in a separate process)..."
  CHECK_OK=1
  EXPECT_Q=""; [ "$EDITION" = full ] && EXPECT_Q=1
  VOICE2EE_DATA="$APP_DIR" VOICE2EE_FROM_INSTALLER=1 VOICE2EE_EXPECT_QUALITY="$EXPECT_Q" \
    "$RUN_PY" "$APP_DIR/voice2ee.py" --check-full || CHECK_OK=0
  full_checks "$RUN_PY" quiet
  STILL=("${FULL_MISSING[@]}")
  if [ "$CHECK_OK" = 0 ] && [ ${#FULL_MISSING[@]} -eq 0 ]; then
    STILL+=("working-real-plugins")       # installed, but the self-test above failed
  fi
  if [ "$EDITION" = full ]; then
    [ "$ORT_OK" = 0 ] && STILL+=("onnxruntime")
    [ "$MODEL_OK" = 0 ] && STILL+=("DNSMOS-model")
  fi
  if [ ${#STILL[@]} -eq 0 ]; then STATUS=" - all parts working"
  else
    echo
    warn "Still missing: ${STILL[*]}"
    for p in "${FULL_MISSING[@]}"; do
      if [ ${#INSTALL[@]} -gt 0 ]; then warn "  ${INSTALL[*]} $p      # $(reason_for "$p")"
      else warn "  install: $p ($(reason_for "$p"))"; fi
    done
    if [ "$CHECK_OK" = 0 ] && [ ${#FULL_MISSING[@]} -eq 0 ]; then
      warn "  the real plugins are installed but the self-test above failed - please report the lines it printed"
    fi
    [ "$ORT_OK" = 0 ] && warn "  onnxruntime: run ./install.sh --full again with internet (needs python venv + pip)"
    [ "$MODEL_OK" = 0 ] && warn "  DNSMOS model: run ./install.sh --full again with internet"
    warn "Until then voice2ee uses its built-in model / no quality score for those parts (everything still works)."
    STATUS=", some parts missing (see above)"
  fi
fi
echo
if [ -z "$STATUS" ] || [ "$STATUS" = " - all parts working" ]; then DC="$G"; MARK="✓"; else DC="$Y"; MARK="!"; fi
[ "$PLUGINS" = builtin ] && STATUS=", built-in plugin model"
echo "${DC}${MARK} Done (${EDITION:-minimal} version${STATUS}).${N} Start EasyEffects, then run:  ${B}voice2ee${N}   (voice2ee --help for options)"
