import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch
from agent_baton.cli.commands import maintenance_cmd

def test_maintenance_status():
    mock_git = MagicMock()
    mock_git.list_merged_branches.return_value = ["feat/test-merged"]
    
    mock_wt = MagicMock()
    mock_wt.gc_stale.return_value = []
    
    with patch('agent_baton.cli.commands.maintenance_cmd._handle_status') as mock_status:
        # Just verifying the handler routes correctly
        args = argparse.Namespace(subcommand="status")
        with patch('agent_baton.core.git_manager.GitManager', return_value=mock_git),              patch('agent_baton.core.engine.worktree_manager.WorktreeManager', return_value=mock_wt):
            maintenance_cmd.handler(args)
            mock_status.assert_called_once()

def test_maintenance_cleanup_dry_run():
    mock_git = MagicMock()
    mock_git.list_merged_branches.return_value = ["feat/test-merged"]
    
    mock_wt = MagicMock()
    
    args = argparse.Namespace(
        subcommand="cleanup",
        branches=True,
        worktrees=False,
        remote_prune=False,
        dry_run=True,
        force=False
    )
    
    with patch('agent_baton.core.git_manager.GitManager', return_value=mock_git),          patch('agent_baton.core.engine.worktree_manager.WorktreeManager', return_value=mock_wt):
        # Capturing stdout to verify dry run message
        with patch('builtins.print') as mock_print:
            maintenance_cmd.handler(args)
            mock_print.assert_any_call("  [DRY RUN] Would delete branch: feat/test-merged")
