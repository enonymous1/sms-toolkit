"""Tests for the sms-toolkit CLI skeleton."""

from __future__ import annotations

from pathlib import Path

import pytest

from sms_toolkit import cli


def test_stub_subcommands_exit_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    for name in cli.SUBCOMMANDS:
        if name in cli._RUNNERS:
            continue
        assert cli.main([name]) == cli.EXIT_NOT_IMPLEMENTED
    captured = capsys.readouterr()
    assert "not implemented yet" in captured.err


def test_sms_and_calls_are_wired(capsys: pytest.CaptureFixture[str]) -> None:
    for command in ("sms", "calls"):
        with pytest.raises(SystemExit) as exc:
            cli.main([command, "--help"])
        assert exc.value.code == 0
    assert "input_dir" in capsys.readouterr().out


def test_stage_subcommands_have_help() -> None:
    for command in (
        "normalize",
        "build-contacts",
        "import-ifttt",
        "export-attachments",
        "export-year",
        "ocr-extract",
        "ocr-match",
    ):
        with pytest.raises(SystemExit) as exc:
            cli.main([command, "--help"])
        assert exc.value.code == 0, command


def test_normalize_requires_report_or_apply(tmp_path: Path) -> None:
    db = tmp_path / "t.sqlite"
    db.touch()
    assert cli.main(["--root", str(tmp_path), "normalize", "--db", str(db)]) == 1


def test_export_year_rejects_bad_year(tmp_path: Path) -> None:
    assert cli.main(["--root", str(tmp_path), "export-year", "--year", "1999"]) == 1


def test_build_contacts_missing_db(tmp_path: Path) -> None:
    assert (
        cli.main(["--root", str(tmp_path), "build-contacts", "--db", str(tmp_path / "nope.sqlite")])
        == 1
    )


def test_export_attachments_missing_db(tmp_path: Path) -> None:
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path),
                "export-attachments",
                "--db",
                str(tmp_path / "nope.sqlite"),
            ]
        )
        == 1
    )


def test_import_ifttt_dry_run_through_cli(tmp_path: Path) -> None:
    from tests.stage_fixtures import make_ifttt_xlsx, make_stage_db

    db = make_stage_db(tmp_path / "t.sqlite")
    xlsx = make_ifttt_xlsx(tmp_path / "ifttt.xlsx")
    assert (
        cli.main(
            [
                "--root",
                str(tmp_path),
                "import-ifttt",
                "--db",
                str(db),
                "--files",
                str(xlsx),
                "--preview",
                "0",
            ]
        )
        == 0
    )


def test_sms_invalid_dir_returns_1(tmp_path: Path) -> None:
    assert cli.main(["--root", str(tmp_path), "sms", str(tmp_path / "nope")]) == 1


def test_no_args_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 0
    assert "sms-toolkit" in capsys.readouterr().out


def test_version_flag(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        cli.main(["--version"])
    assert exc.value.code == 0
    assert "0.1.0" in capsys.readouterr().out
