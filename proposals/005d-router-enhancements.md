# Proposal 005d: Advanced Agent Routing & Composite Assignment

## 1. Objective
Replace the fragile file-presence routing heuristics with a robust, weighted scoring system, and enable multi-agent composite assignment within a single execution plan to better support polyglot monorepos.

## 2. Weighted Stack Detection
Create `agent_baton/core/orchestration/detector.py`. 

### Scoring Algorithm
Instead of halting at the first manifest file found, the detector will scan the workspace up to a configurable depth and apply weighted scores to determine the primary and secondary project profiles.

*Example Heuristics:*
- `pyproject.toml` (root): +10 Python
- `Cargo.toml` (root): +10 Rust
- `package.json` (root): +10 Node
- `package.json` (depth > 1, e.g., `ui/` or `frontend/`): +3 Node
- `.go` files presence: +5 Go

The highest score determines the *primary* stack flavor, but all identified stacks are preserved in a new `CompositeStackProfile` object.

## 3. Phase-Level Agent Assignment
Update the `MachinePlan` and `Phase` Pydantic models to allow agent overrides at the phase or step level, rather than enforcing a global project agent.

```python
class Phase(BaseModel):
    id: str
    name: str
    required_agent: Optional[str] = Field(
        default=None, 
        description="Overrides the project default agent for this phase (e.g., 'frontend-engineer--react')"
    )
```

## 4. Planner Updates
Modify the `CapabilityAnalyzer` (introduced in Proposal 005b) to utilize the `CompositeStackProfile`. 

If the detector finds both Python (primary score 10) and Node (secondary score 3), the planner is empowered to assign the `backend-engineer--python` agent to backend infrastructure steps, and the `frontend-engineer--node` (or `--react`) agent to UI development steps. This effectively creates a composite team working collaboratively on a polyglot monorepo within a single unified plan.