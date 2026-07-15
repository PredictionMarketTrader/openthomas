#!/usr/bin/env bash
# Build the GraphCast inference env for run_graphcast.sh, reproducibly.
#
# ai-models (0.7.4, the last release) is unmaintained against the current earthkit
# / jax, so the versions below are pinned to a mutually-consistent, tested set,
# and two source patches are applied that a fresh install needs:
#   1. ecmwf-opendata: default to the AWS mirror (the ECMWF portal 429-throttles).
#   2. ai-models-graphcast: request every cycle from the 'oper' stream (open-data
#      serves 06/18z as oper now; the old scda mapping 404s, and GraphCast needs
#      the t-6h step which always lands on 06/18z).
#
# Idempotent: safe to re-run. Needs internet (or a reverse-SOCKS proxy) + git.
set -euo pipefail

GC_HOME="${OPENTHOMAS_GC_HOME:-$HOME/.openthomas/graphcast}"
VENV="$GC_HOME/venv-gc"
PY="$VENV/bin/python"
PIP="$VENV/bin/pip"
mkdir -p "$GC_HOME"

echo "[1/4] venv + pinned install"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$PIP" install -q -U pip
# pysocks lets requests use the reverse-SOCKS proxy run_remote.sh sets on boxes
# with no clean route to ECMWF/AWS.
"$PIP" install -q ai-models ai-models-graphcast pysocks
# graphcast is git-only (not on PyPI); jax pinned to 0.4.28 to match dm-haiku's
# jax.linear_util use (removed in newer jax) and to run on driver ≥525.
"$PIP" install -q "jax[cuda12]==0.4.28" "git+https://github.com/google-deepmind/graphcast.git"
# earthkit-data 1.0 moved FieldArray; 0.20 is the newest that ai-models 0.7.4
# imports. dm-haiku 0.0.13 uses jax.extend.linear_util (0.0.10 uses the removed
# jax.linear_util).
"$PIP" install -q "earthkit-data==0.20.0" "dm-haiku==0.0.13"

echo "[2/4] patch ecmwf-opendata -> AWS mirror"
SP="$("$PY" -c 'import ecmwf.opendata, os; print(os.path.dirname(ecmwf.opendata.__file__))')"
sed -i 's/        source="ecmwf",/        source="aws",/' "$SP/client.py"

echo "[3/4] patch ai-models-graphcast stream -> oper"
GM="$("$PY" -c 'import ai_models_graphcast, os; print(os.path.dirname(ai_models_graphcast.__file__))')/model.py"
"$PY" - "$GM" <<'PYEOF'
import sys
p = sys.argv[1]; s = open(p).read()
old = '''        r["stream"] = {
            0: "oper",
            6: "scda",
            12: "oper",
            18: "scda",
        }[time]'''
new = '        r["stream"] = "oper"  # open-data serves 06/18z as oper now; scda 404s'
if 'r["stream"] = "oper"' in s:
    print("  already patched")
elif old in s:
    open(p, "w").write(s.replace(old, new)); print("  stream -> oper")
else:
    sys.exit("  STREAM PATTERN NOT FOUND — check ai-models-graphcast version")
PYEOF

echo "[3b] add GC_MODEL=full switch (37-level full model from ERA5)"
"$PY" - "$GM" <<'PYEOF'
import sys
p=sys.argv[1]; s=open(p).read()
old_df='''        (
            "params/GraphCast_operational - ERA5-HRES 1979-2021 - resolution 0.25 -"
            " pressure levels 13 - mesh 2to6 - precipitation output only.npz"
        ),'''
new_df='''        (
            "params/GraphCast - ERA5 1979-2017 - resolution 0.25 - pressure levels 37"
            " - mesh 2to6 - precipitation input and output.npz"
            if os.environ.get("GC_MODEL") == "full" else
            "params/GraphCast_operational - ERA5-HRES 1979-2021 - resolution 0.25 -"
            " pressure levels 13 - mesh 2to6 - precipitation output only.npz"
        ),'''
old_lv='''        ["t", "z", "u", "v", "w", "q"],
        [50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 850, 925, 1000],'''
new_lv='''        ["t", "z", "u", "v", "w", "q"],
        ([1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 125, 150, 175, 200, 225, 250, 300,
          350, 400, 450, 500, 550, 600, 650, 700, 750, 775, 800, 825, 850, 875, 900,
          925, 950, 975, 1000] if os.environ.get("GC_MODEL") == "full"
         else [50, 100, 150, 200, 250, 300, 400, 500, 600, 700, 850, 925, 1000]),'''
if "GC_MODEL" in s: print("  already GC_MODEL-patched")
elif old_df in s and old_lv in s:
    open(p,"w").write(s.replace(old_df,new_df).replace(old_lv,new_lv)); print("  GC_MODEL=full switch added")
else: print("  GC_MODEL pattern not found (version drift?)", file=sys.stderr)
PYEOF

echo "[4/4] stage weights ($GC_HOME/{params,stats})"
# Reuse a shared copy if present (e.g. /mnt/data/models/graphcast); else fetch the
# operational param + stats from the public GCS bucket.
if [ -d /mnt/data/models/graphcast/params ]; then
  ln -sfn /mnt/data/models/graphcast/params "$GC_HOME/params"
  ln -sfn /mnt/data/models/graphcast/stats  "$GC_HOME/stats"
else
  "$PY" - "$GC_HOME" <<'PYEOF'
import os, sys, urllib.parse, urllib.request
root = sys.argv[1]; bucket = "dm_graphcast"
files = [
    "params/GraphCast_operational - ERA5-HRES 1979-2021 - resolution 0.25 -"
    " pressure levels 13 - mesh 2to6 - precipitation output only.npz",
    "stats/diffs_stddev_by_level.nc", "stats/mean_by_level.nc", "stats/stddev_by_level.nc",
]
for name in files:
    dest = os.path.join(root, name); os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest):
        continue
    url = f"https://storage.googleapis.com/{bucket}/" + urllib.parse.quote(name, safe="/")
    print("  GET", name); urllib.request.urlretrieve(url, dest)
PYEOF
fi

echo "verify:"
JAX_PLATFORMS=cpu "$PY" -c "import ai_models_graphcast.model, graphcast, jax; print('  graphcast env OK, jax', jax.__version__)"
echo "done — run with scripts/nwp/run_graphcast.sh"
