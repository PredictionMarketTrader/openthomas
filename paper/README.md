# OpenThomas — paper draft (build-in-public)

*The Harness Is the Edge: Leak-Free Evaluation of a Disciplined Language-Model
Agent on Weather Prediction Markets.*

| File | For |
|---|---|
| `openthomas-icaif.tex` / `.pdf` | **conference version** — `acmart` sigconf, anonymized (double-blind), ≤ 8 pp incl. refs. The living draft. |
| `openthomas.tex` / `.pdf` | venue-neutral signed draft (plain `article`) — arXiv / website. Older; re-derive from the conference version before use. |
| `openthomas-neurips-ws.tex` / `.pdf` | workshop version — the RSI thread ("keeping the judge off the edit surface"), 4 pp incl. refs. Older. |

## How this draft works

The conference version is written **build-in-public** (`docs/EXPERIMENTS.md`):

- Every empirical cell that is not yet measured is marked `\tbd{E#}` (orange
  box) and names the experiment that will fill it.
- Every conclusion that depends on pending data is marked `\pend{E#}{...}`
  (orange text). If the data go against the thesis, the sentence changes, not
  the data.
- The experiments E1–E9 and their **decision rules were written before the
  numbers** (Table "Pre-registered experiments" in the paper; same table in
  `docs/EXPERIMENTS.md`).
- Numbers come from `scripts/paper_data.sh`, which runs daily on the trading
  box and publishes frozen replay rows + results to
  [huggingface.co/openthomas](https://huggingface.co/openthomas)
  (`weather-forecasts`, folder `replay/`). Each table cites the frozen file and
  its digest.

Filling a cell: copy the number from `results-<start>-to-<end>.md` (the
`ablate` output) into the `.tex`, replace the `\tbd{}` with it, and cite the
digest in the table caption. When E1's control decides the thesis, rewrite the
`\pend{E1}{...}` sentences in the abstract, E1 paragraph, and conclusion.

The v0 numbers (21-day replay, June 24 – July 14, UTC snapshots) stay in the
paper as "preliminary, superseded"; they are not used for any conclusion.

## Building

```bash
cd paper
pdflatex openthomas-icaif.tex && pdflatex openthomas-icaif.tex   # 2 passes for refs
# or, self-contained:
tectonic -X compile openthomas-icaif.tex
```

## Submitting (conference)

- **ICAIF '26** (Milan, Nov 14–17 2026; CMT, *Research Papers* track) had a
  2026-08-02 deadline; this draft targets the next venue with an ACM-style
  page limit (≤ 8 pages including references, `sigconf`).
- **Double-blind:** the submitted PDF is anonymized (author masked, URLs
  withheld). Keep the repo public — ACM allows it — just don't de-anonymize
  the submission during review.
- **Camera-ready TODO** (in the `.tex` header): drop the `anonymous`/`review`
  options, restore `printacmref`, the copyright block, real authors, the
  repository / dataset URLs, remove `\draftstatus`, and make sure no `\tbd{}`
  is left.
