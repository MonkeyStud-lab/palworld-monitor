# Copyright (c) 2024 Nomomo
# Copyright (c) 2026 Kevin Perez - Modified work
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.

import logging
import time
import threading
from src.settings import settings
from src.player_manager import PlayerManager
from src.banlist_manager import BanlistManager
from src.events import bus, Event
import os
import platform


class PalWorldController:
    def __init__(
        self, client, process_manager=None, player_manager=None, banlist_manager=None
    ):
        self.client = client
        self.player_manager = (
            player_manager if player_manager is not None else PlayerManager()
        )
        self.banlist_manager = (
            banlist_manager if banlist_manager is not None else BanlistManager()
        )

        if process_manager is not None:
            self.process_manager = process_manager
        else:
            self.process_manager = self._create_process_manager()

        self.current_server_info = {"running": False, "playerCount": 0, "players": []}
        self.is_palworld_server_starting = False
        self.server_starting_cooldown = 5
        self.last_server_started_time = 0
        self.server_stopping_cooldown = 5
        self.last_server_stopped_time = 0

        self._auto_mode_cancelled = False
        self.update_thread = None
        self.update_thread_stop_event = threading.Event()
        self.auto_stop_delay_thread = None

        self._detection_thread = None
        self._detection_stop_event = threading.Event()

        self._setup_subscriptions()
        self._detect_existing_server_process()

        # If no server was found, keep looking every 5 seconds so a
        # process that starts later is still picked up.
        if not self.process_manager.launched_pid:
            self._start_detection_loop()

    def _create_process_manager(self):
        detected_os = platform.system()
        if detected_os.lower() == "linux":
            if getattr(settings, "useLGSM", False):
                from src.process_manager import LGSMProcessManager

                return LGSMProcessManager()
            from src.process_manager import LinuxProcessManager

            return LinuxProcessManager()
        else:
            from src.process_manager import WindowsProcessManager

            return WindowsProcessManager()

    def _setup_subscriptions(self):
        bus.subscribe(Event.SERVER_STARTED, self._on_server_started)
        bus.subscribe(Event.SERVER_STOPPED, self._on_server_stopped)
        bus.subscribe(Event.SERVER_STATUS, self._on_server_status)

    def _on_server_started(self, data):
        self.last_server_started_time = time.time()
        self.is_palworld_server_starting = False
        logging.debug(
            f"Controller: Server started event received (PID: {data.get('pid')})"
        )
        self.start_server_info_update_thread()

    def _on_server_stopped(self, data):
        self.last_server_stopped_time = time.time()
        logging.info("Controller: Server stopped event received")
        self._cancel_auto_stop_delay()
        self.current_server_info["running"] = False
        self.current_server_info["playerCount"] = 0
        self.current_server_info["players"] = []
        self.current_server_info.pop("players_unknown", None)
        self.stop_server_info_update_thread()
        self._start_detection_loop()

    def _on_server_status(self, data):
        self.current_server_info["running"] = data.get("running", False)
        self.current_server_info["playerCount"] = data.get("playerCount", 0)
        self.current_server_info["players"] = data.get("players", [])

        # Never auto-stop when the process is already down, or when we could
        # not reach the player API (connection refused / auth errors). Treating
        # those as "0 players" is what caused the infinite stop loop.
        if not data.get("running", False):
            self._cancel_auto_stop_delay()
            return
        if data.get("players_unknown"):
            return
        if settings.autoStop and data.get("playerCount", 0) == 0:
            self._handle_auto_stop_condition()
        else:
            self._cancel_auto_stop_delay()

    def is_palworld_process_running(self):
        return self.process_manager.is_process_running()

    def start_server(self):
        logging.info("Palworld server start command received")
        current_time = time.time()
        if self._should_block_start(current_time):
            return False
        self.is_palworld_server_starting = True
        try:
            bus.publish(
                Event.CMD_START_SERVER,
                {
                    "exe_path": settings.palworldServerExePath,
                    "exe_args": settings.palworldExeArguments,
                },
            )
            # SERVER_STARTED fires synchronously during the publish above and
            # resets is_palworld_server_starting to False on a successful launch.
            return True
        except Exception as e:
            logging.error(f"Error issuing start command: {e}")
            self.is_palworld_server_starting = False
            return False

    def _should_block_start(self, current_time):
        if self.is_palworld_process_running():
            logging.warning(
                "The attempt to start the Palworld server was made, but it is already running."
            )
            return True
        if self.is_palworld_server_starting:
            logging.warning("Palworld Server is already starting.")
            return True
        if current_time - self.last_server_started_time < self.server_starting_cooldown:
            logging.warning("Tried to start the server too quickly multiple times.")
            return True
        if current_time - self.last_server_stopped_time < self.server_stopping_cooldown:
            logging.warning(
                "You attempted to restart the server too Quickly after trying to stop it."
            )
            return True
        return False

    def _detect_existing_server_process(self):
        try:
            if not self.process_manager.launched_pid and not os.path.exists(
                self.process_manager.pid_file_name()
            ):
                pid = self.process_manager.find_process_pid("PalServer")
                if pid:
                    bus.publish(Event.SERVER_STARTED, {"pid": pid})
                    self.process_manager.set_known_pid(pid)
        except Exception:
            pass

    def _start_detection_loop(self):
        """Start a background thread that polls for a PalServer process every 5 s."""
        if self._detection_thread and self._detection_thread.is_alive():
            return
        self._detection_stop_event.clear()
        self._detection_thread = threading.Thread(
            target=self._server_detection_loop, daemon=True
        )
        self._detection_thread.start()

    def _server_detection_loop(self):
        """Repeatedly check for a PalServer process until one is found."""
        try:
            while not self._detection_stop_event.is_set():
                self._detect_existing_server_process()
                if self.process_manager.launched_pid:
                    break
                self._detection_stop_event.wait(settings.pollingRate)
        except Exception:
            logging.exception("Server detection loop failed")

    # Palworld often needs well over 30s before REST reports players. A short
    # guard caused auto-stop to fire during boot and then loop forever.
    AUTO_STOP_STARTUP_GUARD_SECONDS = 120

    def stop_server(self):
        logging.info("Palworld server stop command received")
        self._cancel_auto_stop_delay()
        if not self.is_palworld_process_running():
            logging.warning(
                "An attempt to stop the Palworld server was made, but it was not running."
            )
            # Still tear down polling / re-arm auto-start. A missed
            # SERVER_STOPPED event is what left the monitor stuck looping.
            self._force_stopped_state()
            return False
        current_time = time.time()
        if current_time - self.last_server_stopped_time < self.server_stopping_cooldown:
            logging.warning(
                "You attempted to restart the server too Quickly after trying to stop it."
            )
            return False
        try:
            bus.publish(Event.CMD_STOP_SERVER, {})
            return True
        except Exception as e:
            logging.error(f"Error issuing stop command: {e}")
            return False

    def _force_stopped_state(self):
        """Recover when the process is gone but SERVER_STOPPED never fired."""
        self.current_server_info["running"] = False
        self.current_server_info["playerCount"] = 0
        self.current_server_info["players"] = []
        self.current_server_info.pop("players_unknown", None)
        self.stop_server_info_update_thread()
        if self.process_manager.launched_pid is not None:
            pid = self.process_manager.launched_pid
            self.process_manager.set_known_pid(None)
            bus.publish(Event.SERVER_STOPPED, {"pid": pid})
        else:
            # Ensure auto-start re-arms even if pid was already cleared.
            bus.publish(Event.SERVER_STOPPED, {"pid": None})

    def _update_server_status(self):
        """Poll server and emit SERVER_STATUS event."""
        self._update_server_info_with_players()
        bus.publish(
            Event.SERVER_STATUS,
            {
                "running": self.current_server_info["running"],
                "playerCount": self.current_server_info["playerCount"],
                "players": list(self.current_server_info["players"]),
                "players_unknown": bool(
                    self.current_server_info.get("players_unknown")
                ),
                "banned_players": list(self.banlist_manager.get_banned_players()),
            },
        )

    def _update_server_info_with_players(self):
        running = self.is_palworld_process_running()
        self.current_server_info["running"] = running
        if not running:
            self.current_server_info["playerCount"] = 0
            self.current_server_info["players"] = []
            self.current_server_info.pop("players_unknown", None)
            return

        players = self.get_player_names()
        if players is None:
            # API down/booting — do not treat as an empty server.
            self.current_server_info["players_unknown"] = True
            return

        self.current_server_info.pop("players_unknown", None)
        self.current_server_info["playerCount"] = len(players)
        self.current_server_info["players"] = players
        if settings.enablePlayerTracking:
            self.player_manager.update_players_from_server(players)

    def _handle_auto_stop_condition(self):
        if self.auto_stop_delay_thread and self.auto_stop_delay_thread.is_alive():
            return
        if not self.is_palworld_process_running():
            return
        guard = self.AUTO_STOP_STARTUP_GUARD_SECONDS
        if time.time() - self.last_server_started_time < guard:
            remaining = guard - (time.time() - self.last_server_started_time)
            logging.debug(f"Auto-stop startup guard active, {remaining:.0f}s remaining")
            return
        logging.info("Auto-stop condition met, starting delay thread")
        self._auto_mode_cancelled = False
        self.auto_stop_delay_thread = threading.Thread(
            target=self._auto_stop_delay_worker, daemon=True
        )
        self.auto_stop_delay_thread.start()

    def _cancel_auto_stop_delay(self):
        if self.auto_stop_delay_thread and self.auto_stop_delay_thread.is_alive():
            logging.debug("Auto-stop cancelled")
            self._auto_mode_cancelled = True

    def _auto_stop_delay_worker(self):
        logging.info(f"Auto-stop delay started, stopping in {settings.autoStopDelay}s")
        time.sleep(settings.autoStopDelay)
        if self._auto_mode_cancelled:
            logging.debug("Auto-stop cancelled during sleep")
            return
        if not self.is_palworld_process_running():
            logging.info("Auto-stop skipped: server already stopped")
            self._force_stopped_state()
            return
        players = self.get_player_names()
        if players is None:
            logging.warning("Auto-stop skipped: player API unavailable")
            return
        if len(players) > 0:
            logging.info("Auto-stop skipped: players are online")
            return
        logging.info("Auto-stop delay elapsed, stopping server")
        self.stop_server()

    def get_player_count(self):
        return self.client.get_player_count()

    def get_player_names(self):
        return self.client.get_player_names()

    def get_players_for_web(self):
        return self.player_manager.get_all_players()

    def kick_player(self, steam_id):
        try:
            success = self.client.kick_player({"steam_id": steam_id})
            if success:
                bus.publish(Event.CMD_KICK_PLAYER, {"steam_id": steam_id})
            return success
        except Exception as e:
            logging.error(f"Error kicking {steam_id}: {e}")
            return False

    def ban_player(self, steam_id):
        try:
            success = self.client.ban_player({"steam_id": steam_id})
            if success:
                bus.publish(Event.CMD_BAN_PLAYER, {"steam_id": steam_id})
            return success
        except Exception as e:
            logging.error(f"Error banning {steam_id}: {e}")
            return False

    def unban_player(self, steam_id):
        try:
            success = self.client.unban_player({"steam_id": steam_id})
            if success:
                bus.publish(Event.CMD_UNBAN_PLAYER, {"steam_id": steam_id})
            return success
        except Exception as e:
            logging.error(f"Error unbanning {steam_id}: {e}")
            return False

    def get_banned_players(self):
        return self.banlist_manager.get_banned_players()

    def is_player_banned(self, steam_id):
        return self.banlist_manager.is_banned(steam_id)

    def start_server_info_update_thread(self):
        if not (self.update_thread and self.update_thread.is_alive()):
            self.update_thread_stop_event.clear()
            self.update_thread = threading.Thread(
                target=self._server_info_update_loop, daemon=True
            )
            self.update_thread.start()

    def stop_server_info_update_thread(self):
        if self.update_thread and self.update_thread.is_alive():
            self.update_thread_stop_event.set()
            self.update_thread.join(timeout=5)

    def _server_info_update_loop(self):
        while not self.update_thread_stop_event.is_set():
            try:
                self._update_server_status()
            except Exception as e:
                logging.error(f"Update loop error: {e}")
            if self.update_thread_stop_event.wait(settings.pollingRate):
                break

