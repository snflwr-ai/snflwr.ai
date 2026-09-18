# Inference engines and the serving plan

snflwr.ai runs the tutor on one of two engines, chosen from the hardware it finds
at start-up. Everything above the engine — the safety pipeline, the topic gate,
the homework guidance enforcer, the history ledger — is identical either way.

| | Ollama | vLLM |
|---|---|---|
| Protocol | `/api/chat` | OpenAI `/v1/chat/completions` |
| Concurrency | one request at a time | many, batched by the engine |
| Shared system prompt | re-processed per request | cached across requests |
| Where it runs | Linux, macOS, Windows, CPU or GPU | Linux with an NVIDIA/AMD GPU |
| Persona source | the Modelfile, applied by Ollama | sent by snflwr with every request |

## The serving plan

`core/serving_plan.py` produces one `ServingPlan` per process: engine, tutor
model, context window, how many turns may run at once, and a human-readable
reason. `GET /health/detailed` reports it, and it is logged once at start-up:

```
serving plan: engine=ollama model=snflwr.ai-31b tier=certified tutoring=on
              num_ctx=16384 slots=1 (snflwr.ai-31b certified 2026-09-17)
```

### The quality floor

A backbone may serve children only if a sealed tutoring run certified it, and a
certification belongs to an **(engine, model, context)** triple — not to a model
name. `CERTIFIED_BACKBONES` records each one with the date of the run that
certified it.

When no certified backbone fits the hardware, the tutor is switched off and says
so. It does not fall back to a smaller model: measured on 121 probes, the
smaller backbones produced between 4 and 13 replies containing wrong content
against a bar of 6, and the one configuration that fixed correctness refused to
help 38 times. A tutor that teaches wrong content is worse than no tutor.

vLLM serves different weight formats than Ollama's 4-bit files, so a vLLM
deployment starts as `unverified`. It refuses to tutor until either a sealed run
certifies it, or an operator sets `SNFLWR_ALLOW_UNVERIFIED_ENGINE=1` to
benchmark it deliberately.

### Capacity

Measured on an RTX 3090 Ti (23 GB) with Ollama and the full pipeline:

| Students at once | Typical wait | Slowest 10% | Canned fallbacks |
|---|---|---|---|
| 3 | 16.3 s | 29.0 s | 0 of 24 |
| 12 | 78.5 s | 116.7 s | 3 of 48 |
| 20 | 127.7 s | 156.8 s | 40 of 60 |

Throughput was flat at about 5.7 messages a minute from 1 to 8 concurrent
requests, because Ollama serves one at a time. That is why the app admits one
turn at a time on the Ollama path, and lets vLLM schedule itself.

The 20-student row is the reason admission control exists: those 40 fallbacks
were not bad tutoring, they were queue time eating the reveal check's budget
until it failed closed. Over capacity, snflwr.ai now returns a short "ask me
again in a moment" message and serves no tutoring turn at all.

## Context window

The window is probed at install time, not calculated. On the reference card:

| Window | Result |
|---|---|
| 16384 | loads, ~21.0 GiB of card in use |
| 24576 | loads, ~21.6 GiB |
| 32768 | out of memory — the model unloads |

Arithmetic said 32768 would fit and it did not: the compute buffers a forward
pass allocates are not in the resident figure. So the installer loads the model
at 32k, 24k, 16k, 8k, 4k in turn, keeps the first that loads *and* answers, and
writes it to `INFERENCE_NUM_CTX`.

At runtime the serving plan serves that window **capped at the largest window a
tutoring run has validated** for the backbone
(`CertifiedBackbone.max_validated_num_ctx`). A card with room to spare does not
get an unmeasured window: bigger context changes the KV layout and gives the
model more room to run past the guided shape, which is a quality question.
Raising the cap means running the dev campaign at the larger window and
comparing it to the certified baseline.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `INFERENCE_ENGINE` | `auto` | `auto`, `ollama` or `vllm` |
| `VLLM_BASE_URL` | `http://vllm:8000` | where the vLLM server listens |
| `INFERENCE_MAX_CONCURRENT` | from the plan | cap on simultaneous turns |
| `INFERENCE_QUEUE_WAIT_S` | `20` | how long a turn may wait for a slot |
| `INFERENCE_MAX_QUEUE` | `64` | queued turns before rejecting immediately |
| `INFERENCE_NUM_CTX` | probed at install | context window, capped at the validated maximum |
| `TUTOR_MODELFILE_PATH` | `models/Snflwr_AI_Kids.modelfile` | persona and sampling source |
| `SNFLWR_ALLOW_UNVERIFIED_ENGINE` | unset | allow tutoring on an uncertified engine |
| `INFERENCE_REMOTE_URL` | unset | offload inference to a tutor server (see below) |
| `INFERENCE_REMOTE_TOKEN` | unset | bearer credential this box presents to that server |
| `INFERENCE_SERVER_TOKEN` | unset | set on the SERVER: the credential it accepts from clients |
| `INFERENCE_REMOTE_PLAN_TTL_S` | `300` | how often a remote's plan is re-verified |

