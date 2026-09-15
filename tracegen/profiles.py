"""配置驱动合成的分布、曲线与稳定随机种子。"""

import hashlib
import json
import math

from .validation import integer, positive
from .traffic import nonnegative


def stable_seed(*parts):
    encoded = json.dumps(parts, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8")
    return int.from_bytes(hashlib.sha256(encoded).digest()[:16], "big")


def fields(spec, allowed, name):
    if not isinstance(spec, dict):
        raise ValueError(f"{name} must be an object")
    unknown = set(spec) - set(allowed.split())
    if unknown:
        raise ValueError(f"{name}: unknown fields {sorted(unknown)}")


class Distribution:
    """非负分布；Gamma/Lognormal 用算术 mean、cv，截断采用 clip。"""

    def __init__(self, spec, name, *, count=False, minimum=0):
        self.name, self.count = name, count
        self.spec = spec if isinstance(spec, dict) else {"distribution": "fixed", "value": spec}
        self.kind = self.spec.get("distribution", "fixed")
        allowed = {"fixed": "value", "gamma": "mean cv min max",
                   "lognormal": "mean cv min max", "discrete": "values weights",
                   "mixture": "components weights"}
        if self.kind not in allowed:
            raise ValueError(f"{name}: unsupported distribution {self.kind}")
        fields(self.spec, "distribution " + allowed[self.kind], name)
        self.low = nonnegative(self.spec.get("min", minimum), name + ".min")
        self.high = self.spec.get("max")
        if self.low < minimum:
            raise ValueError(f"{name}.min must be >= {minimum}")
        if self.high is not None:
            nonnegative(self.high, name + ".max")
            if self.high < self.low:
                raise ValueError(f"{name}.max must be >= min")
        if count:
            integer(self.low, name + ".min", minimum)
            if self.high is not None:
                integer(self.high, name + ".max", minimum)
        if self.kind == "mixture":
            components = self.spec.get("components")
            if not isinstance(components, list) or not components:
                raise ValueError(f"{name}.components must be a nonempty list")
            if any(isinstance(c, dict) and c.get("distribution") == "mixture" for c in components):
                raise ValueError(f"{name}: nested mixtures are not supported")
            self.components = [Distribution(c, f"{name}.components[{i}]", count=count, minimum=minimum)
                               for i, c in enumerate(components)]
            self.weights = self.spec.get("weights", [1] * len(components))
            if not isinstance(self.weights, list) or len(self.weights) != len(components):
                raise ValueError(f"{name}.weights must match components")
            for weight in self.weights:
                nonnegative(weight, name + ".weight")
            positive(sum(self.weights), name + ".total_weight")
        elif self.kind == "fixed":
            self._value(self.spec.get("value"), minimum)
        elif self.kind == "discrete":
            values = self.spec.get("values")
            if not isinstance(values, list) or not values:
                raise ValueError(f"{name}.values must be a nonempty list")
            for value in values:
                self._value(value, minimum)
            weights = self.spec.get("weights", [1] * len(values))
            if not isinstance(weights, list) or len(weights) != len(values):
                raise ValueError(f"{name}.weights must match values")
            for weight in weights:
                nonnegative(weight, name + ".weight")
            positive(sum(weights), name + ".total_weight")
        else:
            positive(self.spec.get("mean"), name + ".mean")
            cv = nonnegative(self.spec.get("cv", 1), name + ".cv")
            if cv and (not math.isfinite(cv * cv) or cv * cv == 0):
                raise ValueError(f"{name}.cv is numerically out of range")

    def _value(self, value, minimum):
        nonnegative(value, self.name)
        if value < minimum:
            raise ValueError(f"{self.name} must be >= {minimum}")
        if self.count:
            integer(value, self.name, minimum)

    def sample(self, rng):
        s = self.spec
        if self.kind == "mixture":
            return rng.choices(self.components, self.weights, k=1)[0].sample(rng)
        if self.kind == "fixed":
            value = s["value"]
        elif self.kind == "discrete":
            value = rng.choices(s["values"], s.get("weights"), k=1)[0]
        else:
            mean, cv = s["mean"], s.get("cv", 1)
            if cv == 0:
                value = mean
            elif self.kind == "gamma":
                value = rng.gammavariate(1 / cv**2, mean * cv**2)
            else:
                variance = math.log1p(cv**2)
                value = rng.lognormvariate(math.log(mean) - variance / 2, math.sqrt(variance))
        if not math.isfinite(value):
            raise ValueError(f"{self.name}: nonfinite sample")
        value = max(self.low, value)
        if self.high is not None:
            value = min(self.high, value)
        return int(math.floor(value + .5)) if self.count else value


class Curve:
    """常数或分段线性/阶梯曲线，边缘保持；可周期重复。"""

    def __init__(self, spec, name):
        self.name = name
        self.period, self.phase = None, 0
        self.interpolation = "linear"
        if isinstance(spec, (int, float)) and not isinstance(spec, bool):
            self.points = [(0, nonnegative(spec, name))]
            return
        fields(spec, "points interpolation period phase", name)
        self.interpolation = spec.get("interpolation", "linear")
        if self.interpolation not in ("linear", "previous"):
            raise ValueError(f"{name}.interpolation must be linear or previous")
        self.points = spec.get("points")
        if not isinstance(self.points, list) or not self.points:
            raise ValueError(f"{name}.points must be nonempty [seconds, value] pairs")
        previous = -1
        for point in self.points:
            if not isinstance(point, (list, tuple)) or len(point) != 2:
                raise ValueError(f"{name}.points must contain pairs")
            t, value = point
            nonnegative(t, name + ".time")
            nonnegative(value, name + ".value")
            if t <= previous:
                raise ValueError(f"{name}.points times must be strictly increasing")
            previous = t
        if "period" in spec:
            self.period = positive(spec["period"], name + ".period")
            if self.points[0][0] != 0 or self.points[-1][0] > self.period:
                raise ValueError(f"{name}: periodic points must start at 0 and end <= period")
        self.phase = nonnegative(spec.get("phase", 0), name + ".phase")
        if self.phase and self.period is None:
            raise ValueError(f"{name}.phase requires period")

    def __call__(self, t):
        if self.period is not None:
            t = (t - self.phase) % self.period
        if t <= self.points[0][0]:
            return self.points[0][1]
        for (a, x), (b, y) in zip(self.points, self.points[1:]):
            if t < b:
                return x if self.interpolation == "previous" else x + (y - x) * (t - a) / (b - a)
        return self.points[-1][1]

    def knots(self, duration):
        if self.period is None:
            return [t for t, _ in self.points if 0 < t < duration]
        times = {t for t, _ in self.points} | {0, self.period}
        begin = math.floor(-self.phase / self.period) - 1
        end = math.ceil((duration - self.phase) / self.period) + 1
        return [x for cycle in range(begin, end + 1) for t in times
                if 0 < (x := self.phase + cycle * self.period + t) < duration]


class Bursts:
    """重叠 burst 的乘数相乘，加量相加；加量单位随所在层级变化。"""

    def __init__(self, specs, duration, name):
        if not isinstance(specs, list):
            raise ValueError(f"{name} must be a list")
        self.specs = specs
        for s in specs:
            fields(s, "start duration multiplier addition ramp", name)
            start = nonnegative(s.get("start"), name + ".start")
            length = positive(s.get("duration"), name + ".duration")
            if start + length > duration:
                raise ValueError(f"{name}: burst must fit within duration")
            nonnegative(s.get("multiplier", 1), name + ".multiplier")
            nonnegative(s.get("addition", 0), name + ".addition")
            ramp = nonnegative(s.get("ramp", 0), name + ".ramp")
            if 2 * ramp > length:
                raise ValueError(f"{name}.ramp must be <= duration / 2")

    def __call__(self, rate, t):
        factor, addition = 1, 0
        for s in self.specs:
            x = t - s["start"]
            if 0 <= x < s["duration"]:
                ramp = s.get("ramp", 0)
                strength = min(1, x / ramp, (s["duration"] - x) / ramp) if ramp else 1
                factor *= 1 + (s.get("multiplier", 1) - 1) * strength
                addition += s.get("addition", 0) * strength
        return rate * factor + addition

    def knots(self, duration):
        return [t for s in self.specs for t in
                (s["start"], s["start"] + s.get("ramp", 0),
                 s["start"] + s["duration"] - s.get("ramp", 0), s["start"] + s["duration"])
                if 0 < t < duration]
