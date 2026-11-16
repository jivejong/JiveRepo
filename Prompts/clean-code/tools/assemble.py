"""Rebuild the assembled prompts from tools/*.template.md and check every kernel copy.

Usage: python -I tools/assemble.py <pipeline_dir>

1. Replaces each {{kernel:id@n}} in every tools/NAME.template.md with that block from
   kernel.md and writes NAME.md in the pipeline folder (04-build, 05a-trim-review, 05b-trim-execute).
2. Checks every kernel="id@n" tag in every prompt: the version must exist
   in kernel.md, and a whole-block copy must match the kernel text exactly.
3. Checks the log_standard block still contains engineering-log-standard.md's core.
4. Copies the interview spec schema from 02a-requirements-interview.md into README.md between its markers.
Exits 1 and lists each problem if any check fails.
"""
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
INTERVIEW = "02a-requirements-interview.md"
BLOCK = r'<(\w+)\b[^>]*\bkernel="%s">.*?</\1>'


def read(path):
    return path.read_bytes().decode("utf-8")


def write(path, text):
    path.write_bytes(text.encode("utf-8"))  # bytes, so Windows does not turn LF into CRLF


kernel = read(root / "kernel.md")


def block(kid):
    m = re.search(BLOCK % re.escape(kid), kernel, re.S)
    if not m:
        raise SystemExit("kernel block not found: " + kid)
    return m.group(0)


built = []
for tpl in sorted((root / "tools").glob("*.template.md")):
    out = tpl.name.replace(".template.md", ".md")
    write(root / out, re.sub(r"\{\{kernel:([^}]+)\}\}", lambda m: block(m.group(1)), read(tpl)))
    built.append(out)
if not built:
    raise SystemExit("no templates found in %s" % (root / "tools"))

schema = re.search(r"<spec_schema>\n(.*?)\n</spec_schema>", read(root / INTERVIEW), re.S).group(1)
start, end = "<!-- spec-schema:start -->", "<!-- spec-schema:end -->"
head, rest = read(root / "README.md").split(start)
_, tail = rest.split(end)
write(root / "README.md", head + start + "\n\n```xml\n" + schema + "\n```\n\n" + end + tail)

kernel_ids = set(re.findall(r'kernel="(\w+@\d+)"', kernel))
problems = []
for path in sorted(root.glob("*.md")):
    if path.name in ("kernel.md", "engineering-log-standard.md", "README.md"):
        continue
    text = read(path)
    for m in re.finditer(r'<(\w+)\b[^>]*\bkernel="(\w+@\d+)"', text):
        tag, kid = m.groups()
        if kid not in kernel_ids:
            problems.append("%s: %s is not in kernel.md (stale or misspelled)" % (path.name, kid))
            continue
        want = re.search(BLOCK % re.escape(kid), kernel, re.S)
        if want.group(1) != tag:
            continue  # a one-rule reference such as vague_words: version check only
        got = re.search(BLOCK % re.escape(kid), text, re.S)
        if kid.startswith("tiers@") and got and "<tier " not in got.group(0):
            # Early stages embed the summary lines only: "T1 Personal tool: summary".
            lines = [l for l in got.group(0).splitlines()[1:-1] if l.strip()]
            wanted = ["%s %s: %s" % t for t in re.findall(
                r'<tier id="(\w+)" name="([^"]+)">\s*<summary>(.*?)</summary>', want.group(0), re.S)]
            if lines != wanted:
                problems.append("%s: tier summaries differ from kernel.md %s" % (path.name, kid))
            continue
        if got is None or got.group(0) != want.group(0):
            problems.append("%s: copy of %s differs from kernel.md" % (path.name, kid))

std = read(root / "engineering-log-standard.md")
core = std[std.index("<header_schema>"): std.index("</rules>") + len("</rules>")]
log_block = re.search(BLOCK % r"log_standard@\d+", kernel, re.S)
if log_block is None or core not in log_block.group(0):
    problems.append("kernel.md: log_standard differs from engineering-log-standard.md")

if problems:
    print("\n".join(problems))
    sys.exit(1)
print("ok: rebuilt %s; %d kernel blocks, all copies current" % (", ".join(built), len(kernel_ids)))
