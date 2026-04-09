#!/usr/bin/env bash
set -euo pipefail

VERSION="${AF_VERSION:-1.2.18}"
TAG="${AF_TAG:-af-fsa_v${VERSION}-macos-x86_64}"
ZIP_NAME="${AF_ZIP_NAME:-af-${VERSION}-macos-x86_64.zip}"
ZIP_URL="${AF_ZIP_URL:-https://github.com/hoonkim1092-web/af-fsa/raw/${TAG}/dist/${ZIP_NAME}}"
INSTALL_ROOT="${AF_INSTALL_ROOT:-$HOME/.local/share/agent-factory}"
APP_DIR="${INSTALL_ROOT}/af-${VERSION}"

choose_bin_dir() {
  if [ -n "${AF_BIN_DIR:-}" ]; then
    mkdir -p "${AF_BIN_DIR}"
    printf '%s\n' "${AF_BIN_DIR}"
    return
  fi

  if mkdir -p /usr/local/bin 2>/dev/null && [ -w /usr/local/bin ]; then
    printf '%s\n' "/usr/local/bin"
    return
  fi

  mkdir -p "$HOME/.local/bin"
  printf '%s\n' "$HOME/.local/bin"
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "missing required command: $1" >&2
    exit 1
  fi
}

require_cmd curl
require_cmd unzip

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"

if [ "${OS_NAME}" != "Darwin" ]; then
  echo "install-af-macos.sh is only for macOS. detected: ${OS_NAME}" >&2
  exit 1
fi

if [ "${ARCH_NAME}" != "x86_64" ]; then
  echo "this installer currently supports macOS x86_64 only. detected: ${ARCH_NAME}" >&2
  exit 1
fi

BIN_DIR="$(choose_bin_dir)"
BIN_LINK="${BIN_DIR}/af"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "${TMP_DIR}"' EXIT

echo
echo "============================================================"
echo "  Agent Factory CLI v${VERSION} macOS Intel installer"
echo "============================================================"
echo
echo "Install root: ${INSTALL_ROOT}"
echo "Binary link:  ${BIN_LINK}"

mkdir -p "${INSTALL_ROOT}"

echo
echo "Downloading ${ZIP_NAME}..."
curl -fL "${ZIP_URL}" -o "${TMP_DIR}/${ZIP_NAME}"

echo "Extracting archive..."
unzip -q "${TMP_DIR}/${ZIP_NAME}" -d "${TMP_DIR}/out"

if [ ! -x "${TMP_DIR}/out/af/af" ]; then
  echo "expected binary not found in archive: ${TMP_DIR}/out/af/af" >&2
  exit 1
fi

rm -rf "${APP_DIR}"
mv "${TMP_DIR}/out/af" "${APP_DIR}"
xattr -dr com.apple.quarantine "${APP_DIR}" >/dev/null 2>&1 || true

ln -sfn "${APP_DIR}/af" "${BIN_LINK}"

echo "Verifying binary..."
"${APP_DIR}/af" --help >/dev/null

echo
echo "Installed successfully."
echo "Binary: ${APP_DIR}/af"
echo "Link:   ${BIN_LINK}"
echo
if printf '%s' ":${PATH}:" | grep -q ":${BIN_DIR}:"; then
  echo "Run:"
  echo "  af --help"
else
  echo "Add this to your shell profile:"
  echo "  export PATH=\"${BIN_DIR}:\$PATH\""
  echo
  echo "Then run:"
  echo "  af --help"
fi
