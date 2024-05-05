from pathlib import Path
import platform
from tempfile import NamedTemporaryFile
import typing as T
import re
import shutil
import sys
import textwrap

import nox
from nox import parametrize
from nox_poetry import Session, session

nox.options.error_on_external_run = True
nox.options.reuse_existing_virtualenvs = True
nox.options.sessions = ["lint", "type_check", "test"]


def get_project_version() -> str:
    for line in open("pyproject.toml"):
        # Expected line:
        #   version = "0.6.1"
        m = re.search(
            r"""
                ^
                \s* version \s* = \s*
                "
                ( (\d|\.)+ )
                "
                $
            """,
            line,
            re.VERBOSE,
        )
        if m:
            return T.cast(str, m.group(1))
    raise Exception("could not find `romt` version in `pyproject.toml`")


def get_target_os() -> str:
    if sys.platform.startswith("linux"):
        target_os = "linux"
    elif sys.platform.startswith("darwin"):
        target_os = "darwin"
    elif sys.platform.startswith("win"):
        target_os = "windows"
    else:
        target_os = "unknown"
    return target_os


def get_target_arch() -> str:
    arch = platform.machine()
    # On Windows, get "AMD64" instead of "x86_64".
    if arch == "AMD64":
        arch = "x86_64"
    return arch


def rmtree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)


@session(python=["3.8", "3.9", "3.10", "3.11", "3.12"])
def test(s: Session) -> None:
    s.install(".", "pytest", "pytest-cov")
    s.run(
        "python",
        "-m",
        "pytest",
        "--cov=romt",
        "--cov-report=html",
        "--cov-report=term",
        "tests",
        *s.posargs,
    )


# For some sessions, set `venv_backend="none"` to simply execute scripts within
# the existing Poetry environment. This requires that `nox` is run within
# `poetry shell` or using `poetry run nox ...`.
@session(venv_backend="none")
def fmt(s: Session) -> None:
    s.run("ruff", "check", ".", "--select", "I", "--fix")
    s.run("ruff", "format", ".")


@session(venv_backend="none")
@parametrize(
    "command",
    [
        ["ruff", "check", "."],
        ["ruff", "format", "--check", "."],
    ],
)
def lint(s: Session, command: T.List[str]) -> None:
    s.run(*command)


@session(venv_backend="none")
def lint_fix(s: Session) -> None:
    s.run("ruff", "check", ".", "--fix")


@session(venv_backend="none")
def type_check(s: Session) -> None:
    s.run("mypy", "src", "tests", "noxfile.py")


# Note: This `reuse_venv` does not yet have effect due to:
#   https://github.com/wntrblm/nox/issues/488
@session(reuse_venv=False)
def licenses(s: Session) -> None:
    # Generate a unique temporary file name. Poetry cannot write to the temp
    # file directly on Windows, so only use the name and allow Poetry to
    # re-create it.
    with NamedTemporaryFile() as t:
        requirements_file = Path(t.name)

    # Install dependencies without installing the package itself:
    #   https://github.com/cjolowicz/nox-poetry/issues/680
    s.run_always(
        "poetry",
        "export",
        "--without-hashes",
        f"--output={requirements_file}",
        external=True,
    )
    s.install("pip-licenses", "-r", str(requirements_file))
    s.run("pip-licenses", *s.posargs)
    requirements_file.unlink()


@session(venv_backend="none")
def build(s: Session) -> None:
    version = get_project_version()
    target_os = get_target_os()
    target_arch = get_target_arch()
    target_platform = f"{target_arch}-{target_os}"
    if target_os == "windows":
        suffix = ".exe"
    else:
        suffix = ""
    dist_path = Path("dist") / target_platform
    work_path = Path("build") / target_platform
    github_path = Path("dist") / "github"
    dist_exe_path = dist_path / ("romt" + suffix)
    github_exe_path = github_path / f"romt-{version}-{target_platform}{suffix}"

    rmtree(dist_path)
    rmtree(work_path)
    github_path.mkdir(parents=True, exist_ok=True)
    github_exe_path.unlink(missing_ok=True)

    s.run("poetry", "install")
    args = ["pyinstaller"]
    args.append("--onefile")
    args.extend(["--name", "romt"])
    args.extend(["--distpath", str(dist_path)])
    args.extend(["--specpath", str(work_path)])
    args.extend(["--workpath", str(work_path)])
    args.append("--add-data=../../README.rst:romt")
    args.extend(["--log-level", "WARN"])
    args.append("romt-wrapper.py")
    s.run(*args)
    s.log(f"copy {dist_exe_path} -> {github_exe_path}")
    shutil.copy(dist_exe_path, github_exe_path)


@session(venv_backend="none")
def release(s: Session) -> None:
    version = get_project_version()
    tar_path = Path("dist") / f"romt-{version}.tar.gz"
    whl_path = Path("dist") / f"romt-{version}-py3-none-any.whl"
    rmtree(Path("dist"))
    rmtree(Path("build"))
    s.log("NOTE: safe to perform Windows steps now...")
    build(s)
    s.run("poetry", "export", "-o", "requirements.txt")
    s.run("poetry", "build")
    s.run("twine", "check", str(tar_path), str(whl_path))
    print(
        textwrap.dedent(
            f"""
        ** Remaining manual steps:

        On Windows machine:
          poetry run nox -s build

        Tag and push:
          git tag -am 'Release v{version}.' v{version}
          git push; git push --tags

        Upload to PyPI:
          twine upload {tar_path} {whl_path}

        Create Github release for {version} from tree:
          dist/github/
        """
        )
    )
