#!/usr/bin/env bash
#
# build.sh — produce the installable Calibre plugin zip.
#
# Usage:
#   ./build.sh              # build send-to-calibre-web.zip
#   ./build.sh --install    # build and install into Calibre (needs calibre-customize)
#   ./build.sh --clean      # remove build artifacts
#
set -euo pipefail

PLUGIN_NAME="send-to-calibre-web"
ZIP="${PLUGIN_NAME}.zip"

# Files that go into the plugin zip (must be at the zip root).
FILES=(
    __init__.py
    action.py
    config.py
    profiles.py
    backends
    plugin-import-name-send_to_calibre_web.txt
    images
)

clean() {
    rm -f "${ZIP}"
    find . -type d -name '__pycache__' -prune -exec rm -rf {} +
    find . -type f -name '*.mo' -delete
    echo "Cleaned."
}

compile_translations() {
    # Compile every translations/*.po into translations/*.mo.
    # Prefer system msgfmt; fall back to Calibre's bundled msgfmt.py.
    local msgfmt_cmd=""
    if command -v msgfmt >/dev/null 2>&1; then
        msgfmt_cmd="msgfmt"
    else
        local cal_msgfmt
        for cal_msgfmt in \
            /usr/lib/calibre/calibre/translations/msgfmt.py \
            "$(command -v calibre-debug >/dev/null 2>&1 && echo CAL)"; do
            if [ -f "${cal_msgfmt}" ]; then
                msgfmt_cmd="python3 ${cal_msgfmt}"
                break
            fi
        done
    fi

    if [ -z "${msgfmt_cmd}" ]; then
        echo "No msgfmt found (system or Calibre); skipping translation compilation."
        return
    fi

    shopt -s nullglob
    for po in translations/*.po; do
        if [ "${msgfmt_cmd}" = "msgfmt" ]; then
            msgfmt "${po}" -o "${po%.po}.mo"
        else
            # Calibre's msgfmt.py writes <name>.mo next to the .po
            ${msgfmt_cmd} "${po}"
        fi
        echo "Compiled ${po}"
    done
    shopt -u nullglob
}

build() {
    clean
    compile_translations

    # Include compiled translations if any were produced.
    local extra=()
    shopt -s nullglob
    local mos=(translations/*.mo)
    shopt -u nullglob
    if [ ${#mos[@]} -gt 0 ]; then
        extra+=(translations)
    fi

    zip -r "${ZIP}" "${FILES[@]}" "${extra[@]}" \
        -x '*__pycache__*' -x '*.pyc'
    echo
    echo "Built ${ZIP}"
}

install_plugin() {
    if ! command -v calibre-customize >/dev/null 2>&1; then
        echo "calibre-customize not found. Is Calibre installed?" >&2
        exit 1
    fi
    calibre-customize -a "${ZIP}"
    echo "Installed. Restart Calibre to load the new version."
}

case "${1:-}" in
    --clean)   clean ;;
    --install) build; install_plugin ;;
    "")        build ;;
    *)         echo "Unknown option: $1" >&2; exit 2 ;;
esac
