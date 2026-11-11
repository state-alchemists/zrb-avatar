"""What the command line does with an argument it has not got.

Run with `python test_cli.py`, like the other tests here: this repository's tests are
scripts whose last word is `ok`, not pytest modules.

`start` is swapped for a recorder in every check. The claim is not only that the right text
came out -- it is that nothing was started, because starting is what an unknown option used
to do: `zrb-avatar --version` answered by restarting the face.
"""

import contextlib
import io
import sys
from unittest import mock

from zrb_avatar import __main__ as cli


def run(argv, start=cli.start):
    """Run the command line as *argv*, collecting its output and exit code."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with mock.patch.object(cli, "start", start):
        with mock.patch.object(sys, "argv", ["zrb-avatar", *argv]):
            try:
                with contextlib.redirect_stdout(out):
                    with contextlib.redirect_stderr(err):
                        cli.main()
            except SystemExit as leaving:
                code = leaving.code if isinstance(leaving.code, int) else 1
    return out.getvalue(), err.getvalue(), code


def check_version_prints_a_version_and_starts_nothing() -> None:
    started = []
    said, _, code = run(["--version"], lambda args: started.append(args))
    assert started == [], "asking for the version started a server"
    assert code == 0, code
    assert said.strip(), "no version printed"
    assert said.strip()[0].isdigit(), said
    # The metadata is what `pip` and the wheel agree on, so the flag must not keep its own
    # copy of the number.
    import importlib.metadata

    try:
        installed = importlib.metadata.version("zrb-avatar")
    except importlib.metadata.PackageNotFoundError:
        installed = "0.0.0"
    assert said.strip() == installed, f"said {said.strip()!r}, metadata says {installed!r}"


def check_an_unknown_option_is_refused_and_starts_nothing() -> None:
    started = []
    _, refused, code = run(["--nonsense"], lambda args: started.append(args))
    assert code == 2, f"exit code {code!r}, wanted 2"
    assert started == [], "an unknown option started a server"
    assert "unknown option '--nonsense'" in refused, refused


def check_a_model_name_still_starts_the_face() -> None:
    # The fallback is `zrb-avatar <model>`, so a bare name has to keep reaching `start`.
    started = []
    run(["tara-navy-dress"], lambda args: started.append(args))
    assert started == [["tara-navy-dress"]], started


def check_help_prints_the_usage_and_starts_nothing() -> None:
    started = []
    said, _, _ = run(["--help"], lambda args: started.append(args))
    assert started == [], "asking for help started a server"
    assert "zrb-avatar status" in said, said
    assert "--version" in said, said


for name, check in sorted(globals().items()):
    if name.startswith("check_"):
        check()
print("ok")
