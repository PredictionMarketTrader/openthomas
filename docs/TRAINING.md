# Training a local forecaster on your journal

OpenThomas works with hosted APIs out of the box, but the journal it
accumulates is a fine-tuning dataset for a **local model that gets better on
the markets you actually trade**. This is the self-improvement path for users
with GPUs; nothing here phones home.

## Why bother

- Hosted frontier models are strong general forecasters but systematically
  miscalibrated per category — and you pay per token, forever.
- Research shows fine-tuning for calibration works (Halawi et al. 2024,
  arXiv:2402.18563), and OpenThomas already collects exactly the right data:
  `(question, rules, market price, your model's forecast, outcome)` for every
  settled market.
- A 12B-class open model (e.g. Gemma 12B) fine-tuned on a few thousand of your
  journal rows can match a much larger general model *on your market universe*,
  and runs on one consumer GPU. Larger open models (27B+, or bigger if your
  hardware allows) close more of the gap.

## Hardware guide

| Model class | Inference | LoRA fine-tune |
|---|---|---|
| ~12B (Gemma 12B) | 1× 24 GB GPU (Q4: 12 GB) | 1× 24 GB (QLoRA) |
| ~27–31B | 2× 24 GB or 1× 48 GB | 1–2× 48 GB (QLoRA) |
| 70B+ | 4× 24 GB+ | multi-GPU node |

## The pipeline

1. **Export the dataset** from your journal (settled markets only, first
   forecast per market to avoid hindsight leakage):

```bash
python scripts/train/export_dataset.py --out data/forecasts.jsonl
```

Each row: the exact forecast prompt OpenThomas used, and the settled outcome.
The split is **temporal** (train on the past, validate on the most recent 20%)
— random splits leak future information and will lie to you.

2. **Fine-tune with QLoRA** (needs `pip install 'openthomas[train]'`):

```bash
python scripts/train/finetune_lora.py \
  --base google/gemma-3-12b-it \
  --data data/forecasts.jsonl \
  --out models/openthomas-lora
```

The objective is calibration: the model is trained to output probability
tokens whose implied Brier score on held-out settlements beats the base model.

3. **Evaluate before trusting it.** The script reports held-out Brier score
   and a reliability table for base vs fine-tuned. Only switch the agent over
   if the fine-tune wins out-of-sample:

```bash
python scripts/train/evaluate.py --model models/openthomas-lora --data data/forecasts.jsonl
```

4. **Serve it and point OpenThomas at it:**

```bash
vllm serve models/openthomas-lora --port 8000
openthomas init --provider openai --base-url http://localhost:8000/v1 --model openthomas-lora
```

## Ground rules (recursive self-improvement, with brakes)

- The forecaster is the only trainable component. **The risk engine is never
  learned** — sizing and caps stay deterministic no matter how good the model
  looks.
- Data volume matters: below ~500 settled forecasts, stick with Platt scaling
  (already automatic); fine-tuning on tiny samples memorizes noise.
- Never evaluate on markets the model saw resolve during training
  (temporal split is enforced by the export script).
- Re-run the evaluation after every market regime you care about (elections,
  Fed cycles); a fine-tune can rot.

`scripts/train/` ships with the export script; the LoRA and eval scripts land
with the `[train]` extra as they stabilize — contributions welcome.
