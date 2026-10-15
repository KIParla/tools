#!/usr/bin/env python3
"""regenerate_local.py - Rebuild the derived repositories from released modules, locally.

After one or more modules are released (tag `vX.Y.Z` on `main`), this script does what the
former GitHub workflows did (module `notify-*.yml` -> `KIParla-collection/build.yml` and
`KIParla-NoSketch-Data/regenerate-on-release.yml`), in one go and on the local machine, so that
nothing races and the result can be inspected before it is pushed:

1. **KIParla-collection**: copies `tsv/` and `eaf/` of the modules at their release tag, rebuilds
   the linear files, merges the metadata, regenerates `csv-metadata.json`, bumps the patch
   version in `CITATION.cff` and commits (one commit for all the modules).
2. **KIParla-NoSketch-Data**: for each module, translates the metadata, regenerates
   `<Corpus>/vertical/source` and updates INFO/INFOHREF in the registry; then the aggregate
   `KIParla` corpus from the collection just built. One commit.
3. With `--push`: pushes both repositories, tags the collection and creates its GitHub Release
   (`gh`). Without `--push` nothing leaves the machine: the commands are printed.

The module repositories are read at their tag with `git archive`, so their working trees
(and any local, unpushed work in them) are never touched.

Expected layout (override with --root): <root>/tools (this script), <root>/moduli/<Module>,
<root>/KIParla-collection, <root>/KIParla-NoSketch-Data.

Not covered: recompiling the running NoSketch Engine instance and the HTML/PDF pages of
`KIParla-artifacts` (`generate_artifacts.py`, which needs pandoc and xelatex). The commands for
the pages are printed at the end.

Examples:
    python regenerate_local.py                      # every module at its latest tag, no push
    python regenerate_local.py --modules KIP ParlaBZ
    python regenerate_local.py --tag KIP=v2.1.0 --push
"""
from __future__ import annotations

import argparse
import datetime
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
MODULES = ["KIP", "KIPasti", "ParlaBO", "ParlaTO", "ParlaBZ", "Stra-ParlaBO", "Stra-ParlaTO"]
ANNOTATION_LEVELS_URL = "https://kiparla.github.io/docs-site/portal/1.0/nosketch/annotation-levels.html"
ARTIFACTS_URL = "https://kiparla.github.io/KIParla-artifacts"


def run(cmd, cwd=None, stdout=None, check=True):
    cmd = [str(c) for c in cmd]
    return subprocess.run(cmd, cwd=cwd, stdout=stdout, check=check, text=True,
                          capture_output=stdout is None).stdout


def git(repo: Path, *args, check=True) -> str:
    return (run(["git", "-C", repo, *args], check=check) or "").strip()


def corpus_name(module: str) -> str:
    return module.replace("-", "")


def version_key(tag: str):
    return tuple(int(x) for x in re.findall(r"\d+", tag))


def latest_tag(repo: Path) -> str:
    tags = [t for t in git(repo, "tag", "--list", "v[0-9]*").splitlines() if re.fullmatch(r"v\d+\.\d+\.\d+", t)]
    if not tags:
        sys.exit(f"{repo}: no release tag (vX.Y.Z)")
    return max(tags, key=version_key)


def require_clean_main(repo: Path, fetch: bool) -> None:
    if git(repo, "branch", "--show-current") != "main":
        sys.exit(f"{repo.name}: checkout `main` first (currently on {git(repo, 'branch', '--show-current')!r})")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        sys.exit(f"{repo.name}: uncommitted changes to tracked files; commit or stash them first")
    if fetch:
        git(repo, "pull", "--ff-only", "origin", "main")


def export_module(repo: Path, tag: str, dest: Path) -> Path:
    """The module as of `tag` (tracked files only), without touching its working tree."""
    dest.mkdir(parents=True)
    archive = subprocess.run(["git", "-C", str(repo), "archive", tag], check=True, capture_output=True).stdout
    subprocess.run(["tar", "-x", "-C", str(dest)], input=archive, check=True)
    return dest


def py(*args, cwd=None, stdout=None):
    return run([sys.executable, *args], cwd=cwd, stdout=stdout)


# -- collection ---------------------------------------------------------------------------

