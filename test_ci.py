"""The CI configuration is what keeps the model key out of a pull request's hands.

`.github/workflows` is the folder that decides what runs with this repository's
secrets: a workflow chooses its triggers, the token it hands a job and which
actions that job runs. This repository's default token is `write`, so a job that
names no `permissions:` inherits write access to the repository rather than
proving it needs it.

Five rules hold the boundary up, and each is one careless edit away from being
untrue:

* a workflow that names a secret is not triggered by `pull_request` — GitHub
  resolves that workflow, and every local `uses:` inside it, from the pull
  request's *merge commit*, so the branch could edit the file that runs with the
  key;
* no workflow hands a runner the pull request's head — `zrb` loads `zrb_init.py`
  from the working directory, so a checkout of the head is enough to run the
  contributor's code with the key in the environment;
* every external action is pinned to a commit, because a tag is a moving pointer
  and `@v4` runs whatever it points at today;
* every runner names an image rather than `-latest`, which resolves to a
  different image without a commit changing;
* every workflow declares its own `permissions`, so the repository default is
  never what a job gets by accident.

Like `test_face.py`, this file is run as a script (`python test_ci.py`) and reads
as a checklist. The workflows are the tree's CI, not part of the package, so they
are read here as text rather than imported.
"""

import re
from pathlib import Path

WORKFLOWS = Path(__file__).parent / ".github" / "workflows"

# The trigger, at the indentation a trigger sits at and with nothing after the
# word but the colon -- which is what tells `pull_request:` from
# `pull_request_target:`.
_PULL_REQUEST = re.compile(r"^ {2}pull_request:\s*$", re.MULTILINE)
_SECRET = re.compile(r"\bsecrets\.[A-Za-z_]")

# Reading the head as an event field is fine (`head.repo.full_name` is the fork
# check). Naming its commit or its branch is what puts contributor code on the
# runner.
_HEAD = re.compile(
    r"github\.event\.pull_request\.(?:head\.(?:sha|ref)|merge_commit_sha)"
    r"|github\.head_ref"
)

_USES = re.compile(r"^\s*(?:-\s+)?uses:\s*(\S+)", re.MULTILINE)
_COMMIT = re.compile(r"[0-9a-f]{40}")

# A runner label only where a label is written: after `runs-on:` or after `os:`
# in a matrix, inline list or not. Prose in a comment is not a label, so a
# comment explaining `-latest` does not fail the rule that bans it.
_LABEL_SITE = re.compile(r"^\s*(?:-\s+)?(?:runs-on|os):\s*(.+)$", re.MULTILINE)
_WORD = re.compile(r"[\w.\-]+")

_PERMISSIONS = re.compile(r"^ {0,4}permissions:", re.MULTILINE)

TEXTS = {
    path.name: path.read_text(encoding="utf-8")
    for path in sorted(WORKFLOWS.glob("*.yml"))
}


def moving_label(text: str) -> str | None:
    """The first `-latest` runner label in `text`, if it names one."""
    for site in _LABEL_SITE.finditer(text):
        for word in _WORD.findall(site.group(1)):
            if word.endswith("-latest"):
                return word
    return None


assert TEXTS, "no workflow found under .github/workflows"

for _name, _text in TEXTS.items():
    assert not (_SECRET.search(_text) and _PULL_REQUEST.search(_text)), (
        _name,
        "names a secret and is triggered by pull_request. A pull request can edit "
        "the workflow it runs, so this must be pull_request_target.",
    )

    _head = _HEAD.search(_text)
    assert not _head, (
        _name,
        f"names the pull request's head as a checkout ref: {_head.group(0)}. "
        "Fetch the head as git objects and read it as a diff.",
    )

    for _uses in _USES.findall(_text):
        if _uses.startswith("./") or _uses.startswith("docker://"):
            continue
        assert _COMMIT.fullmatch(_uses.rpartition("@")[2]), (
            _name,
            f"runs an action that is not pinned to a commit: {_uses}. "
            "Pin the commit and keep the tag as a trailing comment.",
        )

    _label = moving_label(_text)
    assert not _label, (
        _name,
        f"runs on a moving label: {_label}. Name the image the label resolves "
        "to; a runner log's `Image:` line says which one ran.",
    )

    assert _PERMISSIONS.search(_text), (
        _name,
        "declares no permissions, so it inherits the repository default -- which "
        "is write here. Name what the job needs.",
    )

# Each rule fires on the thing it is for, and only on that: a guard that reports
# its own prose is worse than none, because it teaches people to edit the guard.
_PR_WITH_SECRET = (
    "on:\n  pull_request:\n    branches:\n      - main\n"
    "    env:\n      KEY: ${{ secrets.OPENAI_API_KEY }}\n"
)
assert _SECRET.search(_PR_WITH_SECRET) and _PULL_REQUEST.search(_PR_WITH_SECRET)
assert not _PULL_REQUEST.search("on:\n  pull_request_target:\n    branches:\n      - main\n")

assert not _HEAD.search(
    "if: github.event.pull_request.head.repo.full_name == github.repository"
)
assert _HEAD.search("ref: ${{ github.event.pull_request.head.sha }}")
assert _HEAD.search("ref: ${{ github.head_ref }}")

assert _COMMIT.fullmatch("11d5960a326750d5838078e36cf38b85af677262")
assert not _COMMIT.fullmatch("v4")

assert moving_label("    runs-on: ubuntu-latest") == "ubuntu-latest"
assert moving_label("        os: [ubuntu-26.04]\n          - os: macos-latest") == "macos-latest"
assert moving_label("    runs-on: ${{ matrix.os }}") is None
assert moving_label("  # a -latest label is a moving pointer") is None

assert _PERMISSIONS.search("jobs:\n  test:\n    permissions:\n      contents: read\n")
assert _PERMISSIONS.search("permissions:\n  contents: read\n")
assert not _PERMISSIONS.search("jobs:\n  test:\n    runs-on: ubuntu-26.04\n")

print("ok")
