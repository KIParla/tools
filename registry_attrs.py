#!/usr/bin/env python3
"""
registry_attrs.py - Attributes shared by all the module corpora, for the
aggregated KIParla corpus.

The aggregated corpus only offers what every module offers: an attribute that
exists in some modules but not in others (the code-variation labels, the
translation, a metadata column that a module lacks, ...) is left out of both its
vertical (tsv2vert.py --common-attrs-of) and its registry (the `prune` command
below). The module registries are the single source of truth, so a new module
with fewer attributes narrows the aggregate by itself.

`variety` and `nonce` are code-variation labels: they are in every module
registry (empty where the module does not mark variation) but are not offered
by the aggregate either.

Usage:
    python3 tools/registry_attrs.py common  REGISTRY [REGISTRY ...]
    python3 tools/registry_attrs.py prune --modules REGISTRY [REGISTRY ...] --registry KIParla-registry
"""

import argparse
import re
import sys
from pathlib import Path

from textutil import ENCODING_READ, ENCODING_WRITE

# Pseudo-structure of the positional (per-token) attributes.
TOKEN = "token"
# Structures whose attributes follow the "common to all modules" rule.
FILTERED = (TOKEN, "conversation", "transcription_unit")
# Never offered by the aggregate, even though every module declares them.
ALWAYS_EXCLUDED = {"variety", "nonce"}

_STRUCTURE = re.compile(r"^STRUCTURE\s+(\w+)\s*\{")
_ATTRIBUTE_LIST = re.compile(r'^((?:SUBCORPATTRS|FREQTTATTRS)\s+)"([^"]*)"\s*$')
_ATTRIBUTE = re.compile(r"^(\s*)ATTRIBUTE\s+(\w+)\s*(\{)?\s*$")


def _scan(lines):
    """Yield (structure, attribute, first_line, last_line) for every attribute."""
    structure = TOKEN
    i = 0
    while i < len(lines):
        line = lines[i]
        m = _STRUCTURE.match(line)
        if m:
            structure = m.group(1)
        elif line.startswith("}") and structure != TOKEN:
            structure = TOKEN
        m = _ATTRIBUTE.match(line)
        if m:
            end = i
            if m.group(3):                     # block form: up to the closing brace
                indent = m.group(1)
                end = i + 1
                while not lines[end].startswith(f"{indent}}}"):
                    end += 1
            yield structure, m.group(2), i, end
            i = end
        i += 1


def read_attributes(path):
    """{structure: [attribute, ...]} in file order, for the filtered structures."""
    lines = Path(path).read_text(encoding=ENCODING_READ).splitlines()
    out = {s: [] for s in FILTERED}
    for structure, name, _, _ in _scan(lines):
        if structure in out:
            out[structure].append(name)
    return out


def common_attributes(registries):
    """{structure: [attribute, ...]}: what every registry declares, in the order
    of the first one, without ALWAYS_EXCLUDED."""
    per_registry = [read_attributes(r) for r in registries]
    common = {}
    for structure in FILTERED:
        shared = set.intersection(*(set(a[structure]) for a in per_registry))
        common[structure] = [
            n for n in per_registry[0][structure]
            if n in shared and n not in ALWAYS_EXCLUDED
        ]
    return common


def _listed_ok(item, keep):
    structure, _, name = item.partition(".")
    return structure not in keep or name in keep[structure]


def prune_registry(text, keep):
    """Drop from a registry the attributes that are not in `keep` (a result of
    common_attributes)."""
    lines = text.splitlines()
    drop = set()
    for structure, name, first, last in _scan(lines):
        if structure in keep and name not in keep[structure]:
            drop.update(range(first, last + 1))
    out = []
    for i, line in enumerate(lines):
        if i in drop:
            continue
        m = _ATTRIBUTE_LIST.match(line)
        if m:                  # "structure.attribute,..." lists must not name dropped ones
            items = [x for x in m.group(2).split(",") if _listed_ok(x, keep)]
            line = f'{m.group(1)}"{",".join(items)}"'
        out.append(line)
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("common", help="print the attributes shared by the registries")
    c.add_argument("registries", nargs="+")
    p = sub.add_parser("prune", help="remove the non-shared attributes from a registry, in place")
    p.add_argument("--modules", nargs="+", required=True, help="module registries")
    p.add_argument("--registry", required=True, help="registry to prune")
    args = ap.parse_args()

    if args.command == "common":
        for structure, names in common_attributes(args.registries).items():
            print(f"{structure}: {' '.join(names)}")
        return
    keep = common_attributes(args.modules)
    path = Path(args.registry)
    path.write_text(prune_registry(path.read_text(encoding=ENCODING_READ), keep),
                    encoding=ENCODING_WRITE)
    print(f"pruned {path}", file=sys.stderr)


if __name__ == "__main__":
    main()