def rebuild_collection(coll: Path, exported: dict[str, Path], tags: dict[str, str]) -> tuple[str, bool]:
    """Returns (version, changed)."""
    for sub in ("tsv", "eaf"):
        (coll / sub).mkdir(exist_ok=True)
    for module, src in exported.items():
        for sub, pattern in (("tsv", "*.tsv"), ("eaf", "*.eaf")):
            for f in (src / sub).glob(pattern):
                shutil.copy2(f, coll / sub / f.name)
    py(TOOLS / "tsv2formats.py", "-i", "tsv/", "--out_enriched", "linear-enriched",
       "--out_orthographic", "linear-orthographic", cwd=coll)
    py(TOOLS / "merge_metadata.py", "--modules", *[str(p) for p in exported.values()],
       "--output-dir", "metadata/", cwd=coll)
    py(TOOLS / "csvw" / "generate_csvw.py", ".", cwd=coll)

    cff = coll / "CITATION.cff"
    text = cff.read_text(encoding="utf-8")
    major, minor, patch = re.search(r"^version:\s*(\d+)\.(\d+)\.(\d+)", text, re.M).groups()
    if not git(coll, "status", "--porcelain", "--", "tsv", "eaf", "linear-enriched", "linear-orthographic",
               "metadata", "csv-metadata.json"):
        return f"{major}.{minor}.{patch}", False   # data already up to date: no new version
    version = f"{major}.{minor}.{int(patch) + 1}"
    text = re.sub(r"^version:.*$", f"version: {version}", text, flags=re.M)
    text = re.sub(r"^date-released:.*$", f"date-released: {datetime.date.today():%Y-%m-%d}", text, flags=re.M)
    cff.write_text(text, encoding="utf-8")

    git(coll, "add", "tsv/", "eaf/", "linear-enriched/", "linear-orthographic/", "metadata/",
        "csv-metadata.json", "CITATION.cff")
    trigger = ", ".join(f"{m}@{tags[m]}" for m in exported)
    git(coll, "commit", "-m", f"chore: rebuild collection v{version} (trigger: {trigger})")
    git(coll, "tag", "-a", f"v{version}", "-m", f"Release v{version}")
    return version, True


# -- NoSketch data ------------------------------------------------------------------------

def update_info(registry: Path, label: str) -> None:
    level = py(TOOLS / "registry_attrs.py", "level", registry).strip()
    text = registry.read_text(encoding="utf-8")
    text = re.sub(r"^INFO .*$", f'INFO       "{registry.name} (release {label}, annotation level {level})"', text, flags=re.M)
    text = re.sub(r"^INFOHREF .*$", f'INFOHREF   "{ANNOTATION_LEVELS_URL}"', text, flags=re.M)
    registry.write_text(text, encoding="utf-8")


def nosketch_module(data: Path, module: str, src: Path, tag: str) -> None:
    corpus = corpus_name(module)
    meta = data / "metadata" / corpus
    py(TOOLS / "translate_metadata.py", "--input-dir", src / "metadata", "--output-dir", meta,
       "--translations", data / "translations.tsv")
    (data / corpus / "vertical").mkdir(parents=True, exist_ok=True)
    with open(data / corpus / "vertical" / "source", "w", encoding="utf-8") as out:
        py(TOOLS / "tsv2vert.py", "--artifacts-base-url", ARTIFACTS_URL, "--artifacts-module", module,
           meta / "conversations.tsv", meta / "participants.tsv", *sorted((src / "tsv").glob("*.vert.tsv")),
           stdout=out)
    update_info(data / "registry" / corpus, tag)
    git(data, "add", f"metadata/{corpus}", f"{corpus}/vertical/source", f"registry/{corpus}")


def nosketch_aggregate(data: Path, coll: Path, version: str) -> None:
    meta = data / "metadata" / "KIParla"
    py(TOOLS / "translate_metadata.py", "--input-dir", coll / "metadata", "--output-dir", meta,
       "--translations", data / "translations.tsv")
    registries = [data / "registry" / corpus_name(m) for m in MODULES]
    common = [arg for r in registries for arg in ("--common-attrs-of", r)]
    (data / "KIParla" / "vertical").mkdir(parents=True, exist_ok=True)
    with open(data / "KIParla" / "vertical" / "source", "w", encoding="utf-8") as out:
        py(TOOLS / "tsv2vert.py", "--artifacts-base-url", ARTIFACTS_URL, "--artifacts-module", "KIParla",
           *common, meta / "conversations.tsv", meta / "participants.tsv",
           *sorted((coll / "tsv").glob("*.vert.tsv")), stdout=out)
    aggregate = data / "registry" / "KIParla"
    py(TOOLS / "registry_attrs.py", "prune", "--modules", *[str(r) for r in registries],
       "--registry", aggregate)
    update_info(aggregate, f"v{version}")
    git(data, "add", "metadata/KIParla", "KIParla/vertical/source", "registry/KIParla")


