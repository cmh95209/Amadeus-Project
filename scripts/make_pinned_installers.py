#!/usr/bin/env python3
"""Build the PINNED installers of Amadeus releases (v3.1.1, 2026-11).

The normal installers (scripts/install_*.{ps1,sh}, "Install Amadeus.bat")
always install the latest version of the main branch. A pinned installer
installs exactly ONE release. It is that release's own installer (the
installer and the app changed together, so an old app gets the installer it
was tested with), changed in only these ways:

  * it downloads the release's tag instead of main;
  * its title says "<tag>-pinned";
  * a safety check runs first: when the install folder already holds a
    DIFFERENT version, it stops and changes nothing (an older version could
    misread newer memory files). It explains how to update instead or, on
    Windows, how to install the old version NEXT TO the current one
    (-InstallDir + -EnvSuffix: own folder, own Python environments);
  * re-running it on its own version keeps that version (a repair run).

The normal installers move a pinned install to the latest version.

Usage (from the repository; needs the release tags locally):
    python scripts/make_pinned_installers.py --out <folder> [v3.0.4 v3.1.0 ...]
Without tags it builds every release from v3.0 on. Per tag it writes
<out>/<tag>/: install_windows-<tag>.ps1, Install-Amadeus-<tag>.bat (downloads
that .ps1 from the GitHub release and runs it), install_linux-<tag>.sh (when
the release had one), install_macos-<tag>.sh. Attach them to the release.
"""
from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RELEASES_URL = "https://github.com/cmh95209/Amadeus-Project/releases/download"
FIRST_PINNED = (3, 0, 0)          # older releases are not pinned (see docstring)
TAG_RX = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")

PLATFORMS = {
    # name: (script in the release, output file pattern)
    "windows": ("scripts/install_windows.ps1", "install_windows-{tag}.ps1"),
    "linux": ("scripts/install_linux.sh", "install_linux-{tag}.sh"),
    "macos": ("scripts/install_macos.sh", "install_macos-{tag}.sh"),
}


class PatchError(RuntimeError):
    pass


def git(*args: str) -> str:
    return subprocess.run(["git", "-C", str(REPO), *args], check=True,
                          capture_output=True, text=True, encoding="utf-8").stdout


def tag_key(tag: str):
    m = TAG_RX.match(tag)
    return tuple(int(x or 0) for x in m.groups()) if m else None


def release_tags() -> list[str]:
    tags = [t for t in git("tag", "--list", "v*").split() if tag_key(t)]
    return sorted((t for t in tags if tag_key(t) >= FIRST_PINNED), key=tag_key)


def show(tag: str, path: str) -> str | None:
    r = subprocess.run(["git", "-C", str(REPO), "show", f"{tag}:{path}"],
                       capture_output=True)
    return r.stdout.decode("utf-8") if r.returncode == 0 else None


def replace(text: str, old: str, new: str, *, count: int | None = 1, what: str = "") -> str:
    n = text.count(old)
    if (count is None and n == 0) or (count is not None and n != count):
        raise PatchError(f"{what or old!r}: expected {count or '1+'} match(es), found {n}")
    return text.replace(old, new)


def sub(text: str, rx: str, new: str, *, what: str, flags=re.M) -> str:
    out, n = re.subn(rx, lambda _m: new, text, count=1, flags=flags)
    if n != 1:
        raise PatchError(f"{what}: pattern not found")
    return out


def ps_quote(s: str) -> str:
    return s.replace('"', '`"')


