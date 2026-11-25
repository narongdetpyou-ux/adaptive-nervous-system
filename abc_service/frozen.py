"""Verify and load the original controller; cache its compiled class, not experiences."""
from functools import lru_cache
import hashlib
import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FROZEN = Path(__file__).resolve().parent / "_frozen"
EXPECTED = {
    "experiment.py": "83aa8b68b2f68ab83a26ff56a651332878d5355bfff0a7aee985f72b05090342",
    "original/ABC.H.txt": "f8a50611ba65a935c2b0079241df3d7f90a0ef7746c647d88d0feb293e33af00",
    "action_catalog.json": "a36d6614f1c7af608451e75974c1df23ed68f78f4b098c5badb487a8dafc03eb",
}

def verify():
    actual = {name: hashlib.sha256((FROZEN/name).read_bytes()).hexdigest() for name in EXPECTED}
    if actual != EXPECTED:
        raise RuntimeError("Frozen ABC source or action catalog changed")
    return actual

@lru_cache(maxsize=1)
def module():
    verify()
    spec = importlib.util.spec_from_file_location("abc_frozen_207", FROZEN/"experiment.py")
    obj = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(obj)
    # Avoid repeatedly parsing the same immutable class file on each new session.
    # ABC_Causal207.decide and every policy constant remain byte-for-byte original.
    obj.original_class = lru_cache(maxsize=1)(obj.original_class)
    return obj

@lru_cache(maxsize=1)
def catalog():
    # Immutable policy configuration is loaded at startup; no observation,
    # episode history, score or experience is cached here.
    return json.loads((FROZEN/"action_catalog.json").read_text())
