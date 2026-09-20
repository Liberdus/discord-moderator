"""Build the standalone JEV batch helper; no profile reads or network calls."""

import argparse
from pathlib import Path
import zipfile


def build(root, output):
    if output.is_symlink():
        raise ValueError("Output must not be a symlink")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((root / "liberdus_moderator").glob("*.py")):
            archive.write(path, path.relative_to(root).as_posix())
        archive.writestr("__main__.py", "from liberdus_moderator.jev_batch import main\nraise SystemExit(main())\n")
    output.chmod(0o644)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.output)
