"""Tests for SteamCMD / LGSM update helpers and controller update flow."""

from unittest.mock import MagicMock, patch

from src.steam_update import (
    check_steam_update,
    parse_public_build_id,
    read_local_build_id,
    resolve_install_dir,
    resolve_steamcmd_path,
    run_steamcmd_update,
)


class TestResolveSteamcmdPath:
    def test_uses_configured_path_when_present(self, mock_settings):
        mock_settings.steamcmdPath = "/custom/steamcmd"
        with (
            patch("src.steam_update.settings", mock_settings),
            patch("src.steam_update.shutil.which", return_value=None),
            patch("src.steam_update.os.path.isfile", return_value=True),
            patch("src.steam_update.os.access", return_value=True),
        ):
            assert resolve_steamcmd_path() == "/custom/steamcmd"

    def test_falls_back_to_which(self, mock_settings):
        mock_settings.steamcmdPath = None
        with (
            patch("src.steam_update.settings", mock_settings),
            patch(
                "src.steam_update.shutil.which",
                side_effect=lambda c: "/usr/games/steamcmd" if c == "steamcmd" else None,
            ),
        ):
            assert resolve_steamcmd_path() == "/usr/games/steamcmd"


class TestResolveInstallDir:
    def test_uses_configured_install_dir(self, mock_settings):
        mock_settings.steamcmdInstallDir = "/home/steam/PalServer"
        mock_settings.palworldServerExePath = "/ignored/PalServer.sh"
        with patch("src.steam_update.settings", mock_settings):
            assert resolve_install_dir().endswith("PalServer")

    def test_defaults_to_exe_dirname(self, mock_settings):
        mock_settings.steamcmdInstallDir = None
        mock_settings.palworldServerExePath = "/home/steam/PalServer/PalServer.sh"
        with patch("src.steam_update.settings", mock_settings):
            assert resolve_install_dir().replace("\\", "/").endswith(
                "/home/steam/PalServer"
            )


class TestBuildIdParsing:
    def test_parse_public_build_id(self):
        sample = '''
        "depots"
        {
            "branches"
            {
                "public"
                {
                    "buildid"		"20123456"
                    "timeupdated"		"1710000000"
                }
            }
        }
        '''
        assert parse_public_build_id(sample) == "20123456"

    def test_read_local_build_id(self, tmp_path):
        steamapps = tmp_path / "steamapps"
        steamapps.mkdir()
        manifest = steamapps / "appmanifest_2394010.acf"
        manifest.write_text('"AppState"\n{\n\t"buildid"\t\t"111"\n}\n', encoding="utf-8")
        assert read_local_build_id(str(tmp_path)) == "111"


class TestCheckSteamUpdate:
    def test_up_to_date(self, tmp_path):
        steamapps = tmp_path / "steamapps"
        steamapps.mkdir()
        (steamapps / "appmanifest_2394010.acf").write_text(
            '"buildid" "42"\n', encoding="utf-8"
        )
        with patch(
            "src.steam_update.fetch_remote_build_id", return_value=("42", "")
        ):
            available, message = check_steam_update("steamcmd", str(tmp_path))
        assert available is False
        assert "up to date" in message.lower()

    def test_update_available(self, tmp_path):
        steamapps = tmp_path / "steamapps"
        steamapps.mkdir()
        (steamapps / "appmanifest_2394010.acf").write_text(
            '"buildid" "42"\n', encoding="utf-8"
        )
        with patch(
            "src.steam_update.fetch_remote_build_id", return_value=("99", "")
        ):
            available, message = check_steam_update("steamcmd", str(tmp_path))
        assert available is True
        assert "available" in message.lower()


class TestRunSteamcmdUpdate:
    def test_success(self):
        result = MagicMock(returncode=0, stdout="Success!", stderr="")
        with patch("src.steam_update.subprocess.run", return_value=result) as run:
            ok, message = run_steamcmd_update(
                "/usr/games/steamcmd", "/home/steam/PalServer"
            )
        assert ok is True
        assert "successfully" in message.lower()
        assert "2394010" in run.call_args.args[0]

    def test_nonzero_exit(self):
        result = MagicMock(returncode=1, stdout="Error!", stderr="")
        with patch("src.steam_update.subprocess.run", return_value=result):
            ok, message = run_steamcmd_update(
                "/usr/games/steamcmd", "/home/steam/PalServer"
            )
        assert ok is False
        assert "failed" in message.lower()


class TestControllerUpdateServer:
    def test_blocks_concurrent_updates(
        self, mock_settings, mock_process_manager, mock_player_manager, mock_banlist_manager
    ):
        from src.palworld_control import PalWorldController
        from test.support import create_mock_api_client

        controller = PalWorldController(
            client=create_mock_api_client(),
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )
        controller._steam_update_status = {"state": "running", "message": "busy"}
        ok, message = controller.update_server()
        assert ok is False
        assert "already" in message.lower()

    def test_start_blocked_while_updating(
        self, mock_settings, mock_process_manager, mock_player_manager, mock_banlist_manager
    ):
        from src.palworld_control import PalWorldController
        from test.support import create_mock_api_client

        mock_process_manager.is_process_running.return_value = False
        controller = PalWorldController(
            client=create_mock_api_client(),
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )
        controller._steam_update_status = {"state": "running", "message": "busy"}
        assert controller.start_server() is False

    def test_up_to_date_skips_stop_and_update(
        self, mock_settings, mock_process_manager, mock_player_manager, mock_banlist_manager
    ):
        from src.palworld_control import PalWorldController
        from test.support import create_mock_api_client

        mock_process_manager.is_process_running.return_value = True
        controller = PalWorldController(
            client=create_mock_api_client(),
            process_manager=mock_process_manager,
            player_manager=mock_player_manager,
            banlist_manager=mock_banlist_manager,
        )
        controller.stop_server = MagicMock()
        with (
            patch(
                "src.palworld_control.check_steam_update",
                return_value=(False, "Up to date (build 1)"),
            ),
            patch("src.palworld_control.resolve_steamcmd_path", return_value="/steamcmd"),
            patch(
                "src.palworld_control.resolve_install_dir",
                return_value="/home/steam/PalServer",
            ),
            patch("src.palworld_control.run_steamcmd_update") as mock_update,
        ):
            controller._steam_update_worker()
            mock_update.assert_not_called()
            controller.stop_server.assert_not_called()
        status = controller.get_steam_update_status()
        assert status["state"] == "success"
        assert "up to date" in status["message"].lower()
