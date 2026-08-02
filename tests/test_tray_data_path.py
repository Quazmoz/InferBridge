from types import SimpleNamespace

from app import tray_app


def test_tray_passes_resolved_custom_data_root_to_server_child(monkeypatch, tmp_path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        tray_app,
        "configure_logging",
        lambda logs_dir: logs_dir / "tray.log",
    )
    args = SimpleNamespace(
        portable=False,
        data_dir="relative-data",
        mock=True,
        port=8123,
    )

    application = tray_app.TrayApplication(args)
    expected = (tmp_path / "relative-data").resolve()

    assert application.paths.data_root == expected
    assert application.controller.options.data_dir == str(expected)
    command = application.controller._server_command(
        SimpleNamespace(port=8123, nonce="nonce")
    )
    data_dir_index = command.index("--data-dir")
    assert command[data_dir_index + 1] == str(expected)
