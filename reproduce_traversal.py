
import os
from pathlib import Path
from agent_baton.core.engine.executor import ExecutionEngine
from agent_baton.models.execution import StepResult, ExecutionState

def test_path_traversal():
    # Setup dummy paths
    root = Path("/tmp/baton_test_root")
    root.mkdir(parents=True, exist_ok=True)
    
    # Secret file we want to read
    secret_file = root / "secret.txt"
    secret_file.write_text("THIS IS A SECRET")
    
    # Task execution dir
    task_id = "task-001"
    task_dir = root / "executions" / task_id
    task_dir.mkdir(parents=True, exist_ok=True)
    
    # Create executor
    executor = ExecutionEngine(root, task_id=task_id)
    
    # Create malicious StepResult
    # We want to go from /tmp/baton_test_root/executions/task-001/ to /tmp/baton_test_root/secret.txt
    # So we need to go up 2 levels: ../../secret.txt
    malicious_path = "../../secret.txt"
    result = StepResult(
        step_id="1.1",
        agent_name="attacker",
        outcome="[TRUNCATED]",
        outcome_spillover_path=malicious_path
    )
    
    print(f"Attempting to read through traversal: {malicious_path}")
    
    # Debug path resolution
    spillover_file = Path(root) / "executions" / task_id / malicious_path
    print(f"Target path: {spillover_file}")
    print(f"Target path resolved: {spillover_file.resolve()}")
    print(f"Secret file path: {secret_file.resolve()}")
    print(f"Secret file exists: {secret_file.exists()}")
    
    handoff = executor._load_handoff_outcome(result)
    
    if "THIS IS A SECRET" in handoff:
        print("VULNERABILITY CONFIRMED: Read secret file content!")
        print(f"Content: {handoff}")
    else:
        print("VULNERABILITY NOT EXPOSED (or failed to read)")

if __name__ == "__main__":
    try:
        test_path_traversal()
    finally:
        # Cleanup
        import shutil
        if os.path.exists("/tmp/baton_test_root"):
            shutil.rmtree("/tmp/baton_test_root")
