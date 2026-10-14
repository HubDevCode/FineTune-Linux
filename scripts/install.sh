#!/bin/sh
# Install FineTune for the current user (default) or system-wide:
#   scripts/install.sh               → ~/.local
#   sudo scripts/install.sh /usr     → /usr
set -e
PREFIX="${1:-$HOME/.local}"
SRC="$(cd "$(dirname "$0")/.." && pwd)"

echo "Building audio engine…"
make -C "$SRC/engine"

echo "Installing to $PREFIX"
install -Dm755 "$SRC/engine/finetune-engine" "$PREFIX/lib/finetune/finetune-engine"
mkdir -p "$PREFIX/share/finetune"
rm -rf "$PREFIX/share/finetune/finetune"
cp -r "$SRC/finetune" "$PREFIX/share/finetune/finetune"
find "$PREFIX/share/finetune/finetune" -name __pycache__ -type d -prune -exec rm -rf {} +
cp -r "$SRC/data/icons" "$PREFIX/share/finetune/"
install -Dm644 "$SRC/data/style.css" "$PREFIX/share/finetune/style.css"
install -Dm755 "$SRC/bin/finetune" "$PREFIX/bin/finetune"
# Absolute Exec path: the desktop portal (global shortcuts) runs without ~/.local/bin in PATH
# and treats a desktop file whose Exec isn't found as missing.
install -d "$PREFIX/share/applications"
sed "s|^Exec=finetune|Exec=$PREFIX/bin/finetune|" "$SRC/data/io.github.hubdevcode.FineTune.desktop" \
    > "$PREFIX/share/applications/io.github.hubdevcode.FineTune.desktop"
install -Dm644 "$SRC/data/icons/hicolor/scalable/apps/io.github.hubdevcode.FineTune.svg" \
    "$PREFIX/share/icons/hicolor/scalable/apps/io.github.hubdevcode.FineTune.svg"
for f in "$SRC"/data/icons/hicolor/symbolic/apps/*.svg; do
    install -Dm644 "$f" "$PREFIX/share/icons/hicolor/symbolic/apps/$(basename "$f")"
done

command -v update-desktop-database >/dev/null && update-desktop-database "$PREFIX/share/applications" || true
[ -f "$PREFIX/share/icons/hicolor/index.theme" ] && command -v gtk-update-icon-cache >/dev/null && gtk-update-icon-cache -q "$PREFIX/share/icons/hicolor" || true
command -v xdg-mime >/dev/null && xdg-mime default io.github.hubdevcode.FineTune.desktop x-scheme-handler/finetune || true

case ":$PATH:" in *":$PREFIX/bin:"*) ;; *) echo "Note: add $PREFIX/bin to your PATH";; esac
echo "Keybindings: compositors often run binds without $PREFIX/bin in PATH, so use the full path, e.g."
echo "  bind = SUPER, A, exec, $PREFIX/bin/finetune --action togglePopup"
echo "Done. Start it with: finetune   (or from your app launcher)"
