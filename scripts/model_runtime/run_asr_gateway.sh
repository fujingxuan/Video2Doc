#!/usr/bin/env bash
set -euo pipefail

provider="$1"
model_root="$2"
python_executable="$3"
service_path="$4"
port="$5"
device="${6:-cuda}"

if [[ "$provider" == "whisper" ]]; then
  nvidia_library_path="$($python_executable -c 'import os; import nvidia.cublas; import nvidia.cudnn; print(os.path.join(next(iter(nvidia.cublas.__path__)), "lib") + ":" + os.path.join(next(iter(nvidia.cudnn.__path__)), "lib"))')"
  export LD_LIBRARY_PATH="$nvidia_library_path:${LD_LIBRARY_PATH:-}"
fi

exec env \
  VIDEO2DOC_ASR_PROVIDER="$provider" \
  VIDEO2DOC_MODEL_ROOT="$model_root" \
  VIDEO2DOC_ASR_DEVICE="$device" \
  "$python_executable" "$service_path" --host 127.0.0.1 --port "$port"
