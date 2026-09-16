"""Pulse RGB effects — pure frame renderers.

base.py     Effect ABC + EffectContext + ParamSpec (the frozen contract)
builtin.py  shipped effect implementations
catalog.py  registry + param validation

All Qt-free and I/O-free: effects render frames, nothing else.
"""
