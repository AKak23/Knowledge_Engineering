#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${DEEPSEEK_API_KEY:-}" ]]; then
  echo "DEEPSEEK_API_KEY is required" >&2
  exit 1
fi

INPUT="${1:-samples/apple_smoke.txt}"
OUTPUT_DIR="${2:-outputs/deepseek_smoke}"

.venv/bin/ontology-construct build \
  --input "$INPUT" \
  --output-dir "$OUTPUT_DIR" \
  --chunk-size 120 \
  --chunk-overlap 0

.venv/bin/ontology-construct status --run-dir "$OUTPUT_DIR"

echo "Wrote $OUTPUT_DIR/ontology.json"
