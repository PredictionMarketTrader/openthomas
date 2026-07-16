# OpenThomas — paper draft

*The Harness Is the Edge: A Disciplined, Self-Improving Language-Model Agent for
Weather Prediction Markets.*

| File | For |
|---|---|
| `openthomas.tex` / `.pdf` | venue-neutral draft (plain `article`, retarget by swapping the class) |
| `openthomas-icaif.tex` / `.pdf` | **ICAIF '26 submission** — `acmart` sigconf, anonymized (double-blind), review line numbers, 5 pp (limit is 8 incl. refs) |

```bash
cd paper
# either builds with tectonic (self-contained) or a full TeX Live:
tectonic -X compile openthomas-icaif.tex     # -> openthomas-icaif.pdf
pdflatex openthomas.tex && pdflatex openthomas.tex   # the venue-neutral one
```

## Submitting to ICAIF '26

- **System:** Microsoft CMT — <https://cmt3.research.microsoft.com/ICAIF2026/>
- **Track:** *Research Papers* (not Competitions / Tutorials / Workshops).
- **Deadline:** 2026-08-02. **Conference:** Milan, Nov 14–17 2026.
- **Limit:** ≤ 8 pages **including references**, ACM `sigconf`. Over-length = desk reject.
- **Double-blind:** the submitted PDF is `openthomas-icaif.pdf` (author masked,
  URLs withheld). Keep the repo public — ACM allows it — just don't de-anonymize
  the submission during review.
- **Camera-ready TODO** (in the `.tex` header): drop the `anonymous`/`review`
  options, restore `printacmref`, the copyright block, real authors, and the
  repository / project URLs.

## Where to submit

**Primary target: ACM ICAIF** (International Conference on AI in Finance). Best
topical fit — LLM trading agents + prediction markets + a deterministic risk
engine are squarely in scope, and ICAIF is friendly to applied/systems work that
reports honest negative results. Retarget with `\documentclass{acmart}`.

Alternatives, by which thread you want to foreground:

| Venue | Foreground | Notes |
|---|---|---|
| **ICAIF** ⭐ | whole system; "harness is the edge" empirics | applied-friendly, primary target |
| **COLM** | LLM-agent harness + RSI (judge off the edit surface) | swap in the COLM style |
| **AAMAS** | autonomous trading agent + market interaction + risk architecture | has demo / blue-sky tracks |
| **NeurIPS / ICLR workshops** | RSI (ALOE / Open-Ended, Agentic AI); NWP (Climate Change AI) | lower bar, fast, best fit for build-in-public |
| **ACM EC** | prediction-market microstructure / pricing | needs more theory |

**Two-paper option (recommended if there's runway):**

- **(A) → ICAIF** — the empirical systems paper: harness-is-the-edge, the
  leak-free hindcast/replay methodology, per-station NWP-bias edge, the ablation.
- **(B) → COLM or an open-ended / agentic workshop** — the methods paper:
  "self-improvement with the evaluator off the edit surface," the kernel/agent
  plane split, and weather markets as a strong-evaluator RSI testbed.

`openthomas.tex` is written so section content can be split cleanly between the
two.

## Before submitting

- The replay numbers are a real 21-day run from 2026-07-08; refresh from the live
  paper run and state the exact window and trade count in the camera-ready.
- Fill in full citation details / DOIs in the bibliography (arXiv IDs are stubs).
- Keep the OSS-hygiene rule: no machine names, IPs, GPU UUIDs, container names, or
  absolute paths anywhere in the PDF.
