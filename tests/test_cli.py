"""Tests for the sms-toolkit CLI skeleton."""

from __future__ import annotations

import pytest

from sms_toolkit import cli


def test_stub_subcommands_exit_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    for name in cli.SUBCOMMANDS:
        assert cli.main([name]) == cli.EXIT_NOT_IMPLEMENTED
    captured = capsys.readouterr()
    assert "not implemented yet" in captured.err


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "sms-toolkit" in capsys.readouterr().out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