`auto` picks vLLM only when the box runs Linux, has an NVIDIA GPU, and a vLLM
server answers `/v1/models`. Anything else uses Ollama.

## Remote mode: offloading inference to a tutor server

A box that cannot serve a certified backbone does not have to go without a
tutor. Setting `INFERENCE_REMOTE_URL` points it at another snflwr install that
has the GPU, and **local hardware detection is skipped entirely** — a machine
with no GPU must not be asked about a remote one.

**Only inference turns cross the network.** Accounts, conversation history,
parental controls, the safety pipeline, COPPA handling and the guidance enforcer
all stay on the local box. The remote sees a prompt and returns text.

On the client:

```bash
INFERENCE_REMOTE_URL=https://tutor.school.example
INFERENCE_REMOTE_TOKEN=<credential issued by the server operator>
```

On the tutor server:

```bash
INFERENCE_SERVER_TOKEN=<the same credential>
```

A server with no `INFERENCE_SERVER_TOKEN` is not offering remote inference and
answers `404`.

### What the client checks before it will tutor

The client fetches `GET /api/inference/plan` and verifies the advertised
`(engine, model, num_ctx)` against **its own** certified table. If the remote
serves an uncertified model or engine, or a context window above the validated
ceiling, the client refuses to tutor and says why in health. The check lives on
the client on purpose: a server that has been misconfigured or quietly
downgraded must not be able to hand a child a weaker tutor than the one that
passed the sealed run.

This protects against misconfiguration and silent downgrade. It does **not**
protect against a hostile server, which still does the generating — run the
tutor server yourself, or choose who does.

### Re-verification

A remote plan describes *another* machine's configuration, which someone can
change without telling this box. So a remote plan is re-verified every
`INFERENCE_REMOTE_PLAN_TTL_S` seconds (default 300): the next turn after the TTL
expires re-fetches the remote's plan and re-applies the certified check. A server
repointed at an uncertified backbone stops receiving turns within that window
rather than at the next restart.

Local plans are **not** re-checked — they describe this box's own hardware, which
does not change underneath a running process, and a re-check would put a
detection probe on a child's turn for nothing.

If the re-verify cannot *reach* the remote, the last verified plan is kept. Being
unable to ask is not evidence that the answer changed, and failing closed on a
network blip would tell a child the tutor is "not available on this computer"
when the truth is "try again in a moment" — which is what the normal
engine-unreachable path already says when the turn itself fails.

Cost: one small plan fetch on one turn every five minutes.

### Transport

Plain HTTP is allowed only to loopback. Any other host must be `https`, and the
client refuses to start otherwise: a bearer credential and a child's text are
crossing that link. Capacity belongs to the server — the client uses the slot
count the server advertises and maps its `429`/`503` onto the normal "ask me
again in a moment" busy message.

## Running vLLM

```bash
docker compose -f docker/compose/docker-compose.yml \
               -f docker/compose/docker-compose.vllm.yml up -d
```

The compose file passes the plan's engine arguments (`--max-model-len`,
`--max-num-seqs`, `--gpu-memory-utilization`) and pins `--no-enable-log-requests`,
because a request body is a child's question: request logging must never be left
to an upstream default.

vLLM needs roughly 32 GB of GPU memory for this backbone. Measured 2026-09-17,
every 4-bit build of the 31b model is 19-21 GB, because the model is multimodal
and parts stay unquantised, so a 24 GB card runs out of memory while loading.
Those boxes stay on Ollama, and the plan says so in its reason.

Before any child is served from a vLLM deployment, run the tutoring comparison
against the certified Ollama baseline and record the result. Until that passes,
the plan keeps tutoring off on that engine.

## Adding an engine

1. Implement `InferenceDriver` (`core/inference/base.py`): `chat`, `stream`,
   `health`, `capabilities`.
2. Translate to and from the Ollama shape, as `core/inference/ollama_shape.py`
   does for the OpenAI protocol. Nothing above the driver may need changing.
3. Add the engine to the plan's selection rules with the hardware it requires.
4. Run the driver contract tests (`tests/test_inference_drivers.py`) against it.
5. Certify it with a sealed tutoring run before any child uses it, then add the
   triple to `CERTIFIED_BACKBONES` with that date.
