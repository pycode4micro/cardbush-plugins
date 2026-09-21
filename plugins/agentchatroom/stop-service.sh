#!/bin/sh
set -eu
plugin_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
exec node "$plugin_dir/runtime/cli.mjs" stop
