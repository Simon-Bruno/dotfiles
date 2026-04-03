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
        stow --adopt -t ~ hypr eww nvim
        ;;
    fedora|fedora-asahi-remix)
        git config --global user.email "simonadrbruno@gmail.com"
        git config --global user.name "Simon Bruno"
        sudo dnf copr enable -y sdegler/hyprland
        sudo dnf install -y $(grep -v '^eww$' "$DOTFILES/packages/fedora.txt" | tr '\n' ' ') \
            rust cargo gtk3-devel glib2-devel pango-devel gdk-pixbuf2-devel cairo-devel atk-devel libdbusmenu-devel libdbusmenu-gtk3-devel gtk-layer-shell-devel
        # Build eww from source (no aarch64 COPR available)
        if ! command -v eww &>/dev/null; then
            rm -rf /tmp/eww && git clone https://github.com/elkowar/eww /tmp/eww
            cd /tmp/eww
            cargo build --release --no-default-features --features wayland
            sudo install -m755 target/release/eww /usr/local/bin/eww
            cd "$DOTFILES"
        fi
        # Install Nerd Fonts Symbols
        mkdir -p ~/.local/share/fonts
        curl -fLo ~/.local/share/fonts/NerdFontsSymbolsOnly.zip \
            https://github.com/ryanoasis/nerd-fonts/releases/latest/download/NerdFontsSymbolsOnly.zip
        unzip -o ~/.local/share/fonts/NerdFontsSymbolsOnly.zip -d ~/.local/share/fonts/
        fc-cache -fv
        cd "$DOTFILES"
        stow --adopt -t ~ hypr eww nvim
        ;;
    *)
        echo "Unsupported OS: $OS. Install packages manually from packages/."
        ;;
esac

echo "Done!"
echo "On first nvim launch, LazyVim will bootstrap and install plugins automatically."
