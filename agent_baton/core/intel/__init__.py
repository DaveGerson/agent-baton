"""Intel — derived knowledge layers over the raw bead store.

Modules in this package turn flat records (beads, traces, retros) into
inferred structure: edges, clusters, contradictions.  All inference is
deterministic and offline — no embeddings, no LLM calls.

Public surface:

* :class:`BeadSynthesizer` — pairwise edge inference, connected-component
  clustering, conflict flagging.  Runs post-phase by the executor and is
  also invokable manually via ``baton beads synthesize``.
"""
from agent_baton.core.intel.bead_synthesizer import BeadSynthesizer, SynthesisResult

__all__ = ["BeadSynthesizer", "SynthesisResult"]
