"""Intel — derived knowledge & learning layers over the raw stores.

Modules in this package turn flat records (steps, beads, traces, retros)
into inferred structure: agent learning, edges, clusters, contradictions.
All inference is deterministic and offline by default — no embeddings,
no LLM calls — and every public entry point is best-effort, swallowing
exceptions so intel work can never block or break the execution path.

Public surface:

* :class:`ContextHarvester` (Wave 2.2) — writes a compact per-
  (agent_name, domain) learning row after every successful step so that
  subsequent dispatches can prepend a "Prior Context" block to the
  delegation prompt.
* :class:`BeadSynthesizer` (Wave 2.1) — pairwise edge inference,
  connected-component clustering, conflict flagging.  Runs post-phase
  by the executor and is also invokable manually via
  ``baton beads synthesize``.
"""
from agent_baton.core.intel.bead_synthesizer import BeadSynthesizer, SynthesisResult
from agent_baton.core.intel.context_harvester import ContextHarvester

__all__ = ["BeadSynthesizer", "ContextHarvester", "SynthesisResult"]
