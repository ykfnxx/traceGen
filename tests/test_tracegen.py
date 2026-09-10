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
from unittest.mock import Mock

from tracegen.generator import arrival_intervals, build_datasets, generate
from tracegen.sources import compile_session
from tracegen.frontends.weka import normalize_weka
from tracegen.frontends.common import prefix_hashes
from tracegen.traffic import offer_times, rate_segments


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
    record = {"requests": [{"timestamp": i, "hash_ids": prefix_hashes(tokens, size)}
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
        template = compile_session(normalize_weka(record, 4), 4, "a", 0)
        self.assertEqual([r.offset for r in template.requests], [0, 0.5, 1.5, 2])
        self.assertEqual([len(r.hashes) for r in template.requests], [2, 2, 3, 3])
        a, b, c, d = [r.hashes for r in template.requests]
        self.assertEqual(a[0], b[0])
        self.assertNotEqual(a[1], b[1])
        self.assertEqual(b, c[:2])
        self.assertEqual(a, d[:2])

    def test_hash_only_tail_and_unsupported_resolution(self):
        record = {"block_size": 2, "requests": [
            {"t": 0, "in": 7, "hash_ids": [10, 11, 12]}]}
        template = compile_session(normalize_weka(record, 4), 4, "x", 0)
        self.assertEqual(len(template.requests[0].hashes), 1)
        for size in (1, 3):
            with self.subTest(size=size), self.assertRaisesRegex(ValueError, "multiple"):
                normalize_weka(record, size)
        record["requests"][0]["in"] = 9
        with self.assertRaisesRegex(ValueError, "cover exactly"):
            normalize_weka(record, 4)

    def test_global_identity_and_local_isolation(self):
        record = {"requests": [{"timestamp": 0, "hash_ids": [10, 11]}]}
        a = compile_session(record, 2, "x", 0, scope="global")
        b = compile_session(record, 2, "x", 1, scope="global")
        self.assertEqual(a.requests[0].hashes, b.requests[0].hashes)
        from tracegen.generator import Instance
        self.assertEqual(Instance("a", a, 0, {}).materialize(a.requests[0].hashes),
                         Instance("b", b, 0, {}).materialize(b.requests[0].hashes))
        a = compile_session(record, 2, "x", 0, "session_jsonl")
        x = Instance("a", a, 0, {})
        y = Instance("b", a, 0, {})
        self.assertEqual(x.materialize(a.requests[0].hashes), x.materialize(a.requests[0].hashes))
        self.assertTrue(set(x.materialize(a.requests[0].hashes)).isdisjoint(
            y.materialize(a.requests[0].hashes)))

    def test_invalid_reference_fails(self):
        for offset in (-1, float("nan"), True):
            with self.subTest(offset=offset), self.assertRaises(ValueError):
                compile_session({"requests": [{"timestamp": offset, "hash_ids": []}]},
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
        config["new_block_jitter"] = 0  # Exact replay of the minimal-format reference.
        a, b = self.path / "a.jsonl", self.path / "b.jsonl"
        manifest = generate(config, a)
        generate(config, b)
        self.assertEqual(a.read_bytes(), b.read_bytes())
        golden = json.loads((ROOT / "tests/golden_nonempty_v1.json").read_text())
        self.assertEqual(manifest["output_sha256_uncompressed"], golden["sha256"])
        self.assertEqual(manifest["stats"], golden["stats"])
        self.assertEqual(rows(a)[:3], golden["first_rows"])
        self.assertEqual(manifest["session_concurrency"], golden["session_concurrency"])

    def test_order_schema_template_coverage_and_timing(self):
        config = demo_config()
        config["new_block_jitter"] = 0
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
            self.assertEqual(len(row["hash_ids"]), len(request.hashes))
            self.assertAlmostEqual(row["timestamp"], info["start"] + request.offset)
            self.assertTrue(all(type(h) is int and 0 <= h < 2**64 for h in row["hash_ids"]))
            counts[row["session_id"]] += 1
        for sid, n in counts.items():
            self.assertEqual(n, sessions[sid]["emitted_requests"])
        self.assertGreater(manifest["stats"]["unique_templates"], 1)

    def test_longer_duration_keeps_original_request_prefix(self):
        config = demo_config()
        generate(config, self.path / "a.jsonl")
        config.update(duration=120)
        generate(config, self.path / "b.jsonl")
        a, b = rows(self.path / "a.jsonl"), rows(self.path / "b.jsonl")
        self.assertEqual(a, [r for r in b if r["timestamp"] < 60])

    def test_cap_changes_session_starts_not_internal_timing_or_hashes(self):
        config = demo_config()
        manifests, outputs = [], []
        for limit in (1, 3, 100):
            config["max_concurrent_sessions"] = limit
            path = self.path / f"{limit}.jsonl"
            manifest = generate(config, path)
            self.assertLessEqual(manifest["stats"]["peak_concurrent_sessions"], limit)
            self.assertEqual(manifest["stats"]["active_sessions_at_end"],
                             manifest["stats"]["truncated_sessions"])
            self.assertEqual(manifest["stats"]["offered_sessions"],
                             manifest["stats"]["sessions"] + manifest["stats"]["pending_sessions_at_end"])
            manifests.append(manifest)
            outputs.append(rows(path))
        for a, b in zip(manifests, manifests[1:]):
            self.assertEqual(a["offered_session_starts"], b["offered_session_starts"])
        for limited, uncapped in zip(outputs[:2], [outputs[2]] * 2):
            for sid in {r["session_id"] for r in limited}:
                a = [r for r in limited if r["session_id"] == sid]
                b = [r for r in uncapped if r["session_id"] == sid]
                self.assertLessEqual(len(a), len(b))
                for x, y in zip(a, b):
                    self.assertEqual(x["hash_ids"], y["hash_ids"])
                    self.assertAlmostEqual(x["timestamp"] - a[0]["timestamp"],
                                           y["timestamp"] - b[0]["timestamp"])

    def test_exact_fifo_admission_release_and_area(self):
        source = self.path / "session.jsonl"
        source.write_text(json.dumps({"requests": [
            {"timestamp": t, "hash_ids": [1]}
            for t in (0, 4, 8)]}) + "\n")
        config = dict(block_size=4, duration=11, max_concurrent_sessions=1,
                      session_rate=1, arrival={"cv": 0}, datasets=[{"name": "a", "path": str(source)}])
        manifest = generate(config, self.path / "fifo.jsonl")
        self.assertEqual([r["timestamp"] for r in rows(self.path / "fifo.jsonl")], [1, 5, 9, 9])
        self.assertEqual([(s["offered_start"], s["start"], s["end"])
                          for s in manifest["sessions"]], [(1, 1, 9), (2, 9, 17)])
        stats = manifest["stats"]
        self.assertEqual(stats["peak_concurrent_sessions"], 1)
        self.assertAlmostEqual(stats["mean_concurrent_sessions"], 10 / 11)
        self.assertEqual(stats["offered_sessions"], 10)
        self.assertEqual(stats["pending_sessions_at_end"], 8)
        self.assertEqual(stats["max_start_delay_seconds"], 7)

    def test_nested_session_releases_after_last_nested_arrival(self):
        source = self.path / "nested.jsonl"
        source.write_text(json.dumps({"block_size": 4, "requests": [
            {"t": 0, "in": 4, "hash_ids": [1]},
            {"t": 1, "requests": [{"t": 10, "in": 4, "hash_ids": [2]}]},
            {"t": 2, "in": 4, "hash_ids": [3]}]}) + "\n")
        source.write_text(json.dumps(normalize_weka(json.loads(source.read_text()), 4))+'\n')
        config = dict(block_size=4, duration=13, max_concurrent_sessions=1, session_rate=1,
                      arrival={"cv": 0}, datasets=[{"name": "a", "path": str(source)}])
        manifest = generate(config, self.path / "nested_out.jsonl")
        self.assertEqual([s["start"] for s in manifest["sessions"]], [1, 11])

    def test_zero_duration_sessions_release_immediately(self):
        source = self.path / "instant.jsonl"
        source.write_text(json.dumps({"requests": [
            {"timestamp": 0, "hash_ids": []}, {"timestamp": 0, "hash_ids": [1]}]}) + "\n")
        config = dict(block_size=4, duration=5, max_concurrent_sessions=1, session_rate=1,
                      arrival={"cv": 0}, datasets=[{"name": "a", "path": str(source)}])
        manifest = generate(config, self.path / "instant_out.jsonl")
        self.assertEqual(manifest["stats"]["requests"], 4)
        self.assertEqual(manifest["stats"]["mean_concurrent_sessions"], 0)
        self.assertEqual(manifest["stats"]["active_sessions_at_end"], 0)

    def test_boundary_accounting_and_empty_window(self):
        config = demo_config()
        manifest = generate(config, self.path / "a.jsonl")
        stats = manifest["stats"]
        self.assertGreater(stats["omitted_requests_at_end"], 0)
        self.assertEqual(stats["requests"] + stats["omitted_requests_at_end"],
                         sum(s["template_requests"] for s in manifest["sessions"]))
        config["duration"] = 1e-12
        config["bursts"] = []
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
        for key, value in (("max_concurrent_sessions", 0), ("duration", -1), ("block_size", True),
                           ("session_rate", float("nan")), ("seed", 1.5), ("load_scale", 1),
                           ("base_session_rate", 0.2)):
            config = demo_config()
            config[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                generate(config, path)
            self.assertEqual(path.read_text(), "keep me")
        config = demo_config()
        config["block_size"] = 3
        with self.assertRaisesRegex(ValueError, "block_size must match"):
            generate(config, path)
        self.assertEqual(path.read_text(), "keep me")
        self.assertEqual(sorted(p.name for p in self.path.iterdir()), ["output.jsonl"])
        config = demo_config()
        with self.assertRaisesRegex(ValueError, "overwrite"):
            generate(config, config["datasets"][0]["path"])

    def test_cli(self):
        result = subprocess.run([sys.executable, str(ROOT / "generate.py"), "--config",
                                 str(ROOT / "examples/demo.json"), "--output",
                                 str(self.path / "cli.jsonl"), "--max-concurrent-sessions", "2",
                                 "--new-block-jitter", "0.6"],
                                cwd=self.path, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(len(rows(self.path / "cli.jsonl")), 0)
        manifest = json.loads((self.path / "cli.jsonl.manifest.json").read_text())
        self.assertEqual(manifest["config"]["new_block_jitter"], 0.6)


class ArrivalSampling(unittest.TestCase):
    def test_sub_ulp_and_underflow_gaps_keep_all_offers(self):
        rng = Mock()
        rng.gammavariate.side_effect = [1.0, 1e-30, 0.0, 0.5, 1.0]
        times = list(offer_times(rng, rate_segments(dict(duration=2, session_rate=1)),
                                 {"cv": 3}))
        self.assertEqual(times, [1.0, 1.0, 1.0, 1.5])
        self.assertEqual(rng.gammavariate.call_count, 5)

    def test_negative_and_nonfinite_intervals_rejected(self):
        for value in [-1, float("nan"), float("inf")]:
            with self.subTest(value=value):
                rng = Mock()
                rng.gammavariate.return_value = value
                with self.assertRaisesRegex(ValueError, "negative/nonfinite"):
                    next(arrival_intervals(rng, 1, {"cv": 3}))

    def test_three_hour_bursty_offer_clock_regression(self):
        times = list(offer_times(random.Random("arrival:42"),
                                 rate_segments(dict(duration=10800, session_rate=10)),
                                 {"cv": 1.5}))
        self.assertGreater(len(times), 100000)
        self.assertTrue(all(0 <= t < 10800 for t in times))
        self.assertTrue(all(a <= b for a, b in zip(times, times[1:])))
        self.assertTrue(any(a == b for a, b in zip(times, times[1:])))

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
        config.update(duration=1000, session_rate=3, max_concurrent_sessions=10000, bursts=[])
        with tempfile.TemporaryDirectory() as folder:
            manifest = generate(config, Path(folder) / "mix.jsonl")
        sessions = manifest["sessions"]
        ratio = sum(s["source"] == "chat" for s in sessions) / len(sessions)
        self.assertAlmostEqual(ratio, 0.6, delta=0.04)
        self.assertEqual(manifest["stats"]["unique_templates"], 4)
        self.assertEqual({s["template_duration"] for s in sessions}, {2, 8, 14, 30})

    def test_deterministic_burst_and_return_to_base_rate(self):
        config = dict(duration=8, session_rate=1,
                      bursts=[{"start": 3, "duration": 2, "session_rate": 4}])
        times = list(offer_times(random.Random(1), rate_segments(config), {"cv": 0}))
        self.assertEqual(times, [1, 2, 3, 3.25, 3.5, 3.75, 4, 4.25, 4.5, 4.75, 5, 6, 7])

    def test_burst_only_and_zero_traffic(self):
        config = dict(duration=8, session_rate=0,
                      bursts=[{"start": 3, "duration": 2, "session_rate": 2}])
        self.assertEqual(list(offer_times(random.Random(1), rate_segments(config), {"cv": 0})),
                         [3.5, 4, 4.5, 5])
        config["bursts"] = []
        self.assertEqual(list(offer_times(random.Random(1), rate_segments(config), {})), [])

    def test_rate_boundary_preserves_arrival_residual(self):
        config = dict(duration=5, session_rate=0.5,
                      bursts=[{"start": 1, "duration": 1, "session_rate": 1}])
        self.assertEqual(list(offer_times(random.Random(1), rate_segments(config), {"cv": 0})),
                         [1.5, 3])

    def test_overlapping_bursts_rejected(self):
        config = dict(duration=8, session_rate=1, bursts=[
            {"start": 1, "duration": 3, "session_rate": 2},
            {"start": 3, "duration": 2, "session_rate": 4}])
        with self.assertRaisesRegex(ValueError, "overlap"):
            rate_segments(config)


if __name__ == "__main__":
    unittest.main()
