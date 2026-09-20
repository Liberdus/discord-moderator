"""Build a disabled-installation zipapp; never reads credentials or connects Discord."""

import argparse
from pathlib import Path
import zipfile


def build(root, policy, target):
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted((root / "liberdus_moderator").glob("*.py")):
            bundle.write(path, str(path.relative_to(root)))
            bundle.write(path, "plugin/" + str(path.relative_to(root)))
        for name in ("__init__.py", "plugin.yaml"):
            bundle.write(root / "hermes_plugin" / name, "plugin/" + name)
        bundle.write(policy, "pilot.toml")
        bundle.writestr("__main__.py", "from liberdus_moderator.install_pilot import main\nraise SystemExit(main())\n")
    target.chmod(0o644)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--jev-output", type=Path, help="Optional owner-run JEV setup zipapp")
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[1], args.config, args.output)

    if args.jev_output:
        root = Path(__file__).resolve().parents[1]
        with zipfile.ZipFile(args.jev_output, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted((root / "liberdus_moderator").glob("*.py")):
                bundle.write(path, str(path.relative_to(root)))
            bundle.writestr("__main__.py", "from liberdus_moderator.configure_jev import main\nraise SystemExit(main())\n")
        args.jev_output.chmod(0o644)
