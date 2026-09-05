# vLLM as a third-party engine for NVIDIA Personal AI Router

[NVIDIA PAIR](https://github.com/NVIDIA/Personal-AI-Router) ships engine support
for Ollama and LM Studio. Both are llama.cpp underneath, which leaves out most of
how people actually serve models on a DGX Spark — vLLM, SGLang, TensorRT-LLM.

**Read this first:** upstream is moving on both fronts. [PR #9](https://github.com/NVIDIA/Personal-AI-Router/pull/9)
adds vLLM as a first-class bundled engine, including a `{model}` placeholder and
an `engine:set-model` operation, which is a better answer than this adapter for
vLLM specifically. [Issue #24](https://github.com/NVIDIA/Personal-AI-Router/issues/24)
argues for adopting arbitrary OpenAI-compatible backends instead of adding
runtimes one at a time. What remains useful here is the demonstration that an
*unbundled* engine already works cluster-wide today, and the two gaps neither of
those has closed.

The good news, and the reason this repo is small: **PAIR's Go services already
accept new engines.** Engines are declarative JSON manifests, not compiled-in
cases. `nvpair-engine-manager` embeds its own manifests and then overlays
anything in `<appdir>/engines/`, and when an override names an engine that isn't
bundled it is registered as a new one rather than rejected. Nothing here patches
or forks PAIR; the binaries are stock.

What this repo adds is the piece a manifest genuinely cannot express, plus the
manifest that uses it.

## Why an adapter is needed at all

Ollama and LM Studio host many models and load them on demand. A manifest
describes that shape well: point at a binary, declare a port, list the HTTP
actions for `list_models`, `load_model` and so on.

vLLM and SGLang bind one model for the lifetime of the process. Swapping models
means restarting the server with different arguments, and in released builds a
manifest cannot say that — `runtime.args` is static and `{model}` is not among
the placeholders the runner resolves. (PR #9 adds exactly that placeholder plus
`engine:set-model`; once it lands, a bundled engine no longer needs an adapter
for this. The adapter still applies to engines PAIR does not bundle, and to
backends that need a supervisor for other reasons — containers, for one.)
So `pair-vllm-engine` sits on one stable port that PAIR
talks to and owns the child engine underneath, which turns load and unload into
real operations:

    GET  /health      readiness and health probes
    GET  /v1/models   models this node can serve
    GET  /ps          the resident model, for loaded_models
    GET  /status      state and last error detail
    POST /load        {"model": "..."} — (re)start the child on that model
    POST /unload      stop the child, free the GPU
    ANY  /v1/*        proxied to the running child

Backends are a JSON config, so the same adapter fronts vLLM natively, vLLM in a
container, or SGLang — see `examples/`. `--mock` runs the state machine with no
GPU, which is how the PAIR integration can be tested quickly.

## Installing

Drop the adapter and the manifest into PAIR's per-user engine directory, which is
`~/.config/Nvidia Corporation/Personal AI Router` on Linux:

```bash
mkdir -p "$HOME/.config/Nvidia Corporation/Personal AI Router/engine-bin/vllm"
install -m 755 pair-vllm-engine \
  "$HOME/.config/Nvidia Corporation/Personal AI Router/engine-bin/vllm/"
cp manifests/vllm.json "$HOME/.config/Nvidia Corporation/Personal AI Router/engines/"

mkdir -p ~/.config/pair-multi-engine
cp examples/vllm-native.json ~/.config/pair-multi-engine/vllm.json   # edit models[]
```

Manifests are read once at startup, so restart engine-manager to pick it up. The
UI broker supervises it with exponential backoff and a restart budget, so killing
it is enough — it comes back within a second or two. Match on the executable
rather than the command line, because `pgrep -f` will match your own shell:

```bash
for p in $(ls /proc | grep -E "^[0-9]+$"); do
  [ "$(readlink /proc/$p/exe 2>/dev/null)" = \
    "/opt/PAIR/resources/cli-bin/nvpair-engine-manager" ] && kill -TERM $p
done
```

It worked if the log reads `loaded 3 engine manifest(s): [lmstudio ollama vllm]`.

## What works, measured

Verified on a 10-node DGX Spark cluster (GB10, aarch64, Ubuntu 24.04) against
stock PAIR 0.91.7 / engine-manager 0.17.4, with two nodes participating.

Locally, driving engine-manager over its stdio JSON-RPC: the engine starts, lists
its catalog, loads a real model, serves a real chat completion, unloads and stops.
Across the cluster over the pinned-mTLS control surface, in both directions
between two nodes: `/v1/engines` lists the new engine, `/v1/engines/start` starts
it remotely, `/v1/models/load` loads a model that then shows up as resident under
`loadedByEngine.vllm`, and unload and stop tear it down and free the GPU. Peer
model lists carry it correctly, keyed by engine name:

```json
"modelsByEngine": {"lmstudio": ["..."], "vllm": ["LiquidAI/LFM2.5-350M"]}
```

So the data plane and the control plane both handle an engine NVIDIA has never
heard of, cluster-wide, with no modified binaries anywhere.

## What does not work, and why

The desktop app ignores it. With the engine installed, running and healthy on two
nodes, and its model listed by both, the Electron UI showed no vLLM row on any
node card — no error and no broken rendering, just absence. That is what the code
says should happen: `EngineTypes` is a closed literal union of `'ollama'` and
`'lm-studio'`, narrowed via `isEngineType()` at the service-bridge boundary, and
`DispatcherBackend` is the same two names again, so inference routing is closed
too. PR #9 handles this by extending the enumeration
(`Extract<EngineType, 'ollama' | 'lm-studio' | 'vllm'>`, `PROXY_ENGINES`) rather
than opening it, so each new runtime still costs a UI change — which is the
argument issue #24 is making.

Three smaller things in the Go layer also stand in the way of engines beyond the
two that ship.

Action HTTP calls get a 30 second response-header timeout, except that Ollama
gets ten minutes — and the long-timeout client is selected by a literal
`engine == "ollama"` comparison in `actions.go`. Any third-party engine whose
load takes longer than thirty seconds therefore cannot use a synchronous
`load_model` at all. This adapter works around it by acking `/load` immediately
and letting the caller poll `loaded_models`, and by warming the model during load
so the first `run_model` doesn't pay compile cost, but the timeout really wants to
be a manifest field.

`ActionHTTP` carries only a method, a path and a body schema. There is no way to
send a header, so an engine behind authentication cannot be driven by a manifest
at all — Unsloth Studio, for instance, returns 401 on `/v1/models`.

And `{model}` is not a resolvable placeholder in `runtime.args` in released
builds, which is the gap this adapter exists to fill — addressed upstream by
PR #9, which is the right fix and supersedes the adapter for bundled engines.

The first two, by contrast, are untouched by PR #9: its only timeout changes are
unrelated test values, and it adds no header support. Both would still block a
generic OpenAI-compatible backend.

## Operational notes

Killing a `docker run` client does not stop the container, so container backends
need `stop_command` (see `examples/vllm-docker.json`) or the GPU stays occupied
after unload.

If native vLLM dies immediately with a permission error, check whether
`~/.cache/vllm/flashinfer_autotune_cache` is root-owned from an earlier container
run; `VLLM_CACHE_ROOT` points it somewhere writable.

The adapter writes child output to a log and returns its tail in the `/load`
error, because a bare exit code tells you nothing about why an engine failed to
come up.

## Licence

Apache-2.0, matching upstream, so anything here can be lifted into a PR without
a licence question. Not affiliated with or endorsed by NVIDIA.
