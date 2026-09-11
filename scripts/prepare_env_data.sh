#!/bin/bash
# ============================================================================
# AeroVLA - PREP ENV DATA for a new map (all steps, parameterized)
#
# Prepares everything the closed-loop eval needs for <MAP> in the <CATEGORY>
# valset. Fully idempotent: re-running re-verifies / regenerates each step.
#
# The eval client (src/vlnce_src/env_uav.py load_my_datasets) reads, for each
# entry in the split JSON,  dataset_path/<map>/<seq>/merged_data.json.
# That file is NOT stored: it is derived on demand from each episode's raw
# `log/` frames + cameras + object_description.json via the TravelUAV generator
#   TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py
#
# This script (in order):
#   1. Locates the episode source tree for <map> (envs/data_raws/<map>).
#   2. Counts episodes already carrying merged_data.json (generator skips these);
#      generates the missing ones with the TravelUAV generator.
#   3. Regenerates the <map> spawn-area rows in data/meta/map_spawnarea_info.json
#      from the episodes' mark.json (one 18-field row per distinct
#      (object_name, target.position)); the model client snaps each episode to
#      the nearest row for spawning live objects.
#   4. Verifies data/meta/object_description.json covers every asset used by the
#      episodes' mark.json (warns on missing names; closeloop_util strips "AA").
#   5. Creates the eval split json
#      data/uav_dataset/<category>_splits/<map>.json: one {"json","frame":1}
#      entry per VALID episode (merged_data.json + mark.json present) — corrupt/
#      unmerged episodes are excluded and reported.
#   6. Repoints dataset_raw/<map> -> envs/data_raws/<map> so the eval's
#      --dataset_path ./dataset_raw/ resolves to the episode source.
#   7. Verifies a sample episode path resolves (merged_data.json + mark.json).
#
# Usage (H100, or anywhere the raw episode tree lives):
#   bash scripts/prepare_env_data.sh [MAP] [CATEGORY] [PYTHON]
#
#   MAP       map/env name (default BrushifyCountryRoads).
#             Input episodes are read from envs/data_raws/<MAP>.
#   CATEGORY  valset category (default seen_valset). Controls the split file:
#             data/uav_dataset/<CATEGORY>_splits/<MAP>.json and the eval save
#             dir naming used by run_eval.sh.
#   PYTHON    python interpreter with json/glob (default .venv/bin/python).
#
# After this, sync git then run:  AEROVLA_MAP=<MAP> bash scripts/run_eval.sh
# ============================================================================
set -euo pipefail

MAP="${1:-BrushifyCountryRoads}"
CATEGORY="${2:-seen_valset}"
PYTHON="${3:-${AEROVLA_PYTHON:-.venv/bin/python}}"

cd "$(dirname "$0")/.."
ROOT="$(pwd)"

GEN="${ROOT}/TravelUAV/Model/LLaMA-UAV/tools/generate_merged_json.py"
RAW_ROOT="${ROOT}/envs/data_raws"
SRC_DIR="${RAW_ROOT}/${MAP}"
DST_DIR="${ROOT}/dataset_raw/${MAP}"
SPLIT_JSON="${ROOT}/data/uav_dataset/${CATEGORY}_splits/${MAP}.json"
SPAWN_JSON="${ROOT}/data/meta/map_spawnarea_info.json"
OBJDESC_JSON="${ROOT}/data/meta/object_description.json"

echo "=============================================="
echo "AeroVLA prep env data"
echo "  map           : ${MAP}"
echo "  category      : ${CATEGORY}"
echo "  episode source: ${SRC_DIR}"
echo "  eval view     : ${DST_DIR} -> symlink to source"
echo "  split json    : ${SPLIT_JSON}"
echo "  spawn json    : ${SPAWN_JSON}"
echo "  object desc   : ${OBJDESC_JSON}"
echo "=============================================="

[ -d "${SRC_DIR}" ] || { echo "FATAL: episode source dir missing: ${SRC_DIR}"; exit 1; }
[ -f "${GEN}" ] || { echo "FATAL: TravelUAV generator missing: ${GEN}"; exit 1; }
[ -x "${PYTHON}" ] || { echo "FATAL: python not found: ${PYTHON} (set AEROVLA_PYTHON)"; exit 1; }
[ -f "${SPAWN_JSON}" ] || { echo "FATAL: ${SPAWN_JSON} missing"; exit 1; }
[ -f "${OBJDESC_JSON}" ] || { echo "FATAL: ${OBJDESC_JSON} missing"; exit 1; }

