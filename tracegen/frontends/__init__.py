"""Dataset frontends: configure(parser), convert(records, options, context)."""

from importlib import import_module

BUILTINS = {
    "weka": "tracegen.frontends.weka:WekaFrontend",
    "swissai": "tracegen.frontends.swissai:SwissAIFrontend",
    "lmsys": "tracegen.frontends.lmsys:LMSYSFrontend",
}


def load_frontend(name):
    target = BUILTINS.get(name, name)
    if ":" not in target:
        raise ValueError(f"unknown frontend {name!r}; choose {', '.join(BUILTINS)} or module:Class")
    module, symbol = target.split(":", 1)
    return getattr(import_module(module), symbol)()
