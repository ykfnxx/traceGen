"""独立 client 时钟与整体 session 发起强度的分配。"""

from dataclasses import dataclass
import math
import random

from .profiles import Bursts, Curve, Distribution, fields, stable_seed
from .validation import integer, positive
from .traffic import arrival_intervals, nonnegative, offer_times


def key(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f"{name} must be a nonempty string")
    return value


@dataclass
class Client:
    task: dict
    key: str
    weight: Curve
    activity: Curve
    bursts: Bursts
    arrival: dict
    segments: list

    def offers(self, seed):
        rng = random.Random(stable_seed(seed, self.task["key"], self.key, "arrival-v1"))
        return offer_times(rng, self.segments, self.arrival)


class ClientPool:
    def __init__(self, config):
        fields(config, "version duration seed block_size traffic tasks prefix_groups output notes", "config")
        if config.get("version") != 3:
            raise ValueError("tasks configuration requires version: 3")
        self.duration = positive(config.get("duration"), "duration")
        self.seed = integer(config.get("seed", 0), "seed")
        self.block_size = integer(config.get("block_size"), "block_size", 1)
        output = config.get("output", {})
        fields(output, "request_metadata", "output")
        if not isinstance(output.get("request_metadata", True), bool):
            raise ValueError("output.request_metadata must be boolean")
        traffic = config.get("traffic", {})
        fields(traffic, "session_rate resolution bursts arrival", "traffic")
        resolution = positive(traffic.get("resolution", 1), "traffic.resolution")
        self.rate = Curve(traffic.get("session_rate"), "traffic.session_rate")
        self.bursts = Bursts(traffic.get("bursts", []), self.duration, "traffic.bursts")
        self.groups = {}
        groups = config.get("prefix_groups", [])
        if not isinstance(groups, list):
            raise ValueError("prefix_groups must be a list")
        for group in groups:
            fields(group, "key tokens scope", "prefix_group")
            name = key(group.get("key"), "prefix_group.key")
            if name in self.groups:
                raise ValueError("prefix_group keys must be unique")
            integer(group.get("tokens"), "prefix_group.tokens")
            if group.get("scope", "task") not in ("global", "task", "client"):
                raise ValueError("prefix_group.scope must be global, task or client")
            self.groups[name] = group
        specs = config.get("tasks")
        if not isinstance(specs, list) or not specs:
            raise ValueError("tasks must be a nonempty list")
        self.tasks, self.clients = [], []
        names = set()
        curves = [self.rate, self.bursts]
        for spec in specs:
            fields(spec, "key weight clients arrival bursts session prefix_groups notes", "task")
            name = key(spec.get("key"), "task.key")
            if name in names:
                raise ValueError("task keys must be unique")
            names.add(name)
            weight = Curve(spec.get("weight", 1), name + ".weight")
            bursts = Bursts(spec.get("bursts", []), self.duration, name + ".bursts")
            profile = spec.get("session", {})
            fields(profile, "requests initial_private_tokens growth_multiplier external_tokens output_tokens gap", name + ".session")
            distributions = {}
            defaults = {"growth_multiplier": 1, "external_tokens": 0, "output_tokens": 0}
            for field in ("requests", "initial_private_tokens", "growth_multiplier", "external_tokens", "output_tokens", "gap"):
                distributions[field] = Distribution(profile.get(field, defaults.get(field)),
                    name + ".session." + field, count=field not in ("gap", "growth_multiplier"),
                    minimum=1 if field == "requests" else 0)
            choices = spec.get("prefix_groups", [])
            if not isinstance(choices, list):
                raise ValueError(name + ".prefix_groups must be a list")
            for choice in choices:
                fields(choice, "key weight", name + ".prefix_groups")
                if choice.get("key") not in self.groups:
                    raise ValueError(f"{name}: unknown prefix group {choice.get('key')}")
                nonnegative(choice.get("weight", 1), name + ".prefix_group.weight")
            if choices:
                positive(sum(c.get("weight", 1) for c in choices), name + ".prefix_group.total_weight")
            task = dict(key=name, weight=weight, bursts=bursts, profile=distributions,
                        prefix_groups=choices, clients=[])
            curves.extend([weight, bursts])
            client_specs = spec.get("clients", {"count": 1})
            if isinstance(client_specs, dict):
                fields(client_specs, "count weight_exponent", name + ".clients")
                count = integer(client_specs.get("count"), name + ".clients.count", 1)
                exponent = nonnegative(client_specs.get("weight_exponent", 0), name + ".clients.weight_exponent")
                client_specs = [dict(key=f"client-{i:06d}", weight=1 / (i + 1)**exponent)
                                for i in range(count)]
            if not isinstance(client_specs, list) or not client_specs:
                raise ValueError(name + ".clients must be a nonempty list or count object")
            client_names = set()
            for c in client_specs:
                fields(c, "key weight activity bursts arrival", name + ".client")
                client_key = key(c.get("key"), name + ".client.key")
                if client_key in client_names:
                    raise ValueError(name + ": client keys must be unique")
                client_names.add(client_key)
                arrival = c.get("arrival", spec.get("arrival", traffic.get("arrival", {})))
                kind = arrival.get("distribution", "gamma") if isinstance(arrival, dict) else None
                fields(arrival, "distribution " + ("cv" if kind == "gamma" else "shape"), "arrival")
                next(arrival_intervals(random.Random(0), 1, arrival))
                client = Client(task, client_key, Curve(c.get("weight", 1), name + ".client.weight"),
                    Curve(c.get("activity", 1), name + ".client.activity"),
                    Bursts(c.get("bursts", []), self.duration, name + ".client.bursts"), arrival, [])
                curves.extend([client.weight, client.activity, client.bursts])
                task["clients"].append(client)
                self.clients.append(client)
            self.tasks.append(task)
        self.tasks.sort(key=lambda t: t["key"])
        self.clients.sort(key=lambda c: (c.task["key"], c.key))
        for task in self.tasks:
            task["clients"].sort(key=lambda c: c.key)
        # 固定物理网格加所有拐点/跳变点；每段使用中点强度，不重置 renewal 残量。
        edges = {0, self.duration}
        edges.update(i * resolution for i in range(1, math.ceil(self.duration / resolution)))
        for curve in curves:
            edges.update(curve.knots(self.duration))
        edges = sorted(edges)
        self.rate_segments = []
        for start, end in zip(edges, edges[1:]):
            t = (start + end) / 2
            base = self.bursts(self.rate(t), t)
            total_weight = sum(task["weight"](t) for task in self.tasks)
            total, by_task = 0, {}
            for task in self.tasks:
                allocation = base * task["weight"](t) / total_weight if total_weight else 0
                task_rate = task["bursts"](allocation, t)
                client_weight = sum(c.weight(t) for c in task["clients"])
                effective = 0
                for c in task["clients"]:
                    share = task_rate * c.weight(t) / client_weight if client_weight else 0
                    rate = c.bursts(share * c.activity(t), t)
                    nonnegative(rate, "effective client session rate")
                    if not math.isfinite(rate * (end - start)):
                        raise ValueError("integrated client session rate out of range")
                    segment = dict(start=start, end=end, session_rate=rate)
                    if c.segments and c.segments[-1]["session_rate"] == rate:
                        c.segments[-1]["end"] = end
                    else:
                        c.segments.append(segment)
                    effective += rate
                by_task[task["key"]] = effective
                total += effective
            self.rate_segments.append(dict(start=start, end=end, configured_session_rate=base,
                                           session_rate=total, tasks=by_task))
