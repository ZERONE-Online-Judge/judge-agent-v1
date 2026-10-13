#!/usr/bin/env bash
# Build runtime images before registering Editor execution support.
set -euo pipefail
root=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$root"
docker build -t zoj-editor-runtime:agent-v1 editor-runtime
docker build -t zoj-editor-renderer:agent-v1 -f editor-runtime/Renderer.Dockerfile editor-runtime
docker image inspect zoj-editor-runtime:agent-v1 zoj-editor-renderer:agent-v1 >/dev/null
printf '%s\n' 'Editor execution images are ready. Recreate the idle judge-agent with its existing Compose environment and CPU/memory limits.'
