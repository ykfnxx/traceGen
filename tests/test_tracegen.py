from copy import deepcopy
import gzip
import hashlib
import json
import math
from pathlib import Path
import random
import statistics
import subprocess
import sys
import tempfile
import unittest

from tracegen.generator import arrival_intervals, build_datasets, generate
from tracegen.sources import compile_session


ROOT = Path(__file__).resolve().parents[1]


def demo_config():
    config = json.loads((ROOT / "examples/demo.json").read_text())
    for dataset in config["datasets"]:
        dataset["path"] = str(ROOT / "examples" / dataset["path"])
    return config


def rows(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rt") as stream:
        return [json.loads(line) for line in stream]


def compile_tokens(requests, size=4):
    record = {"id": "a", "requests": [{"timestamp": i, "token_ids": tokens}
                                         for i, tokens in enumerate(requests)]}
    return compile_session(record, size, "test", 0, "session_jsonl")


class HashSemantics(unittest.TestCase):
    def test_full_coverage_and_partial_tail(self):
        template = compile_tokens([list(range(n)) for n in range(14)])
        self.assertEqual([len(r.hashes) for r in template.requests], [n // 4 for n in range(14)])
        self.assertEqual(template.requests[8].hashes, template.requests[9].hashes)
        self.assertEqual(template.requests[7].hashes, template.requests[8].hashes[:1])

    def test_prefix_fork_and_no_suffix_reconvergence(self):
        template = compile_tokens([
            [1, 2, 3, 4, 5, 6], [1, 2, 7, 8, 5, 6], [9, 0, 3, 4, 5, 6],
            [1, 2, 3, 4, 5, 6],
        ], size=2)
        a, b, c, repeat = [r.hashes for r in template.requests]
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertNotEqual(a[2], b[2])
        self.assertTrue(all(x != y for x, y in zip(a, c)))
        self.assertEqual(a, repeat)

    def test_weka_coarsening_and_nested_absolute_offsets(self):
        record = json.loads((ROOT / "examples/data/agent.jsonl").read_text().splitlines()[0])
        template = compile_session(record, 4, "a", 0, "weka")
        self.assertEqual([r.offset for r in template.requests], [0, 0.5, 1.5, 2])
        self.assertEqual([len(r.hashes) for r in template.requests], [2, 2, 3, 3])
        a, b, c, d = [r.hashes for r in template.requests]
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertEqual(b, c[:2])
        self.assertEqual(a, d[:2])

    def test_hash_only_tail_and_unsupported_resolution(self):
        record = {"block_size": 2, "requests": [
            {"timestamp": 0, "num_tokens": 7, "hash_ids": [10, 11, 12]}]}
        template = compile_session(record, 4, "x", 0, "session_jsonl")
        self.assertEqual(len(template.requests[0].hashes), 1)
        for size in (1, 3):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "multiple"):
                compile_session(record, size, "x", 0, "session_jsonl")
        record["requests"][0]["num_tokens"] = 9
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            compile_session(record, 4, "x", 0, "session_jsonl")

    def test_global_identity_and_local_isolation(self):
        record = {"block_size": 2, "hash_id_scope": "global", "requests": [
            {"timestamp": 0, "num_tokens": 4, "hash_ids": [10, 11]}]}
        a = compile_session(record, 2, "x", 0, "session_jsonl")
        b = compile_session(record, 2, "x", 1, "session_jsonl")
        self.assertEqual(a.requests[0].hashes, b.requests[0].hashes)
        from tracegen.generator import Instance
        self.assertEqual(Instance("a", a, 0, {}).materialize(a.requests[0].hashes),
                         Instance("b", b, 0, {}).materialize(b.requests[0].hashes))
        record["hash_id_scope"] = "local"
        a = compile_session(record, 2, "x", 0, "session_jsonl")
        x = Instance("a", a, 0, {})
        y = Instance("b", a, 0, {})
        self.assertEqual(x.materialize(a.requests[0].hashes), x.materialize(a.requests[0].hashes))
        self.assertTrue(set(x.materialize(a.requests[0].hashes)).isdisjoint(
            y.materialize(a.requests[0].hashes)))

    def test_invalid_reference_fails(self):
        for offset in (-1, float("nan"), True):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                compile_session({"requests": [{"timestamp": offset, "token_ids": []}]},
                                4, "a", 0, "session_jsonl")
        with self.assertRaisesRegex(ValueError, "at least one"):
            compile_session({"requests": []}, 4, "a", 0, "session_jsonl")


class Generation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    def test_deterministic_golden(self):
        config = demo_config()
        a, b = self.path / "a.jsonl", self.path / "b.jsonl"
        manifest = generate(config, a)
        generate(config, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        golden = json.loads((ROOT / "tests/golden_v1.json").read_text())
        self.assertEqual(manifest["output_sha256_uncompressed"], golden["sha256"])
        self.assertEqual(manifest["stats"], golden["stats"])
        self.assertEqual(rows(a)[:3], golden["first_rows"])

    def test_order_schema_template_coverage_and_timing(self):
        config = demo_config()
        datasets = build_datasets(config)
        manifest = generate(config, self.path / "out.jsonl", datasets=datasets)
        output = rows(self.path / "out.jsonl")
        sessions = {s["session_id"]: s for s in manifest["sessions"]}
        counts = dict.fromkeys(sessions, 0)
        previous = -1
        for row in output:
            self.assertEqual(set(row), {"timestamp", "hash_ids", "session_id"})
            self.assertGreaterEqual(row["timestamp"], previous)
            self.assertLess(row["timestamp"], config["duration"])
            previous = row["timestamp"]
            info = sessions[row["session_id"]]
            source = next(d for d in datasets if d.name == info["source"])
            template = source.get(info["source_record"])
            request = template.requests[counts[row["session_id"]]]
            self.assertEqual(len(row["hash_ids"]), request.num_tokens // config["block_size"])
            self.assertAlmostEqual(row["timestamp"], info["start"] + request.offset)
            self.assertTrue(all(type(h) is int and 0 <= h < 2**64 for h in row["hash_ids"]))
            counts[row["session_id"]] += 1
        for sid, n in counts.items():
            self.assertEqual(n, sessions[sid]["emitted_requests"])
        self.assertGreater(manifest["stats"]["unique_templates"], 1)

    def test_scale_common_sequence_and_fixed_duration(self):
        generated = []
        for scale in (0.5, 1, 2):
            config = demo_config()
            config["load_scale"] = scale
            path = self.path / f"{scale}.jsonl"
            generate(config, path)
            generated.append(rows(path))
        self.assertLess(len(generated[0]), len(generated[1]))
        self.assertLess(len(generated[1]), len(generated[2]))
        for lower, higher in zip(generated, generated[1:]):
            for a, b in zip(lower, higher):
                self.assertEqual(a["session_id"], b["session_id"])
                self.assertEqual(a["hash_ids"], b["hash_ids"])
                self.assertAlmostEqual(a["timestamp"], b["timestamp"] * 2)

    def test_complete_sequence_when_duration_is_rescaled(self):
        config = demo_config()
        generate(config, self.path / "a.jsonl")
        config.update(load_scale=2, duration=30)
        generate(config, self.path / "b.jsonl")
        a, b = rows(self.path / "a.jsonl"), rows(self.path / "b.jsonl")
        self.assertEqual(len(a), len(b))
        self.assertEqual(a, [dict(r, timestamp=r["timestamp"] * 2) for r in b])

    def test_boundary_accounting_and_empty_window(self):
        config = demo_config()
        manifest = generate(config, self.path / "a.jsonl")
        stats = manifest["stats"]
        self.assertGreater(stats["omitted_requests_at_end"], 0)
        self.assertEqual(stats["requests"] + stats["omitted_requests_at_end"],
                         sum(s["template_requests"] for s in manifest["sessions"]))
        config["duration"] = 1e-12
        manifest = generate(config, self.path / "empty.jsonl")
        self.assertEqual(manifest["stats"]["requests"], 0)
        self.assertEqual(rows(self.path / "empty.jsonl"), [])

    def test_compressed_output(self):
        config = demo_config()
        a = generate(config, self.path / "a.jsonl")
        b = generate(config, self.path / "a.jsonl.gz")
        self.assertEqual(rows(self.path / "a.jsonl"), rows(self.path / "a.jsonl.gz"))
        self.assertEqual(a["output_sha256_uncompressed"], b["output_sha256_uncompressed"])

    def test_seed_changes_sample(self):
        config = demo_config()
        a = generate(config, self.path / "a.jsonl")
        config["seed"] += 1
        b = generate(config, self.path / "b.jsonl")
        self.assertNotEqual(a["output_sha256_uncompressed"], b["output_sha256_uncompressed"])

    def test_invalid_config_and_source_never_replace_output(self):
        path = self.path / "output.jsonl"
        path.write_text("keep me")
        for key, value in (("load_scale", 0), ("duration", -1), ("block_size", True),
                           ("base_session_rate", float("nan")), ("seed", 1.5)):
            config = demo_config()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                generate(config, path)
            self.assertEqual(path.read_text(), "keep me")
        config = demo_config()
        config["block_size"] = 3
        with self.assertRaisesRegex(ValueError, "multiple"):
            generate(config, path)
        self.assertEqual(path.read_text(), "keep me")
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ["output.jsonl"])
        config = demo_config()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            generate(config, config["datasets"][0]["path"])

    def test_cli(self):
        result = subprocess.run([sys.executable, str(ROOT / "generate.py"), "--config",
                                 str(ROOT / "examples/demo.json"), "--output",
                                 str(self.path / "cli.jsonl"), "--load-scale", "2"],
                                cwd=self.path, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(rows(self.path / "cli.jsonl")), 0)


class ArrivalSampling(unittest.TestCase):
    def test_gamma_rate_and_burstiness(self):
        iterator = arrival_intervals(random.Random(17), 2, {"cv": 1.8})
        values = [next(iterator) for _ in range(50000)]
        mean = statistics.mean(values)
        self.assertAlmostEqual(mean, 0.5, delta=0.02)
        self.assertAlmostEqual(statistics.pstdev(values) / mean, 1.8, delta=0.06)

    def test_weibull_rate(self):
        iterator = arrival_intervals(random.Random(17), 2,
                                     {"distribution": "weibull", "shape": 0.7})
        values = [next(iterator) for _ in range(50000)]
        self.assertAlmostEqual(statistics.mean(values), 0.5, delta=0.02)

    def test_weighted_source_mix_and_session_heterogeneity(self):
        config = demo_config()
        config.update(duration=1000, base_session_rate=3)
        with tempfile.TemporaryDirectory() as folder:
            manifest = generate(config, Path(folder) / "mix.jsonl")
        sessions = manifest["sessions"]
        ratio = sum(s["source"] == "chat" for s in sessions) / len(sessions)
        self.assertAlmostEqual(ratio, 0.6, delta=0.04)
        self.assertEqual(manifest["stats"]["unique_templates"], 4)
        self.assertEqual({s["template_duration"] for s in sessions}, {2, 8, 15, 30})


if __name__ == "__main__":
    unittest.main()
