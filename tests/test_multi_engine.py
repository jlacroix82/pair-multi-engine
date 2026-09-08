"""CPU-only HTTP regression for independent vLLM and SGLang adapters."""

import json
from pathlib import Path
import runpy
import threading
import time
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

ROOT = Path(__file__).resolve().parents[1]
ADAPTER = runpy.run_path(str(ROOT / "pair-vllm-engine"))


class MultiEngineTest(unittest.TestCase):
    def start_adapter(self, engine):
        config = dict(ADAPTER["DEFAULT_CONFIG"], backend=engine, models=[engine + "-model"])
        supervisor = ADAPTER["Supervisor"](config, mock=True)
        handler = type(engine + "Handler", (ADAPTER["Handler"],), {"sup": supervisor})
        server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(supervisor.unload)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def request(self, base, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(base + path, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)

    def test_concurrent_adapters_keep_catalog_and_resident_state_separate(self):
        endpoints = {engine: self.start_adapter(engine) for engine in ("vllm", "sglang")}
        for engine, base in endpoints.items():
            catalog = self.request(base, "/v1/models")["data"]
            self.assertEqual([(m["id"], m["owned_by"]) for m in catalog], [(engine + "-model", engine)])
            self.assertTrue(self.request(base, "/load", {"model": engine + "-model"})["ok"])
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if all(self.request(base, "/status")["state"] == "ready" for base in endpoints.values()):
                break
            time.sleep(0.05)
        for engine, base in endpoints.items():
            self.assertEqual(self.request(base, "/ps")["models"], [{"name": engine + "-model"}])
        self.request(endpoints["sglang"], "/unload", {})
        self.assertEqual(self.request(endpoints["sglang"], "/ps")["models"], [])
        self.assertEqual(self.request(endpoints["vllm"], "/ps")["models"], [{"name": "vllm-model"}])

    def test_manifests_and_examples_use_separate_ports_and_configuration(self):
        manifests = [json.loads((ROOT / "manifests" / (e + ".json")).read_text()) for e in ("vllm", "sglang")]
        configs = [json.loads((ROOT / "examples" / f).read_text()) for f in ("vllm-native.json", "sglang.json")]
        ports = [m["runtime"]["port"] for m in manifests] + [c["child_port"] for c in configs]
        self.assertEqual(len(set(ports)), 4)
        self.assertEqual(manifests[1]["engine"], "sglang")
        args = manifests[1]["runtime"]["args"]
        self.assertEqual(args[args.index("--config") + 1], "{install_dir}/sglang.json")


if __name__ == "__main__":
    unittest.main()
