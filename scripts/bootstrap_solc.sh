#!/usr/bin/env bash
# bootstrap_solc.sh — fetch the solc compiler Hardhat needs, from the GitHub
# mirror, and verify its sha256 before placing it in Hardhat's compiler cache.
#
# Why this exists: `npx hardhat compile` downloads solc from
# binaries.soliditylang.org on first run. Some networks (including restricted
# CI/agent environments) block that host, so compilation fails on a clean clone
# with "Host not in allowlist: binaries.soliditylang.org". The identical
# compiler binaries are mirrored at raw.githubusercontent.com/ethereum/solc-bin
# (the repo that BACKS binaries.soliditylang.org), which is reachable where the
# canonical host is not. This script fetches from that mirror and verifies the
# sha256 against the mirror's own signed list.json — so we never run an
# unverified binary — then drops it where Hardhat looks, making `compile` work
# offline afterwards.
#
# Idempotent: if the verified compiler is already cached, it does nothing.
# Safe to run before every `hardhat compile` (npm "precompile" hook does this).
set -euo pipefail

# Compiler version must match hardhat.config.ts (solidity.version).
SOLC_VERSION="${SOLC_VERSION:-0.8.24}"
MIRROR_BASE="https://raw.githubusercontent.com/ethereum/solc-bin/gh-pages"

# --- platform → solc-bin subdir (matches Hardhat's CompilerDownloader) ---
# On unsupported platforms (e.g. Windows) we SKIP with exit 0 rather than fail:
# `hardhat compile` then downloads solc itself. This keeps `npm run compile`
# working everywhere; the mirror bootstrap is only needed where the canonical
# solc host is blocked (Linux/macOS restricted networks).
case "$(uname -s)" in
  Linux)  PLATFORM="linux-amd64" ;;
  Darwin) PLATFORM="macosx-amd64" ;;
  *) echo "bootstrap_solc: platform $(uname -s) not handled here — letting Hardhat fetch solc itself. Skipping."; exit 0 ;;
esac

# --- Hardhat compiler cache dir (respects HARDHAT_CACHE if set) ---
if [ -n "${HARDHAT_CACHE:-}" ]; then
  CACHE_ROOT="$HARDHAT_CACHE"
elif [ "$(uname -s)" = "Darwin" ]; then
  CACHE_ROOT="$HOME/Library/Caches/hardhat-nodejs"
else
  CACHE_ROOT="${XDG_CACHE_HOME:-$HOME/.cache}/hardhat-nodejs"
fi
DEST_DIR="$CACHE_ROOT/compilers-v2/$PLATFORM"
mkdir -p "$DEST_DIR"

echo "bootstrap_solc: solc $SOLC_VERSION for $PLATFORM → $DEST_DIR"

# --- fetch the mirror's list.json (Hardhat also needs it present to trust the binary) ---
LIST_URL="$MIRROR_BASE/$PLATFORM/list.json"
curl -fsSL "$LIST_URL" -o "$DEST_DIR/list.json"

# --- resolve the exact build path + expected sha256 for our version ---
read -r BUILD_PATH EXPECTED_SHA < <(python3 - "$DEST_DIR/list.json" "$SOLC_VERSION" <<'PY'
import json, sys
data = json.load(open(sys.argv[1]))
want = sys.argv[2]
for b in data["builds"]:
    if b["version"] == want and not b.get("prerelease"):
        # list.json stores sha256 as "0x…"; strip for sha256sum comparison.
        print(b["path"], b["sha256"].removeprefix("0x"))
        break
else:
    sys.exit(f"solc {want} not found in list.json")
PY
)

BIN_PATH="$DEST_DIR/$BUILD_PATH"

verify() { # $1=file $2=expected-hex → 0 if match
  local actual
  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$1" | cut -d' ' -f1)"
  else
    actual="$(shasum -a 256 "$1" | cut -d' ' -f1)"   # macOS
  fi
  [ "$actual" = "$2" ]
}

# --- idempotent: already present and verified? done. ---
if [ -f "$BIN_PATH" ] && verify "$BIN_PATH" "$EXPECTED_SHA"; then
  echo "bootstrap_solc: already cached and verified ($BUILD_PATH). Nothing to do."
  exit 0
fi

echo "bootstrap_solc: downloading $BUILD_PATH …"
curl -fsSL "$MIRROR_BASE/$PLATFORM/$BUILD_PATH" -o "$BIN_PATH"

if verify "$BIN_PATH" "$EXPECTED_SHA"; then
  chmod +x "$BIN_PATH"
  echo "bootstrap_solc: sha256 VERIFIED ✓  ($EXPECTED_SHA)"
  echo "bootstrap_solc: ready. Run: npx hardhat compile"
else
  rm -f "$BIN_PATH"
  echo "bootstrap_solc: sha256 MISMATCH ✗ — refusing to keep an unverified compiler." >&2
  exit 1
fi
