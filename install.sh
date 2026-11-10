#!/bin/sh
set -eu

cd -- "$(dirname -- "$0")"

uv_bin=${UV_INSTALL_DIR:-${XDG_BIN_HOME:-"$HOME/.local/bin"}}
if command -v uv >/dev/null 2>&1; then
    uv_command=$(command -v uv)
elif [ -x "$uv_bin/uv" ]; then
    uv_command="$uv_bin/uv"
else
    printf '%s\n' 'Installing uv for the current user...'
    installer=$(mktemp)
    trap 'rm -f "$installer"' 0
    trap 'exit 1' HUP INT TERM
    if command -v curl >/dev/null 2>&1; then
        curl -fsSL --proto '=https' --proto-redir '=https' https://astral.sh/uv/install.sh -o "$installer"
    elif command -v wget >/dev/null 2>&1; then
        wget -qO "$installer" https://astral.sh/uv/install.sh
    else
        printf '%s\n' 'Install curl, wget or uv and run this installer again.' >&2
        exit 1
    fi
    UV_INSTALL_DIR="$uv_bin" UV_NO_MODIFY_PATH=1 sh "$installer"
    uv_command="$uv_bin/uv"
fi

"$uv_command" tool install --python 3.12 --reinstall-package cdmbot '.[managed-java]'
tool_bin=$("$uv_command" tool dir --bin)
"$tool_bin/cdmbot" examples/cdm8/inputs.asm examples/cdm8/inputs.yaml
"$tool_bin/cdmbot" examples/cdm16/add.asm examples/cdm16/add.yaml --case simple

if [ "${CDMBOT_NO_PATH_UPDATE:-0}" != 1 ]; then
    if "$uv_command" tool update-shell; then
        printf '%s\n' 'Open a new terminal and run cdmbot --help.'
    else
        printf '%s\n' "To enable the cdmbot command, add $tool_bin to your PATH."
    fi
fi
printf '%s\n' "CDMBot is ready: $tool_bin/cdmbot"
