#!/bin/bash -p
set -euo pipefail

script_path="${BASH_SOURCE[0]}"
if [[ "$script_path" == */* ]]; then
  script_parent="${script_path%/*}"
else
  script_parent="."
fi
if ! script_dir="$(
  builtin unset CDPATH
  builtin cd -P -- "$script_parent"
  builtin pwd -P
)"; then
  builtin printf 'install-claude.sh: unable to resolve installer directory\n' >&2
  exit 1
fi
export ATLAS_INSTALL_ENTRYPOINT=claude-code
exec /bin/bash -p "$script_dir/install.sh" "$@"
