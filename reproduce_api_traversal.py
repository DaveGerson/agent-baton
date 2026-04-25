
import json
import os
from pathlib import Path
from agent_baton.models.decision import DecisionRequest
from agent_baton.api.routes.decisions import get_decision

# Mock DecisionManager
class MockDecisionManager:
    def __init__(self, request):
        self.request = request
    def get(self, request_id):
        return self.request

async def test_api_traversal():
    # Setup dummy paths
    root = Path("/tmp/baton_api_test")
    root.mkdir(parents=True, exist_ok=True)
    
    # Secret file
    secret_file = root / "api_secret.txt"
    secret_file.write_text("API_SERVER_SECRET_DATA")
    
    # Create malicious request
    # Since we are using absolute path here (or relative to API server CWD)
    malicious_path = str(secret_file.resolve())
    
    req = DecisionRequest(
        request_id="dec-001",
        task_id="task-001",
        decision_type="escalation",
        summary="Test",
        context_files=[malicious_path]
    )
    
    manager = MockDecisionManager(req)
    
    print(f"Attempting to read through API traversal: {malicious_path}")
    # Call the route handler directly (it's an async function)
    import asyncio
    response = await get_decision("dec-001", manager)
    
    content = response.context_file_contents.get(malicious_path)
    if content == "API_SERVER_SECRET_DATA":
        print("VULNERABILITY CONFIRMED: Read API server secret file!")
        print(f"Content: {content}")
    else:
        print("VULNERABILITY NOT EXPOSED (or failed to read)")

if __name__ == "__main__":
    import asyncio
    try:
        asyncio.run(test_api_traversal())
    finally:
        # Cleanup
        import shutil
        if os.path.exists("/tmp/baton_api_test"):
            shutil.rmtree("/tmp/baton_api_test")
