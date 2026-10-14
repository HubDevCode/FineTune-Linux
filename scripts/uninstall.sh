#!/bin/sh
# Remove a FineTune installation (settings in ~/.config/FineTune are kept).
PREFIX="${1:-$HOME/.local}"
finetune --quit 2>/dev/null || true
rm -rf "$PREFIX/lib/finetune" "$PREFIX/share/finetune" "$PREFIX/bin/finetune" \
       "$PREFIX/share/applications/io.github.hubdevcode.FineTune.desktop" \
       "$PREFIX/share/icons/hicolor/scalable/apps/io.github.hubdevcode.FineTune.svg"
rm -f "$PREFIX"/share/icons/hicolor/symbolic/apps/ft-*-symbolic.svg "$PREFIX/share/icons/hicolor/symbolic/apps/finetune-symbolic.svg"
rm -f "${XDG_CONFIG_HOME:-$HOME/.config}/autostart/io.github.hubdevcode.FineTune.desktop"
echo "FineTune removed from $PREFIX"
