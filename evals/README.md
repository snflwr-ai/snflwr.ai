# snflwr.ai Evals

Evaluation harnesses that measure **output quality**, not just code correctness.
The test suite (`tests/`) proves the plumbing works; these answer "is the
result any good?"

## tutoring/ — tutoring-quality eval

Historically, snflwr.ai measured **safety** (false-positive rate on a curriculum
set, adversarial categories) but never **tutoring quality** — whether answers
are correct, age-appropriate, and pedagogically sound (guide, don't just tell).
This harness fills that gap.

### What it scores

Each dataset case (a realistic student turn at a known age band) is run through
the model, and the response is scored two ways:

**Deterministic scorers** (`scorers.py`, pure, run in CI — no model needed):

| Signal | What it measures |
|---|---|
| `length_score` | Response word-count vs the band's target (from the modelfile: K-2 30–50, 3-5 50–75, 6-8 75–125, 9-12 125–200 words) |
| `readability_score` | Flesch-Kincaid grade vs the band's grade range. **Soft signal** — conversational tutoring reads "young" on FK, so treat low readability as a hint, not a verdict; the judge's `age_fit` is more reliable |
| `asks_guiding_question` | Whether the response invites the student to think (Socratic signal) |
| `reveals_answer` | For **homework-integrity probes** ("just give me the answer to 7×8"), whether the tutor simply handed over the answer (`56`) instead of guiding |

**LLM judge** (`judge.py`, optional) scores four rubric dimensions 0–2:
`correctness`, `pedagogy`, `age_fit`, `tone`. The backend is pluggable — default
is a local Ollama judge model (privacy-aligned); pass any `generate(prompt)`
callable to use a stronger external judge.

A per-case **composite (0–100)** is the mean of the available deterministic and
judge scores. Overall **PASS threshold is 70**.

### Running it

```bash
# Against a live Ollama model (the real signal):
python -m evals.tutoring.run_eval --model snflwr.ai --base-url http://localhost:11434

# With the LLM judge for correctness/pedagogy/age_fit/tone:
python -m evals.tutoring.run_eval --model snflwr.ai --judge llama-guard3:8b

# Offline — score pre-recorded responses (CI / no model):
python -m evals.tutoring.run_eval --responses responses.jsonl
```

`responses.jsonl`: one `{"id": "<case-id>", "response": "<text>"}` per line.

Outputs a markdown report (`--out`) and JSON (`--json-out`) with per-band and
per-subject breakdowns, and exits non-zero if the overall composite is below
threshold (so it can gate a nightly job once a model is wired into CI).

### Dataset

`dataset.yaml` — v1 has 24 cases spanning math / science / reading / writing
across all four age bands, plus homework-integrity and off-topic-redirect
probes. **It is intentionally small; expand it.** Quality is only as measured
as this set is broad — add cases for every subject area and failure mode you
care about.

### Why this isn't a CI gate (yet)

Running the eval needs a live model, which CI doesn't have. The **scorers and
judge-parsing logic are unit-tested in `tests/test_tutoring_eval.py`** and do
run in CI. Wire the full eval into a nightly job (alongside the Postgres DR
job) once a model endpoint is available to CI.
