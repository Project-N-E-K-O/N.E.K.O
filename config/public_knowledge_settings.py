"""Safe defaults for automatic public-knowledge context."""

PUBLIC_KNOWLEDGE_AUTO_CONTEXT_ENABLED = True
PUBLIC_KNOWLEDGE_AUTO_CONTEXT_MAX_HITS = 2

# Turn-local retrieval is optional enrichment and must never hold the reply path
# open indefinitely.  Automatic context has a first-token-sensitive budget;
# explicit local lookup gets a wider budget because the user asked for it.
PUBLIC_KNOWLEDGE_AUTO_CONTEXT_BUDGET_SECONDS = 0.35
PUBLIC_KNOWLEDGE_EXPLICIT_LOOKUP_BUDGET_SECONDS = 2.0

# Automatic conversation assistance has a much lower tolerance for false
# positives than an explicit lookup.  Keep these separate from the calibrated
# explicit-search floor in ``knowledge.vector_index``.
PUBLIC_KNOWLEDGE_AUTO_KNOWLEDGE_DUAL_THRESHOLD = 0.62
PUBLIC_KNOWLEDGE_AUTO_KNOWLEDGE_SEMANTIC_THRESHOLD = 0.68
PUBLIC_KNOWLEDGE_AUTO_KNOWLEDGE_SEMANTIC_MARGIN = 0.03
PUBLIC_KNOWLEDGE_AUTO_CORPUS_DUAL_THRESHOLD = 0.64
PUBLIC_KNOWLEDGE_AUTO_CORPUS_SEMANTIC_THRESHOLD = 0.70
PUBLIC_KNOWLEDGE_AUTO_CORPUS_SEMANTIC_MARGIN = 0.03
