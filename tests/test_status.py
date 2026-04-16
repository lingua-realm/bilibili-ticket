def test_iteration_status_writer_echoes_and_appends_lines(tmp_path, capsys):
    from bilibili_ticket.status import IterationStatusWriter

    log_file = tmp_path / "monitor.status.log"
    writer = IterationStatusWriter(log_file)

    writer(["line-1", "line-2"])

    output = capsys.readouterr().out
    assert "line-1" in output
    assert "line-2" in output
    assert log_file.read_text(encoding="utf-8") == "line-1\nline-2\n"
