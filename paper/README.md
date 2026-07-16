# OpenThomas — paper draft

*The Harness Is the Edge: A Disciplined, Self-Improving Language-Model Agent for
Weather Prediction Markets.*

| File | For |
|---|---|
| `openthomas.tex` / `.pdf` | venue-neutral signed draft (plain `article`, 7 pp) — build-in-public / arXiv |
| `openthomas-icaif.tex` / `.pdf` | **ICAIF '26 submission** — `acmart` sigconf, anonymized (double-blind), 5 pp (limit 8 incl. refs) |
| `openthomas-neurips-ws.tex` / `.pdf` | **NeurIPS '26 workshop version** — RSI-focused, official NeurIPS style, 4 pp incl. refs |

The abstract in all builds is trimmed to <2000 characters (the ICAIF CMT field limit).

```bash
cd paper
# self-contained with tectonic (drops the TeX bundle on first run):
tectonic -X compile openthomas-icaif.tex        # -> openthomas-icaif.pdf
tectonic -X compile openthomas-neurips-ws.tex   # needs neurips_2025.sty (in this dir)
pdflatex openthomas.tex && pdflatex openthomas.tex
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

## Submitting to a NeurIPS '26 workshop

`openthomas-neurips-ws.pdf` is the RSI thread ("keeping the judge off the edit
surface") as a 4-page workshop paper.

- **Not the June 6 proposal deadline** — that was for *organizing* a workshop.
  Papers go to *accepted* workshops as **contributions**: suggested date
  **2026-08-29**, mandatory accept/reject by 2026-09-29. Each accepted workshop
  runs its own CFP on OpenReview.
- **Do now:** create an OpenReview account (approval can take 2+ weeks); watch the
  accepted-workshop list (published after the 2026-07-11 notification). Targets:
  Agentic AI / LLM agents, Open-Ended Learning (ALOE), Foundation Models for
  Decision-Making, ML-for-finance.
- **Style:** uses `neurips_2025.sty` as a stand-in; swap in the chosen workshop's
  `neurips_2026.sty`. Options in the `.tex` header: `[preprint]` (named, current),
  `[dblblindworkshop]` / `[sglblindworkshop]` (blind), `[final]` (camera-ready).
- **Non-archival:** most NeurIPS workshops allow work also under review elsewhere,
  so this does not conflict with an ICAIF submission (check each workshop's policy).

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

- The replay numbers are a real leak-free run over 968 settled markets
  (settlement 2026-06-24 to 07-14); re-run closer to camera-ready and update the
  window / trade counts.
- Fill in full citation details / DOIs in the bibliography (arXiv IDs are stubs).
- Keep the OSS-hygiene rule: no machine names, IPs, GPU UUIDs, container names, or
  absolute paths anywhere in the PDF.