# --- Step 2: merged_data.json -------------------------------------------------
EP_TOTAL=$(find "${SRC_DIR}" -maxdepth 1 -mindepth 1 -type d | wc -l)
EP_MISSING=0
for d in "${SRC_DIR}"/*/; do
  [ -d "$d" ] || continue
  [ -f "${d}merged_data.json" ] || EP_MISSING=$((EP_MISSING+1))
done
echo
echo "==> [1/5] merged_data.json  (episodes total: ${EP_TOTAL}, missing: ${EP_MISSING})"
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

# --- Step 3: spawn-area rows --------------------------------------------------
echo
echo "==> [2/5] spawn-area rows for ${MAP} from episode mark.json ..."
"${PYTHON}" - "${SRC_DIR}" "${SPAWN_JSON}" "${MAP}" <<'PY'
import json, os, sys
src_dir, spawn_json, map_name = sys.argv[1], sys.argv[2], sys.argv[3]
rows, seen = [], set()
scanned = 0
used = 0
for m in sorted(os.listdir(src_dir)):
    ep = os.path.join(src_dir, m)
    if not os.path.isdir(ep) or not os.path.isfile(os.path.join(ep, 'mark.json')):
        continue
    scanned += 1
    d = json.load(open(os.path.join(ep, 'mark.json')))
    key = (d['object_name'], tuple(d['target']['position']))
    if key in seen:
        used += 1
        continue
    seen.add(key)
    used += 1
    x, y, z = d['target']['position']
    rows.append([x-1, y-1, z-0.5, x+1, y+1, z+0.5, 0, 0, 0,
                 x, y, z, 1.0, 0.0, 0.0, 0.0, d['object_name'], 1.0])
sp = json.load(open(spawn_json))
sp[map_name] = rows
json.dump(sp, open(spawn_json, 'w'), indent=2)
print(f"episodes scanned: {scanned}; spawn-area rows: {len(rows)}; written to {spawn_json}")
PY

# --- Step 4: object_description coverage --------------------------------------
echo
echo "==> [3/5] object_description.json coverage for ${MAP} assets ..."
"${PYTHON}" - "${SRC_DIR}" "${OBJDESC_JSON}" <<'PY'
import json, os, sys
src_dir, objdesc_json = sys.argv[1], sys.argv[2]
desc = json.load(open(objdesc_json))
have = {x['object_name'] for x in desc}
need = set()
ep_missing_obj = []
for m in sorted(os.listdir(src_dir)):
    ep = os.path.join(src_dir, m)
    if not os.path.isdir(ep) or not os.path.isfile(os.path.join(ep, 'mark.json')):
        continue
    try:
        obj = json.load(open(os.path.join(ep, 'mark.json')))['object_name']
    except Exception:
        continue
    key = obj.replace('AA', '')
    if key not in have:
        ep_missing_obj.append((m, obj))
    need.add(key)
missing = sorted(ep_missing_obj)
print(f"assets used (AA-stripped): {len(need)}; covered: {len(need - {n for n,_ in missing} & need)}; missing: {len(missing)}")
if missing:
    print("WARNING: no object_desc for these episodes (eval will get 'None' desc):")
    for ep, obj in missing[:20]:
        print(f"  {ep}: {obj}")
PY

# --- Step 5: split json --------------------------------------------------------
echo
echo "==> [4/5] creating split json ${SPLIT_JSON} ..."
mkdir -p "$(dirname "${SPLIT_JSON}")"
"${PYTHON}" - "${SRC_DIR}" "${SPLIT_JSON}" "${MAP}" <<'PY'
import json, os, sys
src_dir, split_json, map_name = sys.argv[1], sys.argv[2], sys.argv[3]
uuids = []
for m in sorted(os.listdir(src_dir)):
    ep = os.path.join(src_dir, m)
    if (os.path.isdir(ep)
            and os.path.isfile(os.path.join(ep, 'merged_data.json'))
            and os.path.isfile(os.path.join(ep, 'mark.json'))):
        uuids.append(m)
split = [{"json": f"{map_name}/{u}/merged_data.json", "frame": 1} for u in uuids]
json.dump(split, open(split_json, 'w'), indent=2)
print(f"episodes in split: {len(split)}; written to {split_json}")
PY

# --- Step 6: dataset_raw symlink ------------------------------------------------
echo
echo "==> [5/5] pointing dataset_raw/${MAP} at the episode source ..."
mkdir -p "${ROOT}/dataset_raw"
rm -rf "${DST_DIR}"                    # remove stale dir/symlink
ln -sfn "${SRC_DIR}" "${DST_DIR}"
[ -L "${DST_DIR}" ] && echo "    symlink OK: ${DST_DIR} -> $(readlink ${DST_DIR})"

echo
echo "Done. Verify sample eval path exists:"
SAMPLE=$(find -L "${DST_DIR}" -mindepth 1 -maxdepth 1 -type d | head -1)
if [ -n "${SAMPLE}" ] && [ -f "${SAMPLE}/merged_data.json" ] && [ -f "${SAMPLE}/mark.json" ]; then
  echo "  OK: ${SAMPLE}/merged_data.json"
  echo "  OK: ${SAMPLE}/mark.json"
else
  echo "  WARNING: no valid episode found under ${DST_DIR}."
fi
echo
echo "Next:  git add data/meta/map_spawnarea_info.json \\
             data/uav_dataset/${CATEGORY}_splits/${MAP}.json && git push
  then:  AEROVLA_MAP=${MAP} bash scripts/run_eval.sh"
