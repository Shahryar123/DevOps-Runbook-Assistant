"""Vector stores behind one small interface (VectorIndex).

VECTOR_STORE picks the implementation: "chroma" (local folder, default) or
"azure_search" (Azure AI Search). The retriever, graph, UI and ingest
pipeline only use VectorIndex, so they work the same with either.
"""