# ---------------------------------------------------------------- Windows --
WIN_GUARD = r'''
# ---------- PINNED INSTALLER (made by scripts/make_pinned_installers.py) ----------
# This copy installs exactly Amadeus __TAG__ (not the latest version). It never
# puts that version over a DIFFERENT one: an older version could misread newer
# memory files. Re-running it on its own version only repairs the install.
$PinnedVersion = "__TAG__"
$PinnedCommit  = "__SHA__"
function Stop-Pinned($lines){
    Write-Host ""
    foreach ($l in $lines) { Write-Host ("  " + $l) -ForegroundColor Yellow }
    Write-Host ""
    try { [void](Read-Host "Press Enter to close this window") } catch {}
    exit 1
}
# "none" (no Amadeus there), "same" (this version), or what the folder holds.
function Get-PinnedState($projDir){
    $headFile = Join-Path $projDir ".git\HEAD"
    if (Test-Path -LiteralPath $headFile) {
        $h = ([IO.File]::ReadAllText($headFile)).Trim()
        if ($h -eq $PinnedCommit) { return "same" }
        if ($h -like "ref: refs/heads/*") { return ("a copy that follows the '" + $h.Substring(16) + "' branch (normally the latest version)") }
        return ("a different version (commit " + $h.Substring(0, [Math]::Min(7, $h.Length)) + ")")
    }
    if (Test-Path -LiteralPath (Join-Path $projDir "backend\main.py")) { return "a copy of unknown version" }
    return "none"
}
$pinnedSuffix = "v" + ($PinnedVersion -replace '[^0-9]', '')
$pinnedSideBySide = '"Install-Amadeus-' + $PinnedVersion + '.bat" -InstallDir "%USERPROFILE%\Amadeus-' + $PinnedVersion + '" -EnvSuffix ' + $pinnedSuffix
$pinnedProj  = Join-Path $InstallDir "Amadeus-Project"
$pinnedState = Get-PinnedState $pinnedProj
if ($pinnedState -ne "none" -and $pinnedState -ne "same") {
    Stop-Pinned @(
        ("This installer installs exactly Amadeus " + $PinnedVersion + "."),
        ("But " + $pinnedProj + " already holds " + $pinnedState + "."),
        "Putting another version over it could damage her memories, so nothing was changed.",
        "",
        "To keep that Amadeus and update it: double-click 'Update Amadeus.bat' in that",
        "folder (older copies: 'Install Amadeus.bat').",
        "",
        ("To install " + $PinnedVersion + " NEXT TO it (own folder, own chats and memories), open a"),
        "Command Prompt in the folder that holds this installer and run:",
        ("  " + $pinnedSideBySide),
        "Run only one Amadeus at a time."
    )
}
# Another folder still shares the Python environments of the usual install
# unless -EnvSuffix gives it its own (installing would change the ones it uses).
$pinnedDefault = Join-Path (Join-Path $env:USERPROFILE "Amadeus") "Amadeus-Project"
if ($pinnedState -eq "none" -and -not $EnvSuffix -and $pinnedDefault -ne $pinnedProj) {
    $pinnedOther = Get-PinnedState $pinnedDefault
    $pinnedOtherSfx = ""
    $pinnedSfxFile = Join-Path $pinnedDefault ".runtime\env_suffix.txt"
    if (Test-Path -LiteralPath $pinnedSfxFile) { $pinnedOtherSfx = ([IO.File]::ReadAllText($pinnedSfxFile)).Trim() }
    if ($pinnedOther -ne "none" -and $pinnedOther -ne "same" -and -not $pinnedOtherSfx) {
        Stop-Pinned @(
            ("This installer installs exactly Amadeus " + $PinnedVersion + " into " + $InstallDir + "."),
            ("But " + $pinnedDefault + " already holds " + $pinnedOther + ","),
            "and without -EnvSuffix both would share one set of Python environments -",
            "installing would change the ones your current Amadeus uses. Nothing was changed.",
            "",
            "Add -EnvSuffix to give this copy its own, for example:",
            ("  " + $pinnedSideBySide)
        )
    }
}
if ($pinnedState -eq "same") { Write-Host ("  Amadeus " + $PinnedVersion + " is already installed here - checking the rest of the install.") -ForegroundColor Green }
# ---------- end of PINNED INSTALLER check ----------
'''

WIN_STEP3_OLD = 'if (Test-Complete $proj @("backend\\main.py","start_windows.bat")) {'
WIN_STEP3_NEW = (
    'if ((Test-Complete $proj @("backend\\main.py","start_windows.bat")) -and '
    '((& git -C $proj rev-parse HEAD 2>$null | Out-String).Trim() -eq $PinnedCommit)) {\n'
    '        Log ("  Amadeus " + $PinnedVersion + " is already here - keeping it (pinned installer).") "Green"\n'
    '    } elseif (Test-Complete $proj @("backend\\main.py","start_windows.bat")) {'
)

