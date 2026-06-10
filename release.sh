#!/usr/bin/env bash
#
# release.sh — build the plugin zip and publish it to a GitHub release.
#
# Builds with build.sh, then attaches the zip to a GitHub release using the
# GitHub CLI (gh). Prompts for the release tag, defaulting to the version in
# __init__.py. Creates the release if it does not exist, or uploads to it if
# it does. No GitHub Actions and no billing required.
#
# Requirements:
#   - gh (GitHub CLI), authenticated:  gh auth login
#   - build.sh in the same directory
#
set -euo pipefail

cd "$(dirname "$0")"

# --- Read the plugin version from __init__.py, e.g. (1, 3, 0) -> 1.3.0 ---
plugin_version() {
    python3 - <<'PY'
import re
src = open("__init__.py", encoding="utf-8").read()
m = re.search(r"version\s*=\s*\(([^)]+)\)", src)
print(".".join(p.strip() for p in m.group(1).split(",")))
PY
}

# --- Checks ---
if ! command -v gh >/dev/null 2>&1; then
    echo "Error: GitHub CLI 'gh' is not installed." >&2
    echo "  openSUSE: sudo zypper install gh" >&2
    echo "  then:     gh auth login" >&2
    exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
    echo "Error: gh is not authenticated. Run: gh auth login" >&2
    exit 1
fi

VERSION="$(plugin_version)"
DEFAULT_TAG="v${VERSION}"

# --- Prompt for the release tag ---
read -rp "Release tag [${DEFAULT_TAG}]: " TAG
TAG="${TAG:-$DEFAULT_TAG}"

# Warn if the tag version doesn't match the plugin version.
TAG_VERSION="${TAG#v}"
if [ "${TAG_VERSION}" != "${VERSION}" ]; then
    echo "Warning: tag '${TAG}' does not match plugin version '${VERSION}' in __init__.py."
    read -rp "Continue anyway? [y/N]: " yn
    case "${yn}" in
        [Yy]*) ;;
        *) echo "Aborted."; exit 1 ;;
    esac
fi

# --- Optional release title and notes ---
read -rp "Release title [Send to Calibre-web ${TAG}]: " TITLE
TITLE="${TITLE:-Send to Calibre-web ${TAG}}"

read -rp "Release notes (one line, blank to use CHANGELOG section): " NOTES

# Try to pull notes from CHANGELOG.md if none were given.
NOTES_FILE=""
if [ -z "${NOTES}" ] && [ -f CHANGELOG.md ]; then
    NOTES_FILE="$(mktemp)"
    awk -v ver="${TAG_VERSION}" '
        $0 ~ "^## \\[" ver "\\]" { capture=1; next }
        capture && /^## \[/ { exit }
        capture { print }
    ' CHANGELOG.md > "${NOTES_FILE}"
    if [ ! -s "${NOTES_FILE}" ]; then
        rm -f "${NOTES_FILE}"; NOTES_FILE=""
    fi
fi

# --- Build the zip (versioned filename) ---
echo
echo "Building plugin zip…"
./build.sh
ASSET="send-to-calibre-web-${TAG}.zip"
cp send-to-calibre-web.zip "${ASSET}"
echo "Built ${ASSET}"

# --- Make sure the git tag exists locally and remotely ---
if ! git rev-parse "${TAG}" >/dev/null 2>&1; then
    echo "Creating git tag ${TAG}…"
    git tag "${TAG}"
fi
if ! git ls-remote --tags origin "refs/tags/${TAG}" | grep -q "${TAG}"; then
    echo "Pushing tag ${TAG} to origin…"
    git push origin "${TAG}"
fi

# --- Create or update the release ---
echo
if gh release view "${TAG}" >/dev/null 2>&1; then
    echo "Release ${TAG} exists — uploading asset…"
    gh release upload "${TAG}" "${ASSET}" --clobber
else
    echo "Creating release ${TAG}…"
    if [ -n "${NOTES_FILE}" ]; then
        gh release create "${TAG}" "${ASSET}" --title "${TITLE}" --notes-file "${NOTES_FILE}"
    else
        gh release create "${TAG}" "${ASSET}" --title "${TITLE}" --notes "${NOTES:-Release ${TAG}}"
    fi
fi

[ -n "${NOTES_FILE}" ] && rm -f "${NOTES_FILE}"

echo
echo "Done. Asset '${ASSET}' is attached to release ${TAG}."
REPO_URL="$(gh repo view --json url -q .url 2>/dev/null || true)"
[ -n "${REPO_URL}" ] && echo "View it at: ${REPO_URL}/releases/tag/${TAG}"
