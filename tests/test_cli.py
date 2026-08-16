from __future__ import annotations

from click.testing import CliRunner

from stackman.cli import cli, main


def test_click_cli_help_shows_only_branch_first_commands() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "Manage stacked Git branches." in result.output
    for command in ("track", "chain", "sync", "done", "list", "forget", "gh", "status"):
        assert command in result.output
    for legacy in ("init", "merged", "stacks"):
        assert legacy not in result.output
    assert "\n  stack " not in result.output
    assert "discover" not in result.output


def test_track_command_help() -> None:
    result = CliRunner().invoke(cli, ["track", "--help"])

    assert result.exit_code == 0
    assert "BRANCH" in result.output
    assert "--parent" in result.output
    assert "--stack" not in result.output


def test_chain_command_help() -> None:
    result = CliRunner().invoke(cli, ["chain", "--help"])

    assert result.exit_code == 0
    assert "ANCHOR" in result.output
    assert "BRANCHES" in result.output
    assert "--branches" not in result.output
    assert "--stack" not in result.output


def test_sync_command_help() -> None:
    result = CliRunner().invoke(cli, ["sync", "--help"])

    assert result.exit_code == 0
    assert "BRANCH" in result.output
    assert "--dry-run" in result.output
    assert "--squash" in result.output
    assert "--allow-dirty" in result.output
    assert "STACK_ID" not in result.output
    assert "--stack" not in result.output


def test_done_forget_and_list_commands_are_wired() -> None:
    runner = CliRunner()

    assert runner.invoke(cli, ["done", "--help"]).exit_code == 0
    assert runner.invoke(cli, ["forget", "--help"]).exit_code == 0
    assert runner.invoke(cli, ["list", "--help"]).exit_code == 0
    assert "--all" not in runner.invoke(cli, ["list", "--help"]).output


def test_legacy_commands_are_removed() -> None:
    runner = CliRunner()

    for command in ("init", "merged", "stacks", "stack"):
        result = runner.invoke(cli, [command, "--help"])
        assert result.exit_code != 0
        assert "No such command" in result.output


def test_main_prints_usage_errors(capsys) -> None:
    exit_code = main(["track"])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "Missing option '--parent'" in captured.err