WIN_BAT = r'''@echo off
rem ==========================================================================
rem  Install Amadeus __TAG__ - a PINNED installer: it installs exactly Amadeus
rem  __TAG__, not the latest version. (For the latest version use
rem  "Install Amadeus.bat" from the README.) No administrator needed.
rem
rem  It downloads this release's installer (install_windows-__TAG__.ps1) from
rem  GitHub into this same folder and runs it in this window. It never puts
rem  __TAG__ over a different version that is already installed - it tells
rem  you how to install it next to that one instead.
rem
rem  Windows may first say "Windows protected your PC" (this file is not
rem  signed by a company): click "More info", then "Run anyway".
rem
rem  Advanced (from a command prompt): installer options go after the name,
rem    "Install-Amadeus-__TAG__.bat" -InstallDir "D:\Amadeus-__TAG__" -EnvSuffix __SFX__
rem ==========================================================================
setlocal
title Amadeus __TAG__ installer
set "AMADEUS_PS1=%~dp0install_windows-__TAG__.ps1"
set "AMADEUS_URL=__URL__"

echo Downloading the Amadeus __TAG__ installer...
powershell -NoProfile -ExecutionPolicy RemoteSigned -Command "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12; try { Invoke-WebRequest -UseBasicParsing -Uri $env:AMADEUS_URL -OutFile $env:AMADEUS_PS1; exit 0 } catch { Write-Host ('  ' + $_.Exception.Message); exit 1 }"
if errorlevel 1 (
    echo.
    echo Could not download the installer. Check your internet connection,
    echo then double-click this file again. If it still fails, move this file
    echo to a normal folder first ^(for example Downloads^) - not inside a zip.
    echo.
    pause
    exit /b 1
)

powershell -NoProfile -ExecutionPolicy RemoteSigned -File "%AMADEUS_PS1%" %*
'''


def pin_windows(text: str, tag: str, sha: str) -> str:
    text = replace(text, '[string]$Branch = "main",', f'[string]$Branch = "{tag}",', what="Branch default")
    text = sub(text, r'^\$InstallerVersion = "[^"]*"', f'$InstallerVersion = "{tag}-pinned"', what="InstallerVersion")
    text = replace(text, '$Branch -ne "main"', f'$Branch -ne "{tag}"', count=None, what="test-option check")
    text = sub(text, r"(ONE-SHOT INSTALLER FOR WINDOWS\s+)v[\d.]+", r"\g<1>" + f"{tag}-pinned (installs exactly Amadeus {tag})",
               what="title comment")
    anchor = "$InstallDir = $InstallDir.TrimEnd('\\')\n"
    guard = WIN_GUARD.replace("__TAG__", ps_quote(tag)).replace("__SHA__", sha)
    text = replace(text, anchor, anchor + guard, what="InstallDir anchor")
    text = replace(text, WIN_STEP3_OLD, WIN_STEP3_NEW, what="step 3 update check")
    return text


def windows_bat(tag: str) -> str:
    url = f"{RELEASES_URL}/{tag}/install_windows-{tag}.ps1"
    sfx = "v" + re.sub(r"[^0-9]", "", tag)
    return WIN_BAT.replace("__TAG__", tag).replace("__URL__", url).replace("__SFX__", sfx)


# ---------------------------------------------------------- Linux / macOS --
SH_GUARD = r'''
# ---------- PINNED INSTALLER (made by scripts/make_pinned_installers.py) ----------
# This copy installs exactly Amadeus __TAG__ (not the latest version). It never
# puts that version over a DIFFERENT one: an older version could misread newer
# memory files. Re-running it on its own version only repairs the install.
PINNED_VERSION="__TAG__"
PINNED_COMMIT="__SHA__"
pinned_state() {   # prints: none | same | what the folder holds
    _pin_p="$1"
    if [ -f "$_pin_p/.git/HEAD" ]; then
        _pin_h="$(tr -d '\r\n' < "$_pin_p/.git/HEAD")"
        if [ "$_pin_h" = "$PINNED_COMMIT" ]; then
            echo same
        elif [ "${_pin_h#ref: refs/heads/}" != "$_pin_h" ]; then
            echo "a copy that follows the '${_pin_h#ref: refs/heads/}' branch (normally the latest version)"
        else
            echo "a different version (commit $(printf '%s' "$_pin_h" | cut -c1-7))"
        fi
    elif [ -f "$_pin_p/backend/main.py" ]; then
        echo "a copy of unknown version"
    else
        echo none
    fi
}
pinned_stop() {
    printf '\n'
    for _pin_l in "$@"; do printf '  %s\n' "$_pin_l"; done
    printf '\n'
    exit 1
}
_pin_state="$(pinned_state "$PROJ")"
case "$_pin_state" in
    none|same) ;;
    *) pinned_stop "This installer installs exactly Amadeus $PINNED_VERSION." \
           "But $PROJ already holds $_pin_state." \
           "Putting another version over it could damage her memories, so nothing was changed." \
           "" \
           "To keep that Amadeus and update it: run the normal installer line from the README." \
           "To install $PINNED_VERSION instead: rename that folder first (for example to" \
           "Amadeus-Project-old - your chats and her memories stay inside it), then run this" \
           "again. Both copies share one set of Python environments, so the renamed copy works" \
           "again only after you rename it back and run the normal installer." ;;
esac
# Another folder still shares the Python environments of the usual install.
_pin_default="$HOME/Amadeus/Amadeus-Project"
if [ "$_pin_state" = none ] && [ "$_pin_default" != "$PROJ" ]; then
    _pin_other="$(pinned_state "$_pin_default")"
    case "$_pin_other" in
        none|same) ;;
        *) pinned_stop "This installer installs exactly Amadeus $PINNED_VERSION into $PROJ." \
               "But $_pin_default already holds $_pin_other," \
               "and both would share one set of Python environments - installing would change" \
               "the ones your current Amadeus uses. Nothing was changed." \
               "" \
               "To install $PINNED_VERSION anyway: rename $_pin_default first" \
               "(for example to Amadeus-Project-old), then run this again." ;;
    esac
fi
if [ "$_pin_state" = same ]; then
    printf '  Amadeus %s is already installed here - checking the rest of the install.\n' "$PINNED_VERSION"
fi
# ---------- end of PINNED INSTALLER check ----------
'''

