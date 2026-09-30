"""Record, verify and report the Android delta applied to the in-tree Krux.

The in-tree `src/krux` is upstream Krux plus a set of Android modifications.
Merging a new upstream ref means re-applying that delta, and the failure mode
that matters is not a merge conflict -- git or patch says so loudly -- it is a
*silent* divergence, where the merge succeeds but a file ends up carrying neither
the new upstream code nor the intended Android change. That is not hypothetical:
`encryption.py` carries an older mnemonic-storage backend than main's, because
the Android change replaces the whole storage layer, and it quietly forgoes
main's `StorageCorruptedError` corruption tolerance. Nothing flagged it.

So this makes drift detectable instead of merely unlikely.

    tools/krux_delta.py record  [ref]   # snapshot the current delta as patches
    tools/krux_delta.py verify  [ref]   # assert src/krux == ref + those patches
    tools/krux_delta.py report         # what is modified, and by how much

The recorded patches live in `tools/krux-delta/`, one per modified file, plus a
MANIFEST listing each file and why it is modified.

`verify` is what to run after every upstream sync, in CI or before a release.
"""
import argparse
import glob
import os
import shutil
import re
import subprocess
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KRUX = os.path.join(REPO, "src", "krux")
DELTA_DIR = os.path.join(REPO, "tools", "krux-delta")
MANIFEST = os.path.join(DELTA_DIR, "MANIFEST")

# Files whose divergence from upstream is intentional and not worth a patch.
# VERSION is a release identity string, not code: the app's reads
# "26.08.0.unified-sighash.A1" where upstream reads "26.08.0", and it changes
# every time the app is released. Recording that as a delta would mean
# re-recording a patch on each version bump, and the gate would be asserting
# something nobody wants asserted.
#
# Excluded from BOTH directions on purpose: not checked against a patch, and not
# reported as unrecorded drift. A file listed here is genuinely unverified, so it
# is printed on every run rather than being a silent hole -- and if one ever grows
# real Android modifications, `record` will say so and the entry should go.
UNVERIFIED = {
    "metadata.py": "VERSION is a release string, not logic; re-recorded on every "
                   "version bump. Keep this empty of real modifications.",
}
KRUX_REPO = os.environ.get("KRUX_REPO", "/home/user/Develop/src/krux-sighash/krux")
DEFAULT_REF = "unified-sighash-noknots-min"


def git(repo, *args):
    r = subprocess.run(["git", "-C", repo] + list(args),
                       capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("git %s in %s failed:\n%s" % (" ".join(args), repo, r.stderr))
    return r.stdout


def upstream_path(rel):
    """The path of a src/krux file inside a plain checkout of `ref`."""
    return os.path.join(KRUX, rel)


def upstream_bytes(ref, rel):
    return git(KRUX_REPO, "show", "%s:src/krux/%s" % (ref, rel)).encode()


def local_bytes(rel):
    with open(os.path.join(KRUX, rel), "rb") as f:
        return f.read()


def list_krux_files(root):
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d != "__pycache__"]
        for name in sorted(filenames):
            if name.endswith(".py"):
                full = os.path.join(dirpath, name)
                out.append(os.path.relpath(full, root))
    return sorted(out)


def patch_path(rel):
    """Where a file's delta patch is stored.

    Mirrors the tree rather than flattening it into one filename: an earlier
    version joined paths with "__", which is lossy for names that already
    contain "__" -- `pages/__init__.py` round-tripped to `pages//init__.py` and
    was then reported as an unrecorded file.
    """
    return os.path.join(DELTA_DIR, rel + ".patch")


def read_manifest():
    if not os.path.exists(MANIFEST):
        return {}
    entries = {}
    for line in open(MANIFEST, encoding="utf8"):
        line = line.rstrip("\n")
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(\S+)\s*\|\s*(.*)$", line)
        if m:
            entries[m.group(1)] = m.group(2).strip()
    return entries


def cmd_record(ref):
    if os.path.isdir(DELTA_DIR):
        shutil.rmtree(DELTA_DIR)
    os.makedirs(DELTA_DIR, exist_ok=True)

    modified, identical, app_only = [], 0, []
    for rel in list_krux_files(KRUX):
        local = local_bytes(rel)
        try:
            up = upstream_bytes(ref, rel)
        except SystemExit:
            app_only.append(rel)
            continue
        if local == up:
            identical += 1
            continue
        modified.append(rel)
        with tempfile.NamedTemporaryFile("wb", delete=False) as a, \
             tempfile.NamedTemporaryFile("wb", delete=False) as b:
            a.write(up)
            b.write(local)
            a_path, b_path = a.name, b.name
        d = subprocess.run(["diff", "-u", "--label", "a/" + rel,
                            "--label", "b/" + rel, a_path, b_path],
                           capture_output=True, text=True)
        os.unlink(a_path)
        os.unlink(b_path)
        dest = patch_path(rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "w", encoding="utf8") as f:
            f.write(d.stdout)

    reasons = read_manifest()
    with open(MANIFEST, "w", encoding="utf8") as f:
        f.write("# Android modifications applied to the in-tree Krux.\n")
        f.write("#\n")
        f.write("# One patch per file, produced by `krux_delta.py record <ref>`.\n")
        f.write("# The reason column is documentation, not enforced.\n")
        f.write("# Format: <path under src/krux> | <why it is modified>\n")
        f.write("#\n")
        f.write("# Recorded against: %s\n\n" % ref)
        for rel in modified:
            f.write("%s | %s\n" % (rel, reasons.get(rel, "")))

    print("  %d file(s) differ from %s -> %d patch(es) in %s"
          % (len(modified), ref, len(modified), os.path.relpath(DELTA_DIR, REPO)))
    print("  %d identical, %d present only in the app" % (identical, len(app_only)))
    for rel in app_only:
        print("    app-only: %s" % rel)
    missing = [r for r in reasons if r not in modified]
    if missing:
        print("  note: %d manifest entr(ies) no longer differ from upstream:"
              % len(missing))
        for r in missing:
            print("    %s" % r)


