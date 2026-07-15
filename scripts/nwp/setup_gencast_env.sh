#!/usr/bin/env bash
# Build the GenCast inference env for run_gencast.sh, reproducibly. The same venv
# also carries the GraphCast plugin, so one environment runs both models.
#
# Pinned to a mutually-consistent set (see setup_graphcast_env.sh for the why):
# ai-models 0.7.4, jax 0.4.28, earthkit-data 0.20, dm-haiku 0.0.13, graphcast git.
# Two gotchas this script handles:
#   1. ai-models-graphcast must be 0.1.0 (uses earthkit); 0.0.x pulls the dead
#      climetlab lib. It pins dm-haiku==0.0.10, which conflicts with our 0.0.13 —
#      so install it --no-deps (its deps are already satisfied).
#   2. Both plugins' patch_retrieve_request map 06/18z -> the scda stream, which
#      404s now; patch to "oper". (Only matters for the open-data path; GenCast
#      itself uses --input cds because it needs sst, which open-data omits.)
#
# GenCast needs ~/.cdsapirc with the ERA5 single-levels + pressure-levels licences
# accepted. Idempotent; needs internet + git.
set -euo pipefail

GCX_HOME="${OPENTHOMAS_GENCAST_HOME:-$HOME/.openthomas/gencast}"
VENV="$GCX_HOME/venv-gc"; PY="$VENV/bin/python"; PIP="$VENV/bin/pip"
mkdir -p "$GCX_HOME"

echo "[1/4] venv + pinned install (both plugins)"
[ -d "$VENV" ] || python3 -m venv "$VENV"
"$PIP" install -q -U pip
"$PIP" install -q ai-models ai-models-gencast pysocks
"$PIP" install -q "jax[cuda12]==0.4.28" "git+https://github.com/google-deepmind/graphcast.git"
"$PIP" install -q "earthkit-data==0.20.0" "dm-haiku==0.0.13"
# graphcast plugin: 0.1.0 (earthkit-based), --no-deps to keep dm-haiku 0.0.13
"$PIP" install -q --no-deps "ai-models-graphcast==0.1.0"

echo "[2/4] patch ecmwf-opendata -> AWS mirror (open-data path)"
SP="$("$PY" -c 'import ai_models_gencast, os; print(os.path.dirname(os.path.dirname(ai_models_gencast.__file__)))')"
sed -i 's/        source="ecmwf",/        source="aws",/' "$SP/ecmwf/opendata/client.py"

echo "[3/4] patch both plugins' stream -> oper"
for M in "$SP/ai_models_gencast/model.py" "$SP/ai_models_graphcast/model.py"; do
"$PY" - "$M" <<'PYEOF'
import sys
p=sys.argv[1]; s=open(p).read()
old='''        r["stream"] = {
            0: "oper",
            6: "scda",
            12: "oper",
            18: "scda",
        }[time]'''
new='        r["stream"] = "oper"  # open-data serves 06/18z as oper now; scda 404s'
if 'r["stream"] = "oper"' in s: print(" ", p.split("site-packages/")[1], "already oper")
elif old in s: open(p,"w").write(s.replace(old,new)); print(" ", p.split("site-packages/")[1], "-> oper")
else: sys.exit("  STREAM PATTERN NOT FOUND in "+p)
PYEOF
done

echo "[3b] patch graphcast rollout for jax 0.4.28 (jax.P / jax.NamedSharding aliases postdate it)"
"$PY" - "$SP/graphcast/rollout.py" <<'PYEOF'
import sys
p=sys.argv[1]; s=open(p).read()
old='  sharding = jax.NamedSharding(mesh, jax.P(axis_name))'
new='  sharding = jax.sharding.NamedSharding(mesh, jax.sharding.PartitionSpec(axis_name))  # jax.P/jax.NamedSharding aliases postdate 0.4.28'
if 'jax.sharding.PartitionSpec(axis_name)' in s: print("  rollout already patched")
elif old in s: open(p,"w").write(s.replace(old,new)); print("  rollout patched")
else: print("  ROLLOUT PATTERN NOT FOUND (graphcast version drift?)", file=sys.stderr)
PYEOF

echo "[4/4] stage GenCast weights ($GCX_HOME/{params,stats})"
if [ -d /mnt/data/models/gencast/params ]; then
  ln -sfn /mnt/data/models/gencast/params "$GCX_HOME/params"
  ln -sfn /mnt/data/models/gencast/stats  "$GCX_HOME/stats"
else
  "$PY" - "$GCX_HOME" <<'PYEOF'
import os, sys, json, urllib.parse, urllib.request
root=sys.argv[1]; bucket="dm_graphcast"
def listing(prefix):
    url=f"https://storage.googleapis.com/storage/v1/b/{bucket}/o?prefix={prefix}&fields=items(name,size)"
    import urllib.request as u
    with u.urlopen(url,timeout=30) as r: return json.load(r).get("items",[])
for o in listing("gencast/params/")+listing("gencast/stats/"):
    if o["name"].endswith("/"): continue
    rel=o["name"].split("gencast/",1)[1]; dest=os.path.join(root,rel)
    os.makedirs(os.path.dirname(dest),exist_ok=True)
    if os.path.exists(dest): continue
    print("  GET", rel); urllib.request.urlretrieve(
        f"https://storage.googleapis.com/{bucket}/"+urllib.parse.quote(o["name"],safe="/"), dest)
PYEOF
fi

echo "verify:"
JAX_PLATFORMS=cpu "$PY" -c "import ai_models_gencast.model, ai_models_graphcast.model, graphcast.gencast; print('  both plugins + gencast OK')"
echo "done — run with scripts/nwp/run_gencast.sh <members> <YYYYMMDD>"
