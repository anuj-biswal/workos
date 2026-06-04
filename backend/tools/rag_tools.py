"""
RAG search tool for the LangGraph agent.
Provides semantic + keyword hybrid search across all indexed workspace documents.
Stores diagnostics from the last search for the debug panel / eval dashboard.
"""

import os
import time
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

# Lazy import of the RAG engine singleton — initialized in main.py
_rag_engine = None
# Rolling search history (last 20 queries) for eval dashboard
_search_history: list[dict] = []
# Last search diagnostics for debug panel
_last_diagnostics: dict | None = None
# Last retrieved chunks (for LLM-as-judge evaluation)
_last_context_chunks: list[dict] = []

def set_rag_engine(engine):
    """Called from main.py to inject the RAG engine singleton."""
    global _rag_engine
    _rag_engine = engine

def get_rag_engine():
    return _rag_engine

def get_last_diagnostics() -> dict | None:
    """Return diagnostics from the most recent search_documents call."""
    return _last_diagnostics

def get_last_context_chunks() -> list[dict]:
    """Return the retrieved context chunks from the most recent search."""
    return _last_context_chunks

def get_search_history() -> list[dict]:
    """Return rolling search history (last 20)."""
    return list(_search_history)

@tool
def search_documents(query: str, workspace_id: str = "default-workspace") -> str:
    """Search across all uploaded documents in the workspace using AI-powered semantic search.
    
    This tool finds the most relevant passages from PDFs, text files, CSVs, and Excel files
    that have been uploaded to the workspace. Returns matching excerpts with source citations
    (filename and page number).
    
    Use this tool FIRST when the user asks questions about document content, comparisons,
    or analysis. It is much faster and more accurate than reading entire files.
    
    Args:
        query: The search query describing what information you're looking for.
        workspace_id: The workspace identifier.
    
    Returns:
        Relevant passages with source citations, or a message if nothing was found.
    """
    global _last_diagnostics, _last_context_chunks, _search_history

    engine = get_rag_engine()
    if engine is None:
        _last_diagnostics = {"error": "RAG engine not initialized"}
        return "Error: RAG engine not initialized. Please try again later."

    try:
        search_result = engine.search(workspace_id, query, top_k=8)
    except Exception as e:
        logger.error(f"search_documents error: {e}")
        _last_diagnostics = {"error": str(e), "query": query}
        return f"Error during search: {str(e)}. Try using read_pdf_file or read_text_file instead."

    results = search_result.get("results", [])
    diagnostics = search_result.get("diagnostics", {})

    # Store diagnostics for the debug panel
    _last_diagnostics = diagnostics
    _last_context_chunks = results  # store for LLM-as-judge

    # Add to search history (rolling window of 20)
    _search_history.append({
        "query": query,
        "workspace_id": workspace_id,
        "results_count": len(results),
        "avg_confidence": diagnostics.get("avg_rrf_score"),
        "avg_cosine_sim": diagnostics.get("avg_cosine_similarity"),
        "agreement_rate": diagnostics.get("agreement_rate"),
        "latency_ms": diagnostics.get("latency_ms", {}).get("total", 0),
        "source_diversity": diagnostics.get("source_diversity", 0),
        "timestamp": time.time(),
    })
    if len(_search_history) > 20:
        _search_history.pop(0)

    if not results:
        return (
            "No relevant results found in the indexed documents. "
            "The files may not have been indexed yet, or the query may not match any content. "
            "Try using read_pdf_file or read_text_file to read specific files directly."
        )

    # Format results with clear citations
    output_parts = [f"Found {len(results)} relevant passages:\n"]

    for i, r in enumerate(results, 1):
        source = f"{r['filename']}"
        if r.get("page"):
            source += f", page {r['page']}"
        
        output_parts.append(
            f"━━━ Result {i} [Source: {source}] ━━━\n"
            f"{r['text']}\n"
        )

    return "\n".join(output_parts)
