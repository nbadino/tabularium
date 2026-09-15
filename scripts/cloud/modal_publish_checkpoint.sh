#!/usr/bin/env bash
set -euo pipefail

modal_bin="${MODAL_BIN:?MODAL_BIN mancante}"
volume_name="${1:?volume mancante}"
source_path="${2:?sorgente mancante}"
remote_path="${3:?destinazione mancante}"
deploy_script="${4:?script di deploy mancante}"

"$modal_bin" volume put --force "$volume_name" "$source_path" "$remote_path"
exec "$modal_bin" deploy "$deploy_script"