SH_STEP3_OLD = 'if [ -d "$PROJ/.git" ]; then\n    log "  Found an existing Amadeus at $PROJ - updating it..."'
SH_STEP3_NEW = (
    'if [ -d "$PROJ/.git" ] && [ "$(git -C "$PROJ" rev-parse HEAD 2>/dev/null)" = "$PINNED_COMMIT" ]; then\n'
    '    log "  Amadeus $PINNED_VERSION is already here - keeping it (pinned installer)."\n'
    'elif [ -d "$PROJ/.git" ]; then\n'
    '    log "  Found an existing Amadeus at $PROJ - updating it..."'
)


def pin_sh(text: str, tag: str, sha: str, title: str) -> str:
    text = sub(text, r'^BRANCH="main"$', f'BRANCH="{tag}"', what="BRANCH default")
    text = sub(text, r'^INSTALLER_VERSION="[^"]*"$', f'INSTALLER_VERSION="{tag}-pinned"', what="INSTALLER_VERSION")
    text = replace(text, '[ "$BRANCH" != "main" ]', f'[ "$BRANCH" != "{tag}" ]', count=None, what="test-option check")
    text = sub(text, rf"(ONE-SHOT INSTALLER FOR {title}\s+)v[\d.]+", r"\g<1>" + f"{tag}-pinned (installs exactly Amadeus {tag})",
               what="title comment")
    anchor_m = re.search(r'^PROJ="\$INSTALL_DIR/Amadeus-Project"\n', text, flags=re.M)
    if not anchor_m:
        raise PatchError("PROJ anchor: pattern not found")
    guard = SH_GUARD.replace("__TAG__", tag).replace("__SHA__", sha)
    text = text[:anchor_m.end()] + guard + text[anchor_m.end():]
    text = replace(text, SH_STEP3_OLD, SH_STEP3_NEW, what="step 3 update check")
    return text


# ------------------------------------------------------------------ main --
def build(tag: str, out: pathlib.Path) -> list[pathlib.Path]:
    if not tag_key(tag):
        raise SystemExit(f"Not a release tag: {tag}")
    sha = git("rev-parse", f"{tag}^{{commit}}").strip()
    dest = out / tag
    dest.mkdir(parents=True, exist_ok=True)
    written = []
    for name, (src, pattern) in PLATFORMS.items():
        text = show(tag, src)
        if text is None:
            continue                      # e.g. v3.0 had no Linux installer
        text = text.replace("\r\n", "\n")
        try:
            if name == "windows":
                pinned = pin_windows(text, tag, sha)
            else:
                pinned = pin_sh(text, tag, sha, "LINUX" if name == "linux" else "MAC OS")
        except PatchError as e:
            raise SystemExit(f"{tag} {src}: {e}")
        path = dest / pattern.format(tag=tag)
        newline = "\r\n" if name == "windows" else "\n"
        path.write_bytes(pinned.replace("\n", newline).encode("utf-8"))
        written.append(path)
        if name == "windows":
            bat = dest / f"Install-Amadeus-{tag}.bat"
            bat.write_bytes(windows_bat(tag).replace("\n", "\r\n").encode("utf-8"))
            written.append(bat)
    return written


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True, type=pathlib.Path, help="output folder (one sub-folder per tag)")
    ap.add_argument("tags", nargs="*", help="release tags (default: every release from v3.0 on)")
    args = ap.parse_args(argv)
    tags = args.tags or release_tags()
    if not tags:
        print("No release tags found (run `git fetch --tags` first).", file=sys.stderr)
        return 1
    for tag in tags:
        for path in build(tag, args.out):
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
