#!/usr/bin/env bash
# gazehop installer for Ubuntu / Linux.
# Checks that this machine can run gazehop, installs what is missing (asks before using sudo),
# and on GNOME Wayland sets up the "Window Calls" extension gazehop needs there.
set -u
cd "$(dirname "$0")"

EXT_UUID="window-calls@domandoman.xyz"
EXT_REPO="https://github.com/ickyicky/window-calls"
EXT_SHELL_MIN=45
EXT_SHELL_MAX=50

ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
warn() { printf '  \033[33mnote\033[0m  %s\n' "$*"; }
fail() { printf '  \033[31mSTOP\033[0m  %s\n' "$*"; exit 1; }
ask()  { read -r -p "$1 [Y/n] " a; [[ -z "$a" || "$a" =~ ^[Yy] ]]; }
windows_dbus() {
    gdbus call --session --dest org.gnome.Shell --object-path /org/gnome/Shell/Extensions/Windows \
        --method org.gnome.Shell.Extensions.Windows.List >/dev/null 2>&1
}

echo "== 1. Is this machine compatible? =="

[[ "$(uname -s)" == "Linux" ]] || fail "This installer is for Linux. On Windows use install.bat."

command -v python3 >/dev/null || fail "python3 not found. Install it: sudo apt install python3"
if python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    ok "Python $(python3 -c 'import platform; print(platform.python_version())')"
else
    fail "Python 3.10 or newer is needed (found $(python3 --version 2>&1))."
fi

if compgen -G "/dev/video*" >/dev/null; then
    ok "webcam device found"
else
    warn "no webcam found at /dev/video* — plug one in before running gazehop"
fi

SESSION="${XDG_SESSION_TYPE:-unknown}"
DESKTOP="$(echo "${XDG_CURRENT_DESKTOP:-}${DESKTOP_SESSION:-}" | tr '[:upper:]' '[:lower:]')"
NEED_EXT=0
case "$SESSION" in
    x11)
        ok "X11 session — works without any extension" ;;
    wayland)
        if [[ "$DESKTOP" == *gnome* || "$DESKTOP" == *ubuntu* ]]; then
            command -v gdbus >/dev/null || fail "gdbus not found. Install it: sudo apt install libglib2.0-bin"
            SHELL_VER="$(gnome-shell --version 2>/dev/null | grep -oE '[0-9]+' | head -1)"
            if [[ -z "$SHELL_VER" ]]; then
                warn "could not read the GNOME Shell version; continuing"
            elif (( SHELL_VER < EXT_SHELL_MIN || SHELL_VER > EXT_SHELL_MAX )); then
                fail "GNOME Shell $SHELL_VER is outside what Window Calls supports ($EXT_SHELL_MIN-$EXT_SHELL_MAX). Log in with an X11 session instead, if your system offers one."
            else
                ok "GNOME Shell $SHELL_VER on Wayland — supported through the Window Calls extension"
            fi
            NEED_EXT=1
        else
            fail "Wayland session on '${XDG_CURRENT_DESKTOP:-unknown}'. gazehop supports Wayland only on GNOME. Log in with an X11 session instead."
        fi ;;
    *)
        warn "unknown session type '$SESSION' (run this from a terminal inside your desktop, not over SSH)" ;;
esac