def cmd_verify(ref):
    if not os.path.isdir(DELTA_DIR):
        raise SystemExit("no recorded delta; run `krux_delta.py record %s` first" % ref)

    problems = []
    all_rels = sorted(r[len(DELTA_DIR) + 1:-len(".patch")].replace(os.sep, "/")
                      for r in glob.glob(os.path.join(DELTA_DIR, "**", "*.patch"),
                                         recursive=True))
    # A stale patch for an excluded file is dropped rather than applied, so the
    # exclusion survives even if the patch file is left behind.
    rels = [r for r in all_rels if r not in UNVERIFIED]
    stale = [r for r in all_rels if r in UNVERIFIED]
    patched = set()

    for rel in rels:
        patched.add(rel)
        if not os.path.exists(os.path.join(KRUX, rel)):
            problems.append("patched file is gone from src/krux: %s" % rel)
            continue
        # Apply forward inside a scratch tree holding the upstream file at its
        # real relative path, so patch resolves the a/ b/ labels to that path
        # rather than to some temporary filename. The recorded patch is
        # diff(upstream, app), so applying it forward to upstream must
        # reproduce the app's file exactly.
        with tempfile.TemporaryDirectory() as td:
            work = os.path.join(td, rel)
            os.makedirs(os.path.dirname(work), exist_ok=True)
            with open(work, "wb") as f:
                f.write(upstream_bytes(ref, rel))
            r = subprocess.run(
                ["patch", "-p1", "--batch", "--forward", "-s", work],
                stdin=open(patch_path(rel), "rb"),
                capture_output=True, cwd=td)
            if r.returncode != 0:
                problems.append("delta no longer applies to %s (%s): %s"
                                % (ref, rel, r.stdout.decode(errors="replace").strip()[:120]))
                continue
            if open(work, "rb").read() != local_bytes(rel):
                problems.append("src/krux/%s has drifted from the recorded delta" % rel)

    # anything differing from upstream that is not in the delta is unrecorded drift
    for rel in list_krux_files(KRUX):
        if rel in patched or rel in UNVERIFIED:
            continue
        try:
            up = upstream_bytes(ref, rel)
        except SystemExit:
            continue  # app-only file, listed by `record`
        if local_bytes(rel) != up:
            problems.append("src/krux/%s differs from %s but has no recorded delta"
                            % (rel, ref))

    if stale:
        print("  note: ignoring %d stale patch(es) for excluded file(s): %s"
              % (len(stale), ", ".join(stale)))
    for rel, why in sorted(UNVERIFIED.items()):
        print("  note: %s is excluded from verification -- %s" % (rel, why))

    if problems:
        print("  FAIL: %d problem(s) against %s" % (len(problems), ref))
        for p in problems:
            print("    - %s" % p)
        return 1
    print("  OK: src/krux is %s plus the %d recorded Android modification(s)"
          % (ref, len(rels)))
    return 0


def cmd_report():
    reasons = read_manifest()
    if not reasons:
        raise SystemExit("no MANIFEST; run `krux_delta.py record` first")
    rows = []
    for rel, why in reasons.items():
        patch = patch_path(rel)
        added = removed = 0
        if os.path.exists(patch):
            for line in open(patch, encoding="utf8", errors="replace"):
                if line.startswith("+") and not line.startswith("+++"):
                    added += 1
                elif line.startswith("-") and not line.startswith("---"):
                    removed += 1
        rows.append((rel, added, removed, why))
    rows.sort(key=lambda r: -(r[1] + r[2]))
    print("  %-42s %5s %5s  %s" % ("file", "+", "-", "why"))
    print("  " + "-" * 96)
    for rel, a, r, why in rows:
        print("  %-42s %5d %5d  %s" % (rel, a, r, why[:38]))
    print()
    print("  %d modified file(s), %d insertions, %d deletions"
          % (len(rows), sum(r[1] for r in rows), sum(r[2] for r in rows)))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["record", "verify", "report"])
    ap.add_argument("ref", nargs="?", default=DEFAULT_REF)
    args = ap.parse_args()
    if args.command == "record":
        cmd_record(args.ref)
        return 0
    if args.command == "verify":
        return cmd_verify(args.ref)
    cmd_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())
