# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The command a task agent on dsh adds dsh plugins with: ``neko-dsh-plugin``.

``dsh plugin --profile <name> <pnpm arguments>`` hands its arguments to a
``pnpm`` it looks for on the PATH, and most computers have none (nor the
Node.js the usual pnpm needs). A task agent could not use it as it is
either: dsh takes every ``DSH_`` variable out of the environment of the
commands it runs, so a ``dsh plugin`` run from a session would add to the
user's own ``~/.dsh``, not to the dsh of the data root.

So the host writes a small script, rewritten each time it starts dsh, into
a directory it puts in front of the PATH of the dsh process (and so of the
commands dsh runs). The script

* fetches pnpm the first time: the build that needs no Node.js
  (``DSH_PNPM_PACKAGES``), from the first registry of
  ``DSH_PNPM_REGISTRIES`` that answers, each file checked against its size
  and SHA-256 and deleted when it differs, and unpacked to
  ``<dsh root>/pnpm/<version>/``;
* runs ``dsh plugin --profile acp`` with that pnpm, with dsh's directories
  set to the data root's, with pnpm's store, cache and state below
  ``<dsh root>/pnpm/`` (nothing in the user's own directories), and with the
  registry that answered.

What the user is told before a plugin is added, and why, is in
``config/prompts/prompts_nekopower_dsh.py`` and in the permission question
(``nekopower.host.reports``): a plugin runs inside the dsh process, with
everything that process may do, and without a sandbox. A plugin is loaded
when a dsh process starts, so one added now is there from the next one on.

All of it is below the dsh root, which an uninstall removes
(``DshInstallation.uninstall``).
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

COMMAND = "neko-dsh-plugin"
BIN_DIR = "bin"
# Below ``bin``, not on the PATH: PowerShell would run a ``.ps1`` it finds
# there before the ``.cmd`` of the same name, under the execution policy the
# ``.cmd`` steps around (by default one that runs no script at all).
SCRIPT_DIR = "lib"
PNPM_DIR = "pnpm"
# Below ``PNPM_DIR``: the registry that last delivered (pnpm, or a plugin).
REGISTRY_FILE = "registry.txt"

# What pnpm (and dsh) do without fetching anything: no registry is asked first, and none is
# tried after another. Nor do they bring a plugin in.
LOCAL_COMMANDS = (
    "list", "ls", "remove", "rm", "uninstall", "un", "why", "allow-version", "revoke-version", "version-exemptions",
)

# Each call of the script in a command line, with the word after it. Any call
# whose first word is not one of ``LOCAL_COMMANDS`` may bring a plugin in
# (``add``, but also ``update``, ``install``, an option before ``add``), so the
# permission question about it says what a plugin may do. The frontend
# recognizes the same (``components/power/ask.ts``).
# The word is looked for on the same line only: a command on the next one is no argument.
_CALL = re.compile(rf"{COMMAND}(?:\.cmd)?['\"]?(?:[ \t]+['\"]?([^\s'\"]*))?", re.IGNORECASE)

_HEADER = (
    "Written by N.E.K.O each time it starts dsh; a change made here is lost.",
    "Adds, removes and lists the plugins of N.E.K.O's dsh: neko-dsh-plugin add <package>.",
    "pnpm, which dsh hands this to, is fetched the first time and checked against its SHA-256.",
)


def bin_dir(root: Path) -> Path:
    return root / BIN_DIR


def pnpm_root(root: Path) -> Path:
    return root / PNPM_DIR


def adds_plugin(command: str) -> bool:
    """Whether a command line may add a dsh plugin: it calls ``neko-dsh-plugin`` other than to look or remove."""
    return any((call.group(1) or "").lower() not in LOCAL_COMMANDS for call in _CALL.finditer(command or ""))


def written_for(directory: Path, executable: Path) -> bool:
    """Blocking: whether ``directory`` holds a ``neko-dsh-plugin`` that runs ``executable``."""
    try:
        texts = [
            path.read_text(encoding="utf-8-sig")
            for path in directory.rglob(f"{COMMAND}*")
            if path.is_file() and not path.name.endswith(".tmp")
        ]
    except (OSError, ValueError):
        return False
    return any(_ps(executable) in text or _sh(executable) in text for text in texts)


def _pnpm_environment(root: Path) -> dict[str, str]:
    base = pnpm_root(root)
    return {
        "pnpm_config_store_dir": str(base / "store"),
        "pnpm_config_cache_dir": str(base / "cache"),
        "pnpm_config_state_dir": str(base / "state"),
        "pnpm_config_update_notifier": "false",
    }


def _files(tag: str) -> list[dict[str, str | int]] | None:
    from config.nekopower_settings import DSH_PNPM_PACKAGES

    platform = DSH_PNPM_PACKAGES.get(tag)
    return [DSH_PNPM_PACKAGES["main"], platform] if platform is not None else None


def _download_mb(files: list[dict[str, str | int]] | None) -> int:
    return round(sum(int(item["size"]) for item in files or []) / 1_000_000)


def _ps(value: object) -> str:
    """A PowerShell single-quoted string literal."""
    return "'" + str(value).replace("'", "''") + "'"


def _sh(value: object) -> str:
    """A POSIX shell single-quoted string literal."""
    return "'" + str(value).replace("'", "'\\''") + "'"


def render_powershell(*, root: Path, executable: Path, leading: tuple[str, ...], profile: str,
                      environment: Mapping[str, str], tag: str) -> str:
    """Return the PowerShell script behind ``neko-dsh-plugin.cmd``."""
    from config.nekopower_settings import DSH_PNPM_REGISTRIES, DSH_PNPM_VERSION

    files = _files(tag)
    setting = {**environment, **_pnpm_environment(root)}
    lines = [*(f"# {line}" for line in _HEADER), ""]
    lines += [
        "$ErrorActionPreference = 'Stop'",
        "$ProgressPreference = 'SilentlyContinue'",
        "[Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor "
        "[Net.SecurityProtocolType]::Tls12",
        f"$Dsh = {_ps(executable)}",
        f"$Leading = @({', '.join(_ps(item) for item in leading)})",
        f"$PnpmDir = {_ps(pnpm_root(root) / DSH_PNPM_VERSION)}",
        f"$Remembered = {_ps(pnpm_root(root) / REGISTRY_FILE)}",
        f"$Registries = @({', '.join(_ps(item) for item in DSH_PNPM_REGISTRIES)})",
        "$Files = @(",
        ",\n".join(
            f"  @{{ Name = {_ps(item['name'])}; File = {_ps(item['file'])}; Size = {int(item['size'])}; "
            f"Sha256 = {_ps(item['sha256'])} }}"
            for item in files or []
        ),
        ")",
        "",
        "function Say($text) { [Console]::Error.WriteLine(\"neko-dsh-plugin: $text\") }",
        "",
        "function Find-Registry {",
        "  foreach ($registry in $Registries) {",
        "    try { Invoke-WebRequest -Uri \"$registry/-/ping\" -UseBasicParsing -TimeoutSec 15 | Out-Null; "
        "return $registry } catch { }",
        "  }",
        "  return $null",
        "}",
        "",
        "# Fetch one file from the first registry that delivers it whole, and return that registry;",
        "# never keep a file that differs.",
        "function Get-Checked($first, $item, $target) {",
        "  foreach ($registry in @($first) + $Registries) {",
        "    try {",
        "      Invoke-WebRequest -Uri \"$registry/$($item.Name)/-/$($item.File)\" -OutFile $target "
        "-UseBasicParsing -TimeoutSec 600",
        "      $hash = (Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant()",
        "      if ((Get-Item -LiteralPath $target).Length -eq $item.Size -and $hash -eq $item.Sha256) {",
        "        return $registry",
        "      }",
        "      Say \"$($item.File) from $registry is not the file expected; not used\"",
        "    } catch { Say \"$($item.File) could not be fetched from $registry\" }",
        "    Remove-Item -LiteralPath $target -Force -ErrorAction SilentlyContinue",
        "  }",
        "  throw \"pnpm could not be fetched\"",
        "}",
        "",
        # pnpm keeps the registry its packages came from and holds a later command that names
        # another to it: a local one is given the registry that last delivered, without asking it.
        "function Read-Registry {",
        "  try {",
        "    $kept = (Get-Content -LiteralPath $Remembered -Raw -ErrorAction Stop).Trim()",
        "    if ($Registries -contains $kept) { return $kept }",
        "  } catch { }",
        "  return $null",
        "}",
        "",
        "function Save-Registry($registry) {",
        # Written aside and renamed: a run reading it meanwhile never finds it half written.
        "  try {",
        "    [IO.File]::WriteAllText(\"$Remembered.$PID\", $registry)",
        "    Move-Item -LiteralPath \"$Remembered.$PID\" -Destination $Remembered -Force",
        "  } catch { Remove-Item -LiteralPath \"$Remembered.$PID\" -Force -ErrorAction SilentlyContinue }",
        "}",
        "",
        "function Expand-Package($archive, $target) {",
        "  & (Join-Path $env:SystemRoot 'System32\\tar.exe') -xzf $archive -C $target --strip-components 1",
        "  if ($LASTEXITCODE -ne 0) { throw \"$archive could not be unpacked\" }",
        "}",
        "",
        "# Returns the registry the larger file came from: it is the one that delivers here.",
        "function Install-Pnpm($registry) {",
        # What a run that was killed outright left half made, its process being gone.
        "  foreach ($left in @(Get-ChildItem -Path \"$PnpmDir.partial-*\" -Directory -ErrorAction SilentlyContinue)) {",
        "    $owner = $left.Name.Substring($left.Name.LastIndexOf('-') + 1)",
        "    if ($owner -match '^[0-9]+$' -and -not (Get-Process -Id ([int]$owner) -ErrorAction SilentlyContinue)) {",
        "      Remove-Item -LiteralPath $left.FullName -Recurse -Force -ErrorAction SilentlyContinue",
        "    }",
        "  }",
        "  $staging = \"$PnpmDir.partial-$PID\"",
        "  Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue",
        "  try {",
        "    New-Item -ItemType Directory -Force -Path \"$staging\\program\", \"$staging\\platform\" | Out-Null",
        "    $null = Get-Checked $registry $Files[0] \"$staging\\main.tgz\"",
        "    $source = Get-Checked $registry $Files[1] \"$staging\\platform.tgz\"",
        "    Expand-Package \"$staging\\main.tgz\" \"$staging\\program\"",
        "    Expand-Package \"$staging\\platform.tgz\" \"$staging\\platform\"",
        "    Copy-Item -LiteralPath \"$staging\\platform\\pnpm.exe\" -Destination \"$staging\\program\\pnpm.exe\"",
        "    # Another run may have put it there meanwhile: then that one is kept.",
        "    if (-not (Test-Path -LiteralPath $PnpmDir)) {",
        "      Move-Item -LiteralPath \"$staging\\program\" -Destination $PnpmDir",
        "    }",
        "  } finally {",
        "    Remove-Item -LiteralPath $staging -Recurse -Force -ErrorAction SilentlyContinue",
        "  }",
        "  return $source",
        "}",
        "",
        "try {",
    ]
    if files is None:
        lines += [
            "  Say 'pnpm is not built for this computer, so dsh plugins cannot be added here.'",
            "  exit 1",
        ]
    else:
        lines += [
            f"  $Local = @({', '.join(_ps(item) for item in LOCAL_COMMANDS)})",
            "  $offline = $args.Count -gt 0 -and $Local -contains $args[0]",
            "  $ready = Test-Path -LiteralPath (Join-Path $PnpmDir 'pnpm.exe')",
            "  if ($ready -and $offline) { $registry = Read-Registry } else { $registry = Find-Registry }",
            "  if (-not $ready) {",
            "    if (-not $registry) { Say 'no package registry answered, so pnpm could not be fetched.'; exit 1 }",
            f"    Say 'fetching pnpm {DSH_PNPM_VERSION} (about {_download_mb(files)} MB), this once.'",
            "    $registry = Install-Pnpm $registry",
            "    Save-Registry $registry",
            "  }",
            "  $env:PATH = \"$PnpmDir;$env:PATH\"",
            *(f"  $env:{name} = {_ps(value)}" for name, value in setting.items()),
            # The registry that answered first, then those after it in the list, for a command that
            # fetches: one that answers can still fail to deliver.
            "  $tries = @($null)",
            "  if ($registry) {",
            "    $tries = @($registry)",
            "    if (-not $offline) { $tries += @($Registries | Select-Object -Skip ([Array]::IndexOf($Registries, $registry) + 1)) }",
            "  }",
        ]
    lines += [
        "} catch {",
        "  Say $_.Exception.Message",
        "  exit 1",
        "}",
        # A dsh that is not there sets no exit code, which would read as success.
        "if (-not (Test-Path -LiteralPath $Dsh)) { Say \"dsh is not at $Dsh; it is written there again when dsh starts.\"; exit 1 }",
        "$ErrorActionPreference = 'Continue'",
        "$code = 1",
        "for ($i = 0; $i -lt $tries.Count; $i++) {",
        "  if ($tries[$i]) { $env:pnpm_config_registry = \"$($tries[$i])/\" }",
        # The global one, which running dsh sets, however this script comes to be run.
        "  $global:LASTEXITCODE = 1",
        f"  & $Dsh @Leading plugin --profile {_ps(profile)} @args",
        "  $code = if ($null -eq $LASTEXITCODE) { 1 } else { $LASTEXITCODE }",
        "  if ($code -eq 0) {",
        "    if ($tries[$i] -and -not $offline) { Save-Registry $tries[$i] }",
        "    break",
        "  }",
        "  if ($i + 1 -lt $tries.Count) { Say \"trying again with $($tries[$i + 1])\" }",
        "}",
        "exit $code",
        "",
    ]
    return "\n".join(lines)


def render_cmd() -> str:
    """Return ``neko-dsh-plugin.cmd``: it runs the PowerShell script beside it with its arguments."""
    return (
        "@echo off\r\n"
        f"rem {_HEADER[0]}\r\n"
        f'powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File "%~dp0{SCRIPT_DIR}\\{COMMAND}.ps1" %*\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )


def render_sh(*, root: Path, executable: Path, leading: tuple[str, ...], profile: str,
              environment: Mapping[str, str], tag: str) -> str:
    """Return ``neko-dsh-plugin`` for macOS and Linux."""
    from config.nekopower_settings import DSH_PNPM_REGISTRIES, DSH_PNPM_VERSION

    files = _files(tag)
    setting = {**environment, **_pnpm_environment(root)}
    lines = ["#!/bin/sh", *(f"# {line}" for line in _HEADER), "set -u"]
    lines += [
        f"DSH={_sh(executable)}",
        f"PNPM_DIR={_sh(pnpm_root(root) / DSH_PNPM_VERSION)}",
        f"REMEMBERED={_sh(pnpm_root(root) / REGISTRY_FILE)}",
        f"REGISTRIES={_sh(' '.join(DSH_PNPM_REGISTRIES))}",
        "",
        "say() { printf 'neko-dsh-plugin: %s\\n' \"$*\" >&2; }",
    ]
    if files is None:
        lines += [
            "say 'pnpm is not built for this computer, so dsh plugins cannot be added here.'",
            "exit 1",
            "",
        ]
        return "\n".join(lines)
    main, platform = files
    lines += [
        "",
        # Run as a child, waited for: a signal to this script is acted on at once (``stop`` ends
        # the download), not when the download is over.
        "fetch() {",
        "  if command -v curl >/dev/null 2>&1; then curl -fsSL --connect-timeout 15 --max-time 600 -o \"$2\" \"$1\" &",
        "  elif command -v wget >/dev/null 2>&1; then wget -q --tries=2 --timeout=60 -O \"$2\" \"$1\" &",
        "  else return 1; fi",
        "  child=$!",
        "  wait \"$child\"; fetched=$?",
        "  child=''",
        "  return $fetched",
        "}",
        "",
        "# Read from standard input: given a name with a backslash, sha256sum puts one before the digest.",
        "digest() {",
        "  if command -v sha256sum >/dev/null 2>&1; then sha256sum < \"$1\" | cut -d ' ' -f 1",
        "  else shasum -a 256 < \"$1\" | cut -d ' ' -f 1; fi",
        "}",
        "",
        # sh has no local variables: the loops' are named apart from ``registry`` below.
        "probe() {",
        "  if command -v curl >/dev/null 2>&1; then curl -fsS --connect-timeout 10 --max-time 15 -o /dev/null \"$1\"",
        "  elif command -v wget >/dev/null 2>&1; then wget -q --tries=1 --timeout=15 -O /dev/null \"$1\"",
        "  else return 1; fi",
        "}",
        "",
        "find_registry() {",
        "  for candidate in $REGISTRIES; do",
        "    if probe \"$candidate/-/ping\" 2>/dev/null; then echo \"$candidate\"; return 0; fi",
        "  done",
        "  return 1",
        "}",
        "",
        # pnpm keeps the registry its packages came from and holds a later command that names
        # another to it: a local one is given the registry that last delivered, without asking it.
        "read_registry() {",
        "  [ -f \"$REMEMBERED\" ] || return 1",
        "  kept=$(cat \"$REMEMBERED\")",
        "  for candidate in $REGISTRIES; do",
        "    if [ \"$candidate\" = \"$kept\" ]; then echo \"$kept\"; return 0; fi",
        "  done",
        "  return 1",
        "}",
        "",
        # Written aside and renamed: a run reading it meanwhile never finds it half written.
        "save_registry() {",
        "  if printf '%s' \"$1\" > \"$REMEMBERED.$$\" 2>/dev/null; then mv -f \"$REMEMBERED.$$\" \"$REMEMBERED\" 2>/dev/null; fi",
        "  rm -f \"$REMEMBERED.$$\" 2>/dev/null || true",
        "}",
        "",
        "# Fetch one file from the first registry that delivers it whole, which is left in",
        "# ``delivered``; never keep a file that differs.",
        "get_checked() {  # first name file size sha256 target",
        "  for source in \"$1\" $REGISTRIES; do",
        "    if fetch \"$source/$2/-/$3\" \"$6\" && [ \"$(wc -c < \"$6\" | tr -d ' ')\" = \"$4\" ] "
        "&& [ \"$(digest \"$6\")\" = \"$5\" ]; then delivered=$source; return 0; fi",
        "    say \"$3 could not be fetched whole from $source\"",
        "    rm -f \"$6\"",
        "  done",
        "  return 1",
        "}",
        "",
        "install_pnpm() {",
        # What a run that was killed outright left half made, its process being gone.
        "  for left in \"$PNPM_DIR\".partial-*; do",
        "    [ -d \"$left\" ] || continue",
        "    if ! kill -0 \"${left##*.partial-}\" 2>/dev/null; then rm -rf \"$left\"; fi",
        "  done",
        "  staging=\"$PNPM_DIR.partial-$$\"",
        "  rm -rf \"$staging\"",
        "  if mkdir -p \"$staging/program\" \"$staging/platform\" \\",
        f"    && get_checked \"$1\" {_sh(main['name'])} {_sh(main['file'])} {int(main['size'])} "
        f"{_sh(main['sha256'])} \"$staging/main.tgz\" \\",
        f"    && get_checked \"$1\" {_sh(platform['name'])} {_sh(platform['file'])} {int(platform['size'])} "
        f"{_sh(platform['sha256'])} \"$staging/platform.tgz\" \\",
        # No name reaches tar: GNU tar takes one with a colon for a remote host's file.
        "    && (cd \"$staging/program\" && tar -xzf - --strip-components 1) < \"$staging/main.tgz\" \\",
        "    && (cd \"$staging/platform\" && tar -xzf - --strip-components 1) < \"$staging/platform.tgz\" \\",
        "    && cp \"$staging/platform/pnpm\" \"$staging/program/pnpm\" && chmod +x \"$staging/program/pnpm\"; then",
        "    # Another run may have put it there meanwhile: then that one is kept.",
        "    [ -e \"$PNPM_DIR\" ] || mv \"$staging/program\" \"$PNPM_DIR\"",
        "  fi",
        "  rm -rf \"$staging\"",
        "  staging=''",
        "  [ -f \"$PNPM_DIR/pnpm\" ]",
        "}",
        "",
        # Set before anything is fetched: a signal that ends this script also removes what a
        # download left half done, and hands itself on to dsh while dsh runs, so that neither dsh
        # nor its pnpm is left running when the command is given up.
        "child=''",
        "staging=''",
        "stop() {",
        # Waited for, so that it is gone before what it was writing is removed and this exits.
        "  if [ -n \"$child\" ]; then kill \"$child\" 2>/dev/null; wait \"$child\" 2>/dev/null; fi",
        "  if [ -n \"$staging\" ]; then rm -rf \"$staging\"; fi",
        "  exit \"$1\"",
        "}",
        "trap 'stop 143' TERM",
        "trap 'stop 130' INT",
        "trap 'stop 129' HUP",
        "",
        "first=$(printf '%s' \"${1:-}\" | tr '[:upper:]' '[:lower:]')",
        f"case \"$first\" in {'|'.join(LOCAL_COMMANDS)}) offline=1 ;; *) offline=0 ;; esac",
        "registry=''",
        "if [ -f \"$PNPM_DIR/pnpm\" ] && [ \"$offline\" = 1 ]; then registry=$(read_registry) || registry=''",
        "else registry=$(find_registry) || registry=''; fi",
        "if [ ! -f \"$PNPM_DIR/pnpm\" ]; then",
        "  if [ -z \"$registry\" ]; then say 'no package registry answered, so pnpm could not be fetched.'; exit 1; fi",
        f"  say 'fetching pnpm {DSH_PNPM_VERSION} (about {_download_mb(files)} MB), this once.'",
        "  delivered=$registry",
        "  install_pnpm \"$registry\" || { say 'pnpm could not be fetched'; exit 1; }",
        "  registry=$delivered",
        "  save_registry \"$registry\"",
        "fi",
        "PATH=\"$PNPM_DIR:$PATH\"; export PATH",
        *(f"{name}={_sh(value)}; export {name}" for name, value in setting.items()),
        # The registry that answered first, then those after it in the list, for a command that
        # fetches: one that answers can still fail to deliver.
        "tries=$registry",
        "if [ -n \"$registry\" ] && [ \"$offline\" = 0 ]; then",
        "  after=0",
        "  for candidate in $REGISTRIES; do",
        "    if [ \"$after\" = 1 ]; then tries=\"$tries $candidate\"; fi",
        "    if [ \"$candidate\" = \"$registry\" ]; then after=1; fi",
        "  done",
        "fi",
        # dsh runs as a child, waited for (see ``stop``). A command run in the background gets no
        # standard input of its own: it is handed this script's, kept as descriptor 3.
        "exec 3<&0",
        "run() { "
        + " ".join(["\"$DSH\"", *(_sh(item) for item in leading), "plugin --profile", _sh(profile), "\"$@\""])
        + " <&3 & child=$!; wait \"$child\"; }",
        "if [ -z \"$tries\" ]; then run \"$@\"; exit $?; fi",
        "code=1",
        "for source in $tries; do",
        "  pnpm_config_registry=\"$source/\"; export pnpm_config_registry",
        "  run \"$@\"; code=$?",
        "  if [ \"$code\" = 0 ]; then",
        "    if [ \"$offline\" = 0 ]; then save_registry \"$source\"; fi",
        "    break",
        "  fi",
        "  say \"$source did not do it (exit $code)\"",
        "done",
        "exit $code",
        "",
    ]
    return "\n".join(lines)


def write_command(root: Path, *, executable: Path, leading: tuple[str, ...], home: Path, tag: str,
                  windows: bool | None = None) -> Path:
    """Blocking: write ``neko-dsh-plugin`` for this dsh into ``<root>/bin`` and return that directory.

    A file is written only when its text changed, so starting dsh again
    leaves it alone.
    """
    from nekopower.dsh.profile import PROFILE, dsh_directories

    windows = os.name == "nt" if windows is None else windows
    directory = bin_dir(root)
    directory.mkdir(parents=True, exist_ok=True)
    arguments = {
        "root": root,
        "executable": executable,
        "leading": leading,
        "profile": PROFILE,
        "environment": dsh_directories(home),
        "tag": tag,
    }
    if windows:
        # Windows PowerShell reads a script without a byte order mark in the
        # system's code page, which would garble a path in another script.
        scripts = directory / SCRIPT_DIR
        scripts.mkdir(exist_ok=True)
        _write(scripts / f"{COMMAND}.ps1", render_powershell(**arguments), encoding="utf-8-sig")
        _write(directory / f"{COMMAND}.cmd", render_cmd(), encoding="ascii")
    else:
        target = directory / COMMAND
        _write(target, render_sh(**arguments), encoding="utf-8")
        target.chmod(0o755)
    return directory


def _write(path: Path, text: str, *, encoding: str) -> None:
    data = text.encode(encoding)
    try:
        if path.read_bytes() == data:
            return
    except OSError:
        pass
    temporary = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_bytes(data)
        os.replace(temporary, path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise
