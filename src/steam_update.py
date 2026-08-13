"""SteamCMD / LGSM helpers for updating the Palworld dedicated server."""

from __future__ import annotations

import logging
import os
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
