#!/usr/bin/env python3
"""Owner-run, credential-free inspection of the installed Hermes adapter interfaces.

Reads source/version metadata, then imports interfaces with a temporary empty
Hermes home. Does not read the real profile, load plugins, connect Discord, or
change installed packages or services.
"""

import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import tempfile


SOURCE_CONTRACTS = {
    "hermes_cli/plugins.py": {"PluginContext": ["register_platform"]},
    "gateway/platforms/base.py": {"BasePlatformAdapter": [
        "connect", "disconnect", "send", "get_chat_info", "_acquire_platform_lock",
        "_release_platform_lock", "_mark_connected", "_mark_disconnected", "_set_fatal_error",
    ]},
    "gateway/platforms/_shared.py": {None: ["get_scoped_secret"]},
    "gateway/config.py": {"PlatformConfig": ["from_dict"]},
}


def source_contracts(root):
    result = {}
    for relative, contracts in SOURCE_CONTRACTS.items():
        try:
            tree = ast.parse((root / relative).read_text())
            for owner, names in contracts.items():
                nodes = tree.body if owner is None else next(
                    node.body for node in tree.body if isinstance(node, ast.ClassDef) and node.name == owner
                )
                methods = {node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
                for name in names:
                    result[f"{owner + '.' if owner else ''}{name}"] = name in methods
        except (OSError, SyntaxError, StopIteration, UnicodeError):
            result[relative] = False
    return result


IMPORT_PROBE = r'''
import importlib.metadata
import inspect
import json
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, sys.argv[1])

# An import probe has no reason to use the network or launch another process.
def prohibit_io(event, args):
    if event.startswith("socket.") or event in ("subprocess.Popen", "os.system"):
        raise RuntimeError("network_or_process_attempt_during_import_probe")
sys.addaudithook(prohibit_io)

report = {"python_version": sys.version.split()[0], "packages": {}, "interfaces": {}}
for package in ("discord.py", "aiohttp", "PyYAML"):
    try:
        report["packages"][package] = importlib.metadata.version(package)
    except importlib.metadata.PackageNotFoundError:
        report["packages"][package] = None
try:
    import discord
    report["interfaces"]["discord_client"] = hasattr(discord, "Client")
    report["interfaces"]["message_content_intent"] = hasattr(discord.Intents.none(), "message_content")
    report["interfaces"]["mention_suppression"] = discord.AllowedMentions.none().to_dict().get("parse") == []
    report["interfaces"]["raw_edits"] = hasattr(discord, "RawMessageUpdateEvent")
except Exception as error:
    report["discord_import_error_type"] = type(error).__name__
try:
    from gateway.config import PlatformConfig
    from gateway.platform_registry import PlatformEntry
    from gateway.platforms.base import BasePlatformAdapter, SendResult
    from gateway.platforms._shared import get_scoped_secret
    report["interfaces"]["base_adapter"] = True
    report["interfaces"]["reconnect_parameter"] = "is_reconnect" in inspect.signature(BasePlatformAdapter.connect).parameters
    report["interfaces"]["profile_secret_reader"] = callable(get_scoped_secret)
    report["interfaces"]["platform_entry_explicit_gate"] = "is_connected" in inspect.signature(PlatformEntry).parameters
    report["interfaces"]["extra_configuration"] = PlatformConfig.from_dict({"enabled": False, "moderation_config": "probe.toml"}).extra.get("moderation_config") == "probe.toml"
except Exception as error:
    report["hermes_import_error_type"] = type(error).__name__
print("LIBERDUS_PROBE_RESULT=" + json.dumps(report, sort_keys=True))
'''


def git_metadata(root):
    def run(*args):
        try:
            return subprocess.run(["git", "-C", str(root), *args], capture_output=True,
                                  text=True, timeout=15, check=False)
        except (OSError, subprocess.TimeoutExpired):
            return None
    commit = run("rev-parse", "HEAD")
    changed = run("diff", "HEAD", "--quiet", "--", *SOURCE_CONTRACTS)
    sha = commit.stdout.strip() if commit is not None and commit.returncode == 0 else ""
    return {
        "commit": sha if len(sha) == 40 and all(c in "0123456789abcdef" for c in sha) else None,
        "reviewed_interface_files_modified": (bool(changed.returncode) if changed is not None and changed.returncode in (0, 1) else None),
    }


def inspect_runtime(root, python=None):
    root = root.expanduser().resolve()
    report = {"install_directory": str(root), "source": git_metadata(root),
              "source_interfaces": source_contracts(root)}
    candidates = [root / ".venv/bin/python", root / "venv/bin/python"]
    executable = Path(python).expanduser() if python else next((p for p in candidates if p.is_file()), None)
    if executable is None:
        report["runtime_probe"] = "No installation virtualenv found; pass --python with the Hermes interpreter path."
        return report
    report["python_executable"] = str(executable)
    # HOME and HERMES_HOME point to scratch space; no inherited profile/provider credentials.
    with tempfile.TemporaryDirectory(prefix="liberdus-runtime-probe-") as directory:
        environment = {"HOME": directory, "HERMES_HOME": directory,
                       "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8",
                       "PYTHONDONTWRITEBYTECODE": "1"}
        try:
            process = subprocess.run([str(executable), "-I", "-B", "-c", IMPORT_PROBE, str(root)],
                                     env=environment, cwd=directory, capture_output=True,
                                     text=True, timeout=45, check=False)
            matches = [line.removeprefix("LIBERDUS_PROBE_RESULT=") for line in process.stdout.splitlines()
                       if line.startswith("LIBERDUS_PROBE_RESULT=")]
            if process.returncode != 0 or len(matches) != 1:
                report["runtime_probe"] = "Import probe did not finish successfully; raw logs omitted."
            else:
                report["runtime_probe"] = json.loads(matches[0])
        except (OSError, subprocess.TimeoutExpired, ValueError):
            report["runtime_probe"] = "Could not complete the isolated import probe."
    report["changes"] = "No real profile access, package changes, gateway connection, or service restart."
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--install-dir", type=Path, default=Path.home() / ".hermes/hermes-agent")
    parser.add_argument("--python", help="Interpreter used by the existing Hermes installation")
    args = parser.parse_args()
    print(json.dumps(inspect_runtime(args.install_dir, args.python), indent=2, ensure_ascii=True))


if __name__ == "__main__":
    main()
