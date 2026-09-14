"""配置驱动核心：时钟、随机流、token 核算和前缀关系。"""

import copy
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from tracegen.clientpool import ClientPool
from tracegen.synthesis import generate
from tracegen.profiles import Curve, Distribution
from tracegen.synthesis import Session
from tracegen.analysis import analyze
from experiments.run_synthetic import run_config


def config():
    return dict(version=3, duration=10, seed=7, block_size=4,
                traffic=dict(session_rate=1, arrival={"cv": 0}),
                prefix_groups=[dict(key="shared", tokens=5, scope="task")],
                tasks=[dict(key="chat", clients={"count": 1},
                            prefix_groups=[dict(key="shared")],
                            session=dict(requests=3, initial_private_tokens=1,
                                         growth_multiplier=1, external_tokens=1,
                                         output_tokens=2, gap=1))])


class Synthesis(unittest.TestCase):
    def run_trace(self, cfg, suffix=".jsonl"):
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / ("trace" + suffix)
            manifest = generate(cfg, output)
            blob = output.read_bytes()
            raw = gzip.decompress(blob) if suffix.endswith("gz") else blob
            return [json.loads(line) for line in raw.splitlines()], manifest, blob

    def session(self, cfg, ordinal=0, client_index=0):
        pool = ClientPool(cfg)
        return Session(pool, pool.clients[client_index], ordinal, 0)

    def test_token_accounting_and_partial_tail(self):
        rows = list(self.session(config()).requests(10))
        self.assertEqual([r["input_tokens"] for r in rows], [6, 9, 12])
        self.assertEqual([len(r["hash_ids"]) for r in rows], [1, 2, 3])
        self.assertEqual(rows[1]["hash_ids"], rows[2]["hash_ids"][:2])
        # 尾部跨过多轮才成为完整 block，不能每轮单独 floor。
        c = config(); c["prefix_groups"][0]["tokens"] = 0
        c["tasks"][0]["session"].update(requests=5, output_tokens=0)
        rows = list(self.session(c).requests(10))
        self.assertEqual([len(r["hash_ids"]) for r in rows], [0, 0, 0, 1, 1])

    def test_group_scope_and_private_isolation(self):
        for scope, shared in [("task", True), ("global", True), ("client", False)]:
            c = config(); c["prefix_groups"][0]["scope"] = scope
            c["tasks"][0]["clients"] = {"count": 2}
            a = list(self.session(c, 0, 0).requests(10))[-1]["hash_ids"]
            b = list(self.session(c, 1, 0).requests(10))[-1]["hash_ids"]
            d = list(self.session(c, 0, 1).requests(10))[-1]["hash_ids"]
            self.assertEqual(a[:1], b[:1])
            self.assertNotEqual(a[1:], b[1:])
            self.assertEqual(a[:1] == d[:1], shared)
            self.assertNotEqual(a[1:], d[1:])
        c = config(); second = copy.deepcopy(c["tasks"][0]); second["key"] = "other"
        c["tasks"].append(second)
        for scope in ("global", "task"):
            c["prefix_groups"][0]["scope"] = scope
            a = list(self.session(c, client_index=0).requests(10))[0]["hash_ids"]
            b = list(self.session(c, client_index=1).requests(10))[0]["hash_ids"]
            self.assertEqual(a == b, scope == "global")

    def test_replay_order_and_gzip(self):
        c = config(); c["tasks"][0]["clients"] = [{"key": "b"}, {"key": "a"}]
        other = copy.deepcopy(c["tasks"][0]); other["key"] = "zz_agent"
        c["tasks"].append(other)
        a, m, blob = self.run_trace(c, ".jsonl.gz")
        self.assertEqual(blob, self.run_trace(c, ".jsonl.gz")[2])
        c["tasks"].reverse()
        for task in c["tasks"]: task["clients"].reverse()
        b, n, other_blob = self.run_trace(c, ".jsonl.gz")
        self.assertEqual(a, b)
        self.assertEqual(blob, other_blob)
        self.assertEqual(m["output_sha256_uncompressed"], n["output_sha256_uncompressed"])
        self.assertEqual([r["timestamp"] for r in a], sorted(r["timestamp"] for r in a))

    def test_session_streams_independent_of_start_and_other_sources(self):
        c = config(); p = c["tasks"][0]["session"]
        p.update(requests={"distribution": "discrete", "values": [3, 8, 12]},
                 external_tokens={"distribution": "gamma", "mean": 10, "cv": .8},
                 output_tokens={"distribution": "lognormal", "mean": 10, "cv": .4},
                 gap={"distribution": "gamma", "mean": 2, "cv": 1})
        original = self.session(c)
        changed = copy.deepcopy(c); changed["traffic"]["session_rate"] = 3
        second = copy.deepcopy(changed["tasks"][0]); second["key"] = "other"
        changed["tasks"].append(second)
        self.assertEqual(original.plan, self.session(changed).plan)
        self.assertEqual(original.seed, self.session(changed).seed)
        self.assertNotEqual(original.plan, self.session(c, 1).plan)
        changed["tasks"][0]["session"]["gap"] = 9
        altered = self.session(changed)
        self.assertEqual([(r["input_tokens"], r["output_tokens"]) for r in original.plan],
                         [(r["input_tokens"], r["output_tokens"]) for r in altered.plan])
        changed = copy.deepcopy(c); changed["tasks"][0]["session"]["output_tokens"] = 8
        altered = self.session(changed)
        self.assertEqual([r["timestamp"] for r in original.plan], [r["timestamp"] for r in altered.plan])
        self.assertEqual([r["external_tokens"] for r in original.plan], [r["external_tokens"] for r in altered.plan])

    def test_cutoff_zero_gaps_and_empty_requests(self):
        c = config(); c["duration"] = 3
        rows, m, _ = self.run_trace(c)
        self.assertEqual([r["timestamp"] for r in rows], [1, 2, 2])
        self.assertEqual(m["stats"]["truncated_sessions"], 2)
        self.assertEqual(m["stats"]["omitted_requests_at_end"], 3)
        c["tasks"][0]["session"].update(gap=0, initial_private_tokens=0, output_tokens=0, external_tokens=0)
        c["prefix_groups"][0]["tokens"] = 0
        rows, m, _ = self.run_trace(c)
        self.assertEqual(len(rows), 6)
        self.assertTrue(all(r["hash_ids"] == [] for r in rows))
        self.assertEqual(m["stats"]["peak_concurrent_sessions"], 0)
        c["traffic"]["session_rate"] = 0
        rows, m, _ = self.run_trace(c)
        self.assertEqual(rows, [])
        self.assertEqual(m["stats"]["actual_rps"], 0)

    def test_weight_normalization_and_local_burst(self):
        c = config(); c["traffic"]["session_rate"] = 12
        t = c["tasks"][0]; t["weight"] = 2; t["clients"] = [{"key": "a", "weight": 1}, {"key": "b", "weight": 3}]
        other = copy.deepcopy(t); other.update(key="other", weight=1, clients={"count": 1})
        c["tasks"].append(other)
        p = ClientPool(c)
        self.assertEqual([client.segments[0]["session_rate"] for client in p.clients], [2, 6, 4])
        t["clients"][0]["bursts"] = [dict(start=0, duration=10, multiplier=3)]
        p = ClientPool(c)
        self.assertEqual([client.segments[0]["session_rate"] for client in p.clients], [6, 6, 4])
        self.assertEqual(p.rate_segments[0]["session_rate"], 16)

    def test_renewal_residual_across_valley(self):
        c = config()
        c["traffic"]["session_rate"] = dict(points=[[0,.5],[1,0],[3,.5]], interpolation="previous")
        starts = list(ClientPool(c).clients[0].offers(7))
        self.assertEqual(starts, [4, 6, 8])

    def test_growth_multiplier_and_metadata_toggle(self):
        c = config(); c["tasks"][0]["session"]["growth_multiplier"] = 3
        session = self.session(c)
        self.assertEqual([r["input_tokens"] for r in session.plan], [6, 11, 16])
        a, m, _ = self.run_trace(c)
        c["output"] = {"request_metadata": False}
        b, n, _ = self.run_trace(c)
        self.assertEqual([{k:r[k] for k in ("timestamp", "session_id", "hash_ids")} for r in a], b)
        self.assertEqual(m["stats"], n["stats"])
        self.assertEqual(m["sessions"], n["sessions"])

    def test_validation_before_output(self):
        cases = []
        for field, value in [("version", 2), ("seed", True), ("datasets", []), ("calibration", {}), ("block_size", 0)]:
            c = config(); c[field] = value; cases.append(c)
        c = config(); c["tasks"][0]["session"]["requests"] = 0; cases.append(c)
        c = config(); c["tasks"][0]["session"]["gap"] = -1; cases.append(c)
        c = config(); c["tasks"][0]["request_ratio"] = .5; cases.append(c)
        c = config(); c["tasks"][0]["prefix_groups"] = [{"key": "missing"}]; cases.append(c)
        c = config(); c["traffic"]["bursts"] = [{"start": 9, "duration": 2}]; cases.append(c)
        for c in cases:
            with self.subTest(config=c), tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "trace.jsonl"; path.write_text("keep")
                with self.assertRaises(ValueError): generate(c, path)
                self.assertEqual(path.read_text(), "keep")

    def test_periodic_curve_and_distribution_clipping(self):
        curve = Curve(dict(points=[[0,0],[2,4],[4,0]], period=4, phase=1), "test")
        self.assertEqual([curve(t) for t in [1,2,3,5,6]], [0,2,4,0,2])
        d = Distribution(dict(distribution="gamma", mean=10, cv=0, min=1, max=3), "test", count=True)
        self.assertEqual(d.sample(None), 3)

    def test_statistics_and_window_do_not_change_trace(self):
        c = config(); c["duration"] = 3
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "input.json"
            path.write_text(json.dumps(c))
            a = run_config(path, Path(tmp) / "one", window=1, plots=False)
            b = run_config(path, Path(tmp) / "two", window=2, plots=False)
            self.assertEqual((Path(tmp) / "one/trace.jsonl").read_bytes(),
                             (Path(tmp) / "two/trace.jsonl").read_bytes())
            self.assertEqual([v["rps"] for v in b["time_series"]], [.5, 2])
            self.assertEqual(a["historical_prefix_reuse"]["block_references"], 4)
            self.assertEqual(a["historical_prefix_reuse"]["reused_blocks"], 2)
            self.assertEqual(a["historical_prefix_reuse"]["within_session_blocks"], 1)
            self.assertEqual(a["historical_prefix_reuse"]["extra_cross_session_blocks"], 1)
            self.assertEqual([v["samples"] for v in a["context_by_request"]], [2, 1])
            c["output"] = {"request_metadata": False}
            manifest = generate(c, Path(tmp) / "minimal.jsonl")
            minimal = analyze(Path(tmp) / "minimal.jsonl", manifest)
            self.assertFalse(minimal["request_metadata"])
            self.assertEqual(minimal["context_by_request"], [])
            self.assertEqual(minimal["historical_prefix_reuse"], a["historical_prefix_reuse"])

    def test_behavior_draws_do_not_change_client_clock(self):
        c = config(); c["traffic"]["arrival"] = {"cv": 1.4}
        _, a, _ = self.run_trace(c)
        c["tasks"][0]["session"].update(requests=12, output_tokens=120,
            external_tokens={"distribution": "lognormal", "mean": 100, "cv": 1})
        _, b, _ = self.run_trace(c)
        self.assertEqual([(s["session_id"], s["start"]) for s in a["sessions"]],
                         [(s["session_id"], s["start"]) for s in b["sessions"]])


if __name__ == "__main__":
    unittest.main()
