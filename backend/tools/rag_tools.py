"""
RAG search tool for the LangGraph agent.
Provides semantic + keyword hybrid search across all indexed workspace documents.
"""

import os
from langchain_core.tools import tool

# Lazy import of the RAG engine singleton — initialized in main.py
_rag_engine = None

def set_rag_engine(engine):
    """Called from main.py to inject the RAG engine singleton."""
    global _rag_engine
    _rag_engine = engine

def get_rag_engine():
    global _rag_engine
    return _rag_engine

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
    engine = get_rag_engine()
    if engine is None:
        return "Error: RAG engine not initialized. Please try again later."

    results = engine.search(workspace_id, query, top_k=8)

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