echo
echo "== 2. System packages =="
MISSING=()
python3 -c 'import tkinter' 2>/dev/null || MISSING+=(python3-tk)
python3 -c 'import ensurepip, venv' 2>/dev/null || MISSING+=(python3-venv)
if (( ${#MISSING[@]} )); then
    if command -v apt-get >/dev/null; then
        echo "  Missing: ${MISSING[*]}"
        if ask "  Install them now with sudo apt?"; then
            sudo apt-get install -y "${MISSING[@]}" || fail "apt could not install ${MISSING[*]}"
        else
            fail "Install them yourself, then run this again: sudo apt install ${MISSING[*]}"
        fi
    else
        fail "Missing ${MISSING[*]} and this is not an apt-based system; install the equivalents and rerun."
    fi
fi
ok "python3-tk and python3-venv present"

echo
echo "== 3. Python packages (into ./.venv, this takes a few minutes the first time) =="
[[ -x .venv/bin/python ]] || python3 -m venv .venv || fail "could not create the virtual environment"
# keep system packages out of sight while pip decides what to install...
sed -i 's/^include-system-site-packages.*/include-system-site-packages = false/' .venv/pyvenv.cfg
.venv/bin/python -m pip install --quiet --upgrade pip
.venv/bin/python -m pip install --quiet -r requirements.txt || fail "pip install failed (see the messages above)"
ok "packages installed"
# ...then let the environment see them: the tray icon needs the system's GTK bindings (python3-gi)
sed -i 's/^include-system-site-packages.*/include-system-site-packages = true/' .venv/pyvenv.cfg
.venv/bin/python -m gazehop selftest >/dev/null 2>&1 && ok "gazehop self-check passed" || warn "self-check failed: run ./run.sh selftest to see why"

RELOGIN=0
if (( NEED_EXT )); then
    echo
    echo "== 4. Window Calls extension (GNOME Wayland) =="
    if [[ "$(gsettings get org.gnome.shell disable-user-extensions 2>/dev/null)" == "true" ]]; then
        gsettings set org.gnome.shell disable-user-extensions false
        ok "turned GNOME user extensions on (they were disabled)"
    fi
    if windows_dbus; then
        ok "already installed and answering"
    else
        EXT_DIR="$HOME/.local/share/gnome-shell/extensions/$EXT_UUID"
        SYS_DIR="/usr/share/gnome-shell/extensions/$EXT_UUID"
        ext_complete() { [[ -f "$1/metadata.json" && -f "$1/extension.js" && ! -e "$1/.git" ]]; }
        if ! ext_complete "$EXT_DIR" && ! ext_complete "$SYS_DIR"; then
            if [[ ! -e "$EXT_DIR" ]]; then
                echo "  Asking GNOME to install it — if a dialog appears, click Install."
                RESULT="$(gdbus call --session --timeout 180 --dest org.gnome.Shell.Extensions \
                    --object-path /org/gnome/Shell/Extensions \
                    --method org.gnome.Shell.Extensions.InstallRemoteExtension "$EXT_UUID" 2>&1)"
                # GNOME sometimes drops the reply but still finishes the install: give it a moment
                for _ in 1 2 3 4 5 6 7 8; do ext_complete "$EXT_DIR" && break; sleep 1; done
                ext_complete "$EXT_DIR" || warn "GNOME did not install it (${RESULT:-no answer}); fetching it from GitHub instead"
            fi
            if ! ext_complete "$EXT_DIR"; then
                # download to a scratch folder first, so a half-written folder is never left behind
                command -v git >/dev/null || fail "git not found. Install it (sudo apt install git) or add the extension by hand from https://extensions.gnome.org/extension/4724/window-calls/ and rerun."
                TMP="$(mktemp -d)"
                git clone --quiet --depth 1 "$EXT_REPO" "$TMP/ext" || { rm -rf "$TMP"; fail "could not download $EXT_REPO"; }
                rm -rf "$TMP/ext/.git" "$EXT_DIR"
                mkdir -p "$(dirname "$EXT_DIR")"
                mv "$TMP/ext" "$EXT_DIR"
                rm -rf "$TMP"
                ext_complete "$EXT_DIR" || fail "the downloaded extension looks incomplete: check $EXT_DIR"
                ok "extension files placed in $EXT_DIR"
            fi
        fi
        gnome-extensions enable "$EXT_UUID" >/dev/null 2>&1
        sleep 2
        if windows_dbus; then
            ok "installed, enabled and answering"
        else
            RELOGIN=1
            warn "installed, but GNOME only loads new extensions at login on Wayland"
        fi
    fi
fi

echo
echo "== 5. App icon and tray =="
tray_ok() { python3 -c "import gi; gi.require_version('AyatanaAppIndicator3', '0.1')" 2>/dev/null \
         || python3 -c "import gi; gi.require_version('AppIndicator3', '0.1')" 2>/dev/null; }
if tray_ok; then
    ok "tray icon support present"
elif command -v apt-get >/dev/null && ask "  Install tray icon support (python3-gi, gir1.2-ayatanaappindicator3-0.1) with sudo apt?"; then
    sudo apt-get install -y python3-gi gir1.2-ayatanaappindicator3-0.1 && ok "tray icon support installed" \
        || warn "could not install it; gazehop will run without a tray icon"
else
    warn "no tray icon support; gazehop will run without a tray icon (its window still works)"
fi
if [[ "$DESKTOP" == *gnome* || "$DESKTOP" == *ubuntu* ]] && command -v gnome-extensions >/dev/null \
        && ! gnome-extensions list --enabled 2>/dev/null | grep -qi appindicator; then
    warn "GNOME only shows tray icons with the 'Ubuntu AppIndicators' extension enabled (Extensions app)"
fi

HERE="$(pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
cat > "$APPS/gazehop.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=gazehop
Comment=Keyboard focus follows your eyes
Exec="$HERE/run.sh" --window
Icon=$HERE/gazehop/assets/icon.png
Terminal=false
Categories=Utility;Accessibility;
DESKTOP
chmod +x "$APPS/gazehop.desktop"
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" >/dev/null 2>&1
ok "added gazehop to your applications menu"

chmod +x run.sh 2>/dev/null
echo
echo "== Done =="
if (( RELOGIN )); then
    echo "Log out and back in, then run ./install.sh once more to confirm the extension is active."
else
    echo "Open the app:                find gazehop in your applications menu, or  ./run.sh --window"
    echo "Check what gazehop sees:     ./run.sh doctor"
    echo "Try it without risk:         ./run.sh --window --dry-run --dot"
    echo "Terminal only:               ./run.sh"
fi
