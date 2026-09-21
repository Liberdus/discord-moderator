"""Build a standalone synthetic screening evaluator; no profile or network access."""
import argparse
from pathlib import Path
import zipfile


def build(root, output):
    if output.exists() or output.is_symlink():
        raise ValueError("Choose a new output path; existing release artifacts are preserved")
    with zipfile.ZipFile(output, "x", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / "liberdus_moderator").glob("*.py")):
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr("__main__.py", "from liberdus_moderator.screening_eval import main\nraise SystemExit(main())\n")
    output.chmod(0o644)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output)
