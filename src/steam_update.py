"""SteamCMD / LGSM helpers for checking and updating the Palworld dedicated server."""

from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
from typing import Optional, Tuple

from src.settings import settings

PALWORLD_APP_ID = "2394010"


def resolve_steamcmd_path() -> Optional[str]:
    """Return a runnable SteamCMD path, or None if not found."""
    configured = getattr(settings, "steamcmdPath", None)
    candidates = []
    if configured:
        candidates.append(configured)
    candidates.extend(
        [
            "steamcmd",
            "/usr/games/steamcmd",
            "steamcmd.exe",
            r"C:\steamcmd\steamcmd.exe",
        ]
    )
    for candidate in candidates:
        found = shutil.which(candidate)
        if found:
            return found
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
        # Windows .exe may not report X_OK the same way
        if os.path.isfile(candidate):
            return candidate
    return None


def resolve_install_dir() -> Optional[str]:
    """Return the PalServer install directory used by SteamCMD."""
    configured = getattr(settings, "steamcmdInstallDir", None)
    if configured:
        return os.path.abspath(configured)

    exe_path = settings.palworldServerExePath
    if not exe_path:
        return None
    return os.path.dirname(os.path.abspath(exe_path))


def appmanifest_path(install_dir: str) -> str:
    return os.path.join(
        install_dir, "steamapps", f"appmanifest_{PALWORLD_APP_ID}.acf"
    )


def read_local_build_id(install_dir: str) -> Optional[str]:
    """Read the installed build ID from Steam's appmanifest file."""
    path = appmanifest_path(install_dir)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except OSError as e:
        logging.warning("Could not read appmanifest %s: %s", path, e)
        return None
    match = re.search(r'"[Bb]uild[Ii][Dd]"\s*"(\d+)"', text)
    return match.group(1) if match else None


def parse_public_build_id(app_info_text: str) -> Optional[str]:
    """Extract the public-branch buildid from SteamCMD app_info_print output."""
    if not app_info_text:
        return None
    # Prefer the first "public" block, then the buildid inside it.
    lower = app_info_text.lower()
    idx = lower.find('"public"')
    if idx == -1:
        return None
    window = app_info_text[idx : idx + 1200]
    match = re.search(r'"buildid"\s*"(\d+)"', window, re.IGNORECASE)
    return match.group(1) if match else None


def fetch_remote_build_id(
    steamcmd_path: str, timeout: int = 180
) -> Tuple[Optional[str], str]:
    """Ask SteamCMD for the current public build ID. Returns (build_id, error_or_empty)."""
    cmd = [
        steamcmd_path,
        "+login",
        "anonymous",
        "+app_info_update",
        "1",
        "+app_info_print",
        PALWORLD_APP_ID,
        "+quit",
    ]
    logging.info("Querying SteamCMD for remote build: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"SteamCMD timed out after {timeout}s while checking for updates"
    except OSError as e:
        return None, f"Failed to run SteamCMD: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    build_id = parse_public_build_id(output)
    if not build_id:
        tail = output[-1500:] if output else f"exit code {result.returncode}"
        return None, f"Could not read remote build ID from SteamCMD: {tail}"
    return build_id, ""


def check_steam_update(
    steamcmd_path: str, install_dir: str
) -> Tuple[Optional[bool], str]:
    """Compare local vs Steam public build IDs.

    Returns (update_available, message). update_available is None on error.
    """
    local = read_local_build_id(install_dir)
    if not local:
        return (
            None,
            f"Could not read local build ID from {appmanifest_path(install_dir)}. "
            "Is PalServer installed via SteamCMD in this directory?",
        )

    remote, err = fetch_remote_build_id(steamcmd_path)
    if not remote:
        return None, err or "Could not determine remote build ID"

    if local == remote:
        return False, f"Up to date (build {local})"
    return True, f"Update available (local {local} → Steam {remote})"


def check_lgsm_update(lgsm_script: str, timeout: int = 180) -> Tuple[Optional[bool], str]:
    """Run LGSM `check-update`. Returns (update_available, message)."""
    cmd = [lgsm_script, "check-update"]
    logging.info("Running LGSM check-update: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(lgsm_script) or ".",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return None, f"LGSM check-update timed out after {timeout}s"
    except OSError as e:
        return None, f"Failed to run LGSM check-update: {e}"

    output = ((result.stdout or "") + (result.stderr or "")).strip()
    lowered = output.lower()
    if "no update" in lowered or "already up to date" in lowered:
        return False, "Up to date"
    if "update available" in lowered or "update is available" in lowered:
        return True, "Update available"
    if result.returncode != 0:
        tail = output[-1500:] if output else f"exit code {result.returncode}"
        return None, f"LGSM check-update failed: {tail}"
    # Some LGSM versions only print a version line; treat unknown success as up to date.
    return False, output or "Up to date"


def run_steamcmd_update(
    steamcmd_path: str, install_dir: str, timeout: int = 3600
) -> Tuple[bool, str]:
    """Run SteamCMD app_update for Palworld. Returns (ok, message)."""
    cmd = [
        steamcmd_path,
        "+force_install_dir",
        install_dir,
        "+login",
        "anonymous",
        "+app_update",
        PALWORLD_APP_ID,
        "validate",
        "+quit",
    ]
    logging.info("Running SteamCMD update: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"SteamCMD timed out after {timeout}s"
    except OSError as e:
        return False, f"Failed to run SteamCMD: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    # SteamCMD often exits 0 even with warnings; look for hard failures.
    lowered = output.lower()
    if result.returncode != 0:
        tail = output[-1500:] if output else f"exit code {result.returncode}"
        return False, f"SteamCMD failed (exit {result.returncode}): {tail}"
    if "error!" in lowered and "success" not in lowered:
        tail = output[-1500:]
        return False, f"SteamCMD reported an error: {tail}"
    return True, "Palworld server updated successfully via SteamCMD"


def run_lgsm_update(lgsm_script: str, timeout: int = 3600) -> Tuple[bool, str]:
    """Run an LGSM instance script `update` command."""
    cmd = [lgsm_script, "update"]
    logging.info("Running LGSM update: %s", " ".join(cmd))
    try:
        result = subprocess.run(
            cmd,
            cwd=os.path.dirname(lgsm_script) or ".",
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"LGSM update timed out after {timeout}s"
    except OSError as e:
        return False, f"Failed to run LGSM update: {e}"

    output = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        tail = output[-1500:] if output else f"exit code {result.returncode}"
        return False, f"LGSM update failed (exit {result.returncode}): {tail}"
    return True, "Palworld server updated successfully via LGSM"
