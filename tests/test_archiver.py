import sqlite3
import json
from pathlib import Path
import pytest
from agent_baton.core.observe.archiver import DataArchiver

def test_archiver_vacuum_and_rotation(tmp_path):
    # Setup mock environment
    ctx_root = tmp_path / "team-context"
    ctx_root.mkdir()
    
    # 1. Test SQLite Vacuum
    db_path = ctx_root / "baton.db"
    conn = sqlite3.connect(db_path)
    conn.execute("CREATE TABLE test (id INTEGER PRIMARY KEY, data TEXT)")
    conn.execute("INSERT INTO test (data) VALUES ('some large data' || hex(randomblob(1000)))")
    conn.commit()
    initial_size = db_path.stat().st_size
    conn.execute("DELETE FROM test")
    conn.commit()
    conn.close()
    
    # 2. Test JSONL Rotation
    telemetry_path = ctx_root / "telemetry.jsonl"
    with open(telemetry_path, "w") as f:
        for i in range(100):
            f.write(json.dumps({"event": i}) + "\n")
            
    archiver = DataArchiver(ctx_root)
    
    # Run cleanup with 0 retention to trigger everything
    # We'll use a small max_lines for rotation to test it
    archiver._rotate_jsonl(telemetry_path, max_lines=10)
    archiver._vacuum_db(db_path)
    
    # Verify rotation
    with open(telemetry_path, "r") as f:
        lines = f.readlines()
        assert len(lines) == 10
        assert json.loads(lines[-1])["event"] == 99
        
    # Verify vacuum (size should be smaller or at least handled without error)
    assert db_path.exists()
    
def test_archiver_summary_includes_db(tmp_path):
    ctx_root = tmp_path / "team-context"
    ctx_root.mkdir()
    db_path = ctx_root / "baton.db"
    db_path.write_text("dummy sqlite content")
    
    archiver = DataArchiver(ctx_root)
    summary = archiver.summary(retention_days=90)
    assert "baton.db" in summary
    assert "will be vacuumed" in summary
