#!/usr/bin/env bash
set -euo pipefail
if [[ "$(uname -s)" != "Linux" ]]; then
    printf '%s\n' 'This package helper requires Linux.' >&2
    exit 1
fi
elevate=()
if [[ "$(id -u)" != "0" ]]; then elevate=(sudo); fi
if command -v dnf >/dev/null 2>&1; then
    "${elevate[@]}" dnf install -y gcc gcc-c++ make git python3 sysstat procps-ng iproute tar gzip curl rsync
    "${elevate[@]}" dnf install -y perf || printf '%s\n' 'perf package unavailable; allocation diagnostics remain usable.' >&2
elif command -v apt-get >/dev/null 2>&1; then
    "${elevate[@]}" apt-get update
    "${elevate[@]}" apt-get install -y build-essential git python3 sysstat procps iproute2 tar gzip curl rsync ca-certificates
    "${elevate[@]}" apt-get install -y linux-tools-common || printf '%s\n' 'perf package unavailable; allocation diagnostics remain usable.' >&2
else
    printf '%s\n' 'Install the README prerequisites using your distribution package manager.' >&2
    exit 1
fi
if [[ ! -x "$HOME/.cargo/bin/rustup" ]]; then
    installer=$(mktemp)
    curl --fail --proto '=https' --tlsv1.2 https://sh.rustup.rs -o "$installer"
    sh "$installer" -y --profile minimal --default-toolchain 1.98.1
    rm "$installer"
fi
"$HOME/.cargo/bin/rustup" toolchain install 1.98.1 --profile minimal --component rustfmt --component clippy
printf '%s\n' 'Setup complete. Build on this host; no traffic has been started.'
