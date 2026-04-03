#!/usr/bin/env bash
set -e

DOTFILES="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Detect OS
if [[ "$OSTYPE" == "darwin"* ]]; then
    OS="mac"
elif [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
else
    echo "Cannot detect OS. Exiting."
    exit 1
fi

echo "Detected OS: $OS"

case "$OS" in
    mac)
        if ! command -v brew &>/dev/null; then
            echo "Homebrew not found. Install it from https://brew.sh"
            exit 1
        fi
        brew install neovim stow git
        cd "$DOTFILES"
        stow -t ~ nvim
        ;;
    arch|endeavouros|manjaro)
        if command -v yay &>/dev/null; then
            yay -S --needed - < "$DOTFILES/packages/arch.txt"
        else
            sudo pacman -S --needed - < "$DOTFILES/packages/arch.txt"
        fi
        cd "$DOTFILES"
        stow -t ~ hypr eww nvim
        ;;
    fedora)
        sudo dnf install -y $(cat "$DOTFILES/packages/fedora.txt" | tr '\n' ' ')
        cd "$DOTFILES"
        stow -t ~ hypr eww nvim
        ;;
    *)
        echo "Unsupported OS: $OS. Install packages manually from packages/."
        ;;
esac

echo "Done!"
echo "On first nvim launch, LazyVim will bootstrap and install plugins automatically."
