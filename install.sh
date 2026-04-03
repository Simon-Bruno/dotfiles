#!/usr/bin/env bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO=$ID
else
    echo "Cannot detect distro. Exiting."
    exit 1
fi

echo "Detected distro: $DISTRO"

# Install packages
case "$DISTRO" in
    arch|endeavouros|manjaro)
        if command -v yay &>/dev/null; then
            yay -S --needed - < "$DOTFILES/packages/arch.txt"
        else
            sudo pacman -S --needed - < "$DOTFILES/packages/arch.txt"
        fi
        ;;
    fedora)
        sudo dnf install -y $(cat "$DOTFILES/packages/fedora.txt" | tr '\n' ' ')
        ;;
    *)
        echo "Unsupported distro: $DISTRO. Install packages manually from packages/."
        ;;
esac

# Stow configs
cd "$DOTFILES"
stow -t ~ hypr eww dunst nvim

echo "Done! Log out and back in to start Hyprland."
echo "On first nvim launch, LazyVim will bootstrap and install plugins automatically."
