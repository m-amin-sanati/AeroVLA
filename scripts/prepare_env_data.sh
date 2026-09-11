#!/bin/bash
# ============================================================================
# AeroVLA - PREP ENV DATA: ensure every episode has its merged_data.json
#
# The eval client (src/vlnce_src/env_uav.py load_my_datasets) reads, for each
# entry in the split JSON,  dataset_path/<map>/<seq>/merged_data.json.
# That file is NOT stored: it is derived on demand from each episode's raw
# `log/` frames + cameras + object_description.json via the TravelUAV generator
#   TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py
#
# This script:
#   1. Locates the episode source tree for <map> (envs/data_raws/<map>).
#   2. Counts episodes already carrying merged_data.json (generator skips these).
#   3. If any are missing, runs the TravelUAV generator over envs/data_raws
#      with --map_list <map> to synthesize them (as the current user; the
#      env tree is typically root-owned, so run this as root / owner).
#   4. Regenerates the <map> spawn-area rows in data/meta/map_spawnarea_info.json
#      from the episodes' mark.json (one 18-field row per distinct
#      (object_name, target.position)); the model client snaps each episode to
#      the nearest row for spawning live objects.
#   5. Repoints dataset_raw/<map> -> envs/data_raws/<map> so the eval's
#      --dataset_path ./dataset_raw/ resolves to the episodes.
#
# Usage (H100):
#   bash scripts/prepare_env_data.sh [MAP] [--venv .venv/bin/python]
#
#   MAP   map/env name (default BrushifyCountryRoads).
#
# After this, run:  bash scripts/run_eval.sh
# ============================================================================
set -euo pipefail

MAP="${1:-BrushifyCountryRoads}"
PYTHON="${AEROVLA_PYTHON:-.venv/bin/python}"

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

GEN="${ROOT}/TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py"
RAW_ROOT="${ROOT}/envs/data_raws"
SRC_DIR="${RAW_ROOT}/${MAP}"
DST_DIR="${ROOT}/dataset_raw/${MAP}"

echo "=============================================="
echo "AeroVLA prep env data"
echo "  map           : ${MAP}"
echo "  episode source: ${SRC_DIR}"
echo "  eval view     : ${DST_DIR} -> symlink to source"
echo "=============================================="

[ -d "${SRC_DIR}" ] || { echo "FATAL: episode source dir missing: ${SRC_DIR}"; exit 1; }
[ -f "${GEN}" ] || { echo "FATAL: TravelUAV generator missing: ${GEN}"; exit 1; }
[ -x "${PYTHON}" ] || { echo "FATAL: python not found: ${PYTHON} (set AEROVLA_PYTHON)"; exit 1; }

EP_TOTAL=$(find "${SRC_DIR}" -maxdepth 1 -mindepth 1 -type d | wc -l)
EP_MISSING=0
for d in "${SRC_DIR}"/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}merged_data.json" ] || EP_MISSING=$((EP_MISSING+1))
done
echo "episodes total: ${EP_TOTAL}, missing merged_data.json: ${EP_MISSING}"

if [ "${EP_MISSING}" -gt 0 ]; then
  echo "==> Generating merged_data.json for ${EP_MISSING} episode(s) via TravelUAV ..."
  "${PYTHON}" "${GEN}" --root_dir "${RAW_ROOT}" --map_list "${MAP}" 2>&1 | tail -5
else
  echo "==> All ${EP_TOTAL} episodes already have merged_data.json - nothing to generate."
fi

EP_STILL_MISSING=0
for d in "${SRC_DIR}"/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}merged_data.json" ] || EP_STILL_MISSING=$((EP_STILL_MISSING+1))
done
echo "still missing after generation: ${EP_STILL_MISSING}"
if [ "${EP_STILL_MISSING}" -gt 0 ]; then
  echo "WARNING: ${EP_STILL_MISSING} episode(s) could not be merged (check log/ + cameras)."
fi

echo
echo "==> Regenerating spawn-area rows for ${MAP} from episode mark.json ..."
SPAWN_JSON="${ROOT}/data/meta/map_spawnarea_info.json"
[ -f "${SPAWN_JSON}" ] || { echo "FATAL: ${SPAWN_JSON} missing"; exit 1; }
"${PYTHON}" - "${SRC_DIR}" "${SPAWN_JSON}" "${MAP}" <<'PY'
import json, os, sys
src_dir, spawn_json, map_name = sys.argv[1], sys.argv[2], sys.argv[3]
rows, seen = [], set()
count = 0
for m in sorted(os.listdir(src_dir)):
    ep = os.path.join(src_dir, m)
    if not os.path.isdir(ep) or not os.path.isfile(os.path.join(ep, 'mark.json')):
        continue
    count += 1
    d = json.load(open(os.path.join(ep, 'mark.json')))
    key = (d['object_name'], tuple(d['target']['position']))
    if key in seen:
        continue
    seen.add(key)
    x, y, z = d['target']['position']
    rows.append([x-1, y-1, z-0.5, x+1, y+1, z+0.5, 0, 0, 0,
                 x, y, z, 1.0, 0.0, 0.0, 0.0, d['object_name'], 1.0])
sp = json.load(open(spawn_json))
sp[map_name] = rows
json.dump(sp, open(spawn_json, 'w'), indent=2)
print(f"episodes scanned: {count}; spawn-area rows: {len(rows)}; written to {spawn_json}")
PY

echo
echo "==> Pointing dataset_raw/${MAP} at the episode source ..."
mkdir -p "${ROOT}/dataset_raw"
rm -rf "${DST_DIR}"                    # remove stale dir/symlink
ln -sfn "${SRC_DIR}" "${DST_DIR}"
[ -L "${DST_DIR}" ] && echo "    symlink OK: ${DST_DIR} -> $(readlink ${DST_DIR})"

echo
echo "Done. Verify sample eval path exists:"
SAMPLE=$(find "${DST_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1)
if [ -n "${SAMPLE}" ] && [ -f "${SAMPLE}/merged_data.json" ]; then
  echo "  OK: ${SAMPLE}/merged_data.json"
else
  echo "  WARNING: no episode with merged_data.json found under ${DST_DIR}."
fi