# -- main ---------------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=TOOLS.parent,
                    help="directory holding tools/, moduli/, KIParla-collection/, KIParla-NoSketch-Data/")
    ap.add_argument("--modules", nargs="+", default=MODULES, choices=MODULES,
                    help="modules whose release to pick up (default: all). The collection is always "
                         "rebuilt from all seven, so a module not listed is taken at its latest tag too.")
    ap.add_argument("--tag", action="append", default=[], metavar="MODULE=vX.Y.Z",
                    help="use this tag for a module instead of its latest")
    ap.add_argument("--no-fetch", action="store_true", help="do not fetch tags / pull before starting")
    ap.add_argument("--push", action="store_true",
                    help="push the two repositories, the collection tag, and create the collection release")
    args = ap.parse_args()

    root = args.root.resolve()
    coll, data = root / "KIParla-collection", root / "KIParla-NoSketch-Data"
    for repo in (coll, data):
        if not (repo / ".git").exists():
            sys.exit(f"{repo} is not a git repository")
        require_clean_main(repo, fetch=not args.no_fetch)

    wanted = {t.split("=", 1)[0]: t.split("=", 1)[1] for t in args.tag}
    tags: dict[str, str] = {}
    for module in MODULES:
        repo = root / "moduli" / module
        if not (repo / ".git").exists():
            sys.exit(f"{repo} is not a git repository")
        if not args.no_fetch:
            git(repo, "fetch", "--tags", "--quiet", "origin")
        tags[module] = wanted.get(module) or latest_tag(repo)
    print("Modules:", ", ".join(f"{m}@{t}" for m, t in tags.items()))

    with tempfile.TemporaryDirectory() as tmp:
        exported = {m: export_module(root / "moduli" / m, tags[m], Path(tmp) / m) for m in MODULES}

        version, collection_changed = rebuild_collection(coll, exported, tags)
        print(f"KIParla-collection: committed v{version}" if collection_changed
              else f"KIParla-collection: already up to date (v{version})")

        for module in args.modules:
            nosketch_module(data, module, exported[module], tags[module])
            print(f"KIParla-NoSketch-Data: {corpus_name(module)} regenerated")
        nosketch_aggregate(data, coll, version)
        print("KIParla-NoSketch-Data: KIParla aggregate regenerated")

    if subprocess.run(["git", "-C", str(data), "diff", "--cached", "--quiet"]).returncode != 0:
        label = ", ".join(f"{m}@{tags[m]}" for m in args.modules)
        git(data, "commit", "-m", f"Regenerate {label} and the KIParla aggregate @ v{version}")
        print("KIParla-NoSketch-Data: committed")
    else:
        print("KIParla-NoSketch-Data: nothing changed")

    if args.push:
        if collection_changed:
            git(coll, "push", "origin", "main", f"v{version}")
            run(["gh", "release", "create", f"v{version}", "-R", "KIParla/KIParla-collection", "--verify-tag",
                 "--title", f"v{version}", "--notes", "Rebuilt from " + ", ".join(f"{m}@{tags[m]}" for m in tags)])
        git(data, "push", "origin", "main")
        print("Pushed.")
    else:
        print("\nNothing pushed. Inspect, then:")
        if collection_changed:
            print(f"  git -C {coll} push origin main v{version}")
            print(f"  gh release create v{version} -R KIParla/KIParla-collection --verify-tag --title v{version}")
        print(f"  git -C {data} push origin main")

    print("\nStill to do by hand:")
    print("  - recompile the NoSketch Engine corpora on the server (see the NoSketch technical docs);")
    print("  - regenerate the HTML/PDF pages, for each module:")
    for module in args.modules:
        print(f"      python3 tools/generate_artifacts.py --module-root moduli/{module} "
              f"--artifacts-root KIParla-artifacts --release-label \"{module}@{tags[module]}\" "
              f"--release-url \"https://github.com/KIParla/{module}/releases/tag/{tags[module]}\"")


if __name__ == "__main__":
    main()
