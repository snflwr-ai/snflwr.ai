#!/usr/bin/env bash
# Build the Open WebUI frontend with snflwr.ai patches applied.
# Output goes to frontend/open-webui/build/ which is git-ignored.
# Run this after cloning or when upgrading the Open WebUI version.
#
# Requirements: Node.js 18-22, npm

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FRONTEND_DIR="$REPO_ROOT/frontend/open-webui"
BUILD_DIR="$FRONTEND_DIR/build"
OWUI_VERSION="v0.8.3"   # keep in sync with docker-compose.yaml WEBUI_DOCKER_TAG

cd "$FRONTEND_DIR"

echo "==> Installing dependencies (this may take a few minutes)..."
npm install --legacy-peer-deps --engine-strict=false

echo "==> Building Open WebUI frontend (${OWUI_VERSION} + snflwr.ai patches)..."
npm run build

echo ""
echo "Build complete: $BUILD_DIR"
echo "Start snflwr.ai normally — the build is mounted into the container automatically."
