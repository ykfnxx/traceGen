"""Behavioral tests for random new-block lengths and prefix-tree preservation."""

from collections import defaultdict
import json
from pathlib import Path
import statistics
import tempfile
import unittest

from tracegen.generator import Instance, generate
from tracegen.sources import compile_session


ROOT = Path(__file__).resolve().parents[1]


def template(paths, scope="local"):
    return compile_session({"requests": [
        {"timestamp": i, "hash_ids": path} for i, path in enumerate(paths)]},
        1, "test", 0, scope=scope)


def lcp(a, b):
    n = 0
    for x, y in zip(a, b):
        if x != y:
            break
        n += 1
    return n


class NewBlockVariation(unittest.TestCase):
    def test_future_branch_and_endpoint_survive_shortening(self):
        a = list(range(100))
        b = a[:40] + list(range(1000, 1080))
        t = template([a, b, a, a[:70], b + [9000]*50, []])
        instance = Instance("s", t, 0, {}, jitter=1, seed=7)

        class Shrink:
            def uniform(self, lo, hi):
                return .1
            def random(self):
                return .9

        instance.variation.rng = Shrink()
        output = [instance.materialize(r.hashes) for r in t.requests]
        self.assertEqual(list(map(len, output)), [10, 12, 10, 7, 17, 0])
        self.assertEqual(lcp(output[0], output[1]), 4)
        self.assertEqual(output[0], output[2])
        self.assertEqual(output[3], output[0][:7])
        self.assertEqual(output[1], output[4][:12])
        self.assertTrue(set(output[0][4:]).isdisjoint(output[1][4:]))
        self.assertEqual(instance.variation.stats["reference_new_blocks"], 230)
        self.assertEqual(instance.variation.stats["synthetic_new_blocks"], 23)

    def test_random_lengths_and_instance_isolation(self):
        t = template([list(range(100)), list(range(200)), list(range(200))])
        sizes = []
        owners = {}
        for i in range(200):
            instance = Instance(f"s{i}", t, 0, {}, jitter=.3, seed=42)
            a, b, repeat = [instance.materialize(r.hashes) for r in t.requests]
            self.assertEqual(a, b[:len(a)])
            self.assertEqual(b, repeat)
            self.assertTrue(70 <= len(a) <= 130)
            self.assertTrue(70 <= len(b)-len(a) <= 130)
            sizes.append(len(b)-len(a))
            for h in set(b):
                self.assertNotIn(h, owners)
                owners[h] = i
        self.assertGreater(len(set(sizes)), 30)
        self.assertLess(min(sizes), 90)
        self.assertGreater(max(sizes), 110)
        self.assertAlmostEqual(statistics.mean(sizes), 100, delta=4)

    def test_randomized_tree_relationships_and_historical_reuse(self):
        # Includes revisiting non-adjacent branches, shortening, and a new root.
        paths = [list(range(30)), list(range(10))+[100, 101, 102],
                 list(range(50)), list(range(8)), [900, 901, 902],
                 list(range(10))+[100, 101, 102], []]
        t = template(paths)
        for seed in range(30):
            instance = Instance("s", t, 0, {}, jitter=1, seed=seed)
            output = [instance.materialize(r.hashes) for r in t.requests]
            depths = {(): 0}
            for i, a in enumerate(paths):
                for j, b in enumerate(paths):
                    source_common = lcp(a, b)
                    target_common = lcp(output[i], output[j])
                    key = tuple(a[:source_common])
                    self.assertEqual(depths.setdefault(key, target_common), target_common)
                    self.assertEqual(source_common == len(a), target_common == len(output[i]))
                    self.assertEqual(source_common == 0, target_common == 0)
            seen = set()
            for hashes in output:
                known = [h in seen for h in hashes]
                self.assertEqual(known, sorted(known, reverse=True))
                self.assertEqual(len(set(hashes)), len(hashes))
                seen.update(hashes)
            self.assertEqual(len(seen), instance.variation.stats["synthetic_new_blocks"])

    def test_global_scope_requires_explicit_exact_replay(self):
        t = template([[1, 2, 3]], scope="global")
        with self.assertRaisesRegex(ValueError, "new_block_jitter=0"):
            Instance("a", t, 0, {}, jitter=.3)
        a = Instance("a", t, 0, {}, jitter=0)
        b = Instance("b", t, 0, {}, jitter=0)
        self.assertEqual(a.materialize(t.requests[0].hashes), b.materialize(t.requests[0].hashes))

    def test_variation_changes_content_only_and_has_golden(self):
        config = json.loads((ROOT / "examples/demo.json").read_text())
        config["seed"] = 43  # Tiny segments still exhibit an actual count change.
        for dataset in config["datasets"]:
            dataset["path"] = str(ROOT / "examples" / dataset["path"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"out.jsonl"
            actual = generate(config, path)
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            again = generate(config, path)
            self.assertEqual(actual["output_sha256_uncompressed"], again["output_sha256_uncompressed"])
            default = dict(config)
            del default["new_block_jitter"]
            self.assertEqual(generate(default, path)["output_sha256_uncompressed"],
                             actual["output_sha256_uncompressed"])
            config["new_block_jitter"] = 0
            baseline = generate(config, path)
            old = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([(r["session_id"], r["timestamp"]) for r in rows],
                             [(r["session_id"], r["timestamp"]) for r in old])
            self.assertEqual(actual["session_concurrency"], baseline["session_concurrency"])
            self.assertNotEqual([r["hash_ids"] for r in rows], [r["hash_ids"] for r in old])
            seen = defaultdict(set)
            reference_unique = defaultdict(set)
            changed = 0
            for r, ref in zip(rows, old):
                sid = r["session_id"]
                new = set(r["hash_ids"])-seen[sid]
                ref_new = set(ref["hash_ids"])-reference_unique[sid]
                changed += len(new) != len(ref_new)
                seen[sid].update(new)
                reference_unique[sid].update(ref_new)
            self.assertEqual(changed, actual["stats"]["requests_with_changed_new_blocks"])
            self.assertGreater(changed, 0)
            self.assertEqual(sum(map(len, seen.values())), actual["stats"]["synthetic_new_blocks"])
            self.assertEqual(sum(map(len, reference_unique.values())), actual["stats"]["reference_new_blocks"])
            golden = json.loads((ROOT / "tests/golden_nonempty_variation_v1.json").read_text())
            self.assertEqual(actual["output_sha256_uncompressed"], golden["sha256"])
            self.assertEqual(actual["stats"], golden["stats"])
            self.assertEqual(rows[:5], golden["first_rows"])

    def test_invalid_jitter_does_not_replace_output(self):
        config = json.loads((ROOT / "examples/demo.json").read_text())
        for dataset in config["datasets"]:
            dataset["path"] = str(ROOT / "examples" / dataset["path"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder)/"out.jsonl"
            path.write_text("keep")
            for value in (-.1, 1.1, True, float("nan"), "0.3", None):
                with self.subTest(value=value), self.assertRaisesRegex(ValueError, "new_block_jitter"):
                    generate(dict(config, new_block_jitter=value), path)
                self.assertEqual(path.read_text(), "keep")


if __name__ == "__main__":
    unittest.main()
