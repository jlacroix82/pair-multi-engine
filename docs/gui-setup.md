# vLLM and SGLang in the PAIR GUI

The implementation lives in
[jlacroix82/Personal-AI-Router, feat/vllm-sglang](https://github.com/jlacroix82/Personal-AI-Router/tree/feat/vllm-sglang),
based on upstream PR #9 at `a4b96a3`. Build the desktop and services from that
same branch on participating nodes. Stock PAIR still filters unknown engines.

## Existing cluster deployments

This is the intended mode for servers already managed by systemd, Docker, or
another runtime supervisor. No adapter installation is needed.

1. Keep vLLM and SGLang serving normally.
2. Build the branch using Node 25.5+ and Go 1.25+:

   ```sh
   git clone --branch feat/vllm-sglang https://github.com/jlacroix82/Personal-AI-Router.git
   cd Personal-AI-Router/desktop
   npm ci
   npm run build:modular-binaries
   npm run build:tools
   npm run build
   ```

3. Before starting the preview, exit the existing PAIR application and its
   broker. Two PAIR instances sharing configuration and ports must not run at once.
4. Start the built desktop with `npx electron .`. On each hosting node, configure
   the local Engine settings Server port: vLLM defaults to 8000; SGLang to 30000.
   Port configuration remains accessible before engine detection.
5. Node cards should show each reported engine separately. Use the PAIR OpenAI
   proxy endpoint shown in the UI for `/v1/models` and `/v1/chat/completions`.

Remote port editing is not implemented; set the port on the node that owns it.
Bare-host manual discovery probes the default ports. SGLang manual detection
requires `/get_model_info` and a valid OpenAI model list.
Externally managed processes remain under their supervisor: PAIR refuses to
terminate them for model/port changes. SGLang installation is operator-managed.
Identical model IDs on one node resolve in LM Studio, vLLM, SGLang order.
Use distinct served model IDs when a specific deployment must be selectable.

## Optional model-lifecycle adapters

The SGLang adapter manifest uses a separate engine ID, wrapper port 8802, child
port 8803, and explicit configuration path. vLLM uses wrapper 8800 and child 8801.

```sh
pair_appdir="$HOME/.config/Nvidia Corporation/Personal AI Router"
mkdir -p "$pair_appdir/engines" "$pair_appdir/engine-bin/sglang"
install -m 755 pair-vllm-engine "$pair_appdir/engine-bin/sglang/"
cp examples/sglang.json "$pair_appdir/engine-bin/sglang/sglang.json"
cp manifests/sglang.json "$pair_appdir/engines/sglang.json"
```

Edit the copied config's `models` and `command` before restarting PAIR.
The adapters launch their own children; they do not adopt your existing servers.
The GUI branch uses native single-model capabilities and does not expose the
adapters' `/load` and `/unload` as model controls. Drive those adapter operations
directly if using this mode; native adoption is the supported GUI workflow here.

## Validation

The PAIR branch has CPU-only regression coverage for mixed-engine desktop state,
adoption, SGLang probing, discovery withdrawal, and correct HTTP routing. GPU
inference and production-cluster rollout are separate validation steps.
The repository's adapter tests exercise two concurrent mock adapters, including
independent model state. Run them with `python3 -m unittest discover -s tests -v`.
