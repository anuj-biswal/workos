"""
Hybrid RAG Engine — ChromaDB (vector) + BM25 (keyword) with Reciprocal Rank Fusion.

Provides per-workspace document indexing and hybrid semantic+keyword search.
"""

import os
import hashlib
import logging
from typing import Optional

import chromadb
from openai import OpenAI
from rank_bm25 import BM25Okapi
import pdfplumber
import pandas as pd

logger = logging.getLogger(__name__)

# ── Text extraction helpers ──────────────────────────────────────────────────

def _extract_text_from_pdf(file_path: str) -> list[dict]:
    """Extract text per page from a PDF using pdfplumber.
    Returns list of {page: int, text: str}.
    """
    pages = []
    try:
        with pdfplumber.open(file_path) as pdf:
            for i, page in enumerate(pdf.pages):
                text = page.extract_text() or ""
                # Also extract tables and append as text
                tables = page.extract_tables()
                if tables:
                    for table in tables:
                        rows = []
                        for row in table:
                            cleaned = [str(cell) if cell else "" for cell in row]
                            rows.append(" | ".join(cleaned))
                        text += "\n\n[Table]\n" + "\n".join(rows)
                if text.strip():
                    pages.append({"page": i + 1, "text": text.strip()})
    except Exception as e:
        logger.error(f"Error extracting PDF {file_path}: {e}")
    return pages


def _extract_text_from_text_file(file_path: str) -> list[dict]:
    """Read a plain text file. Returns list with a single entry."""
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if content.strip():
            return [{"page": 1, "text": content.strip()}]
    except Exception as e:
        logger.error(f"Error reading text file {file_path}: {e}")
    return []


def _extract_text_from_csv(file_path: str) -> list[dict]:
    """Convert CSV to text representation."""
    try:
        df = pd.read_csv(file_path)
        text = f"Columns: {', '.join(df.columns)}\n\n"
        text += df.to_string(index=False, max_rows=500)
        return [{"page": 1, "text": text}]
    except Exception as e:
        logger.error(f"Error reading CSV {file_path}: {e}")
    return []


def _extract_text_from_excel(file_path: str) -> list[dict]:
    """Convert Excel to text representation, one entry per sheet."""
    pages = []
    try:
        xls = pd.ExcelFile(file_path)
        for i, sheet_name in enumerate(xls.sheet_names):
            df = pd.read_excel(xls, sheet_name=sheet_name)
            text = f"Sheet: {sheet_name}\nColumns: {', '.join(df.columns)}\n\n"
            text += df.to_string(index=False, max_rows=500)
            pages.append({"page": i + 1, "text": text})
    except Exception as e:
        logger.error(f"Error reading Excel {file_path}: {e}")
    return pages


def _get_file_type(filename: str) -> str:
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    return ext


EXTRACTORS = {
    "pdf": _extract_text_from_pdf,
    "txt": _extract_text_from_text_file,
    "md": _extract_text_from_text_file,
    "log": _extract_text_from_text_file,
    "csv": _extract_text_from_csv,
    "xlsx": _extract_text_from_excel,
    "xls": _extract_text_from_excel,
}


# ── Chunking ─────────────────────────────────────────────────────────────────

def _chunk_text(text: str, chunk_size: int = 1000, chunk_overlap: int = 200) -> list[str]:
    """Split text into overlapping chunks using recursive character splitting."""
    if len(text) <= chunk_size:
        return [text]

    # Split on natural boundaries in order of preference
    separators = ["\n\n", "\n", ". ", " "]
    chunks = []
    
    def _split_recursive(text: str, separators: list[str]) -> list[str]:
        if len(text) <= chunk_size:
            return [text] if text.strip() else []
        
        # Find the best separator
        separator = separators[-1]  # fallback to space
        for sep in separators:
            if sep in text:
                separator = sep
                break
        
        parts = text.split(separator)
        current_chunk = ""
        result = []
        
        for part in parts:
            candidate = (current_chunk + separator + part).strip() if current_chunk else part.strip()
            if len(candidate) <= chunk_size:
                current_chunk = candidate
            else:
                if current_chunk:
                    result.append(current_chunk)
                # If the part itself is too large, try splitting with the next separator
                if len(part) > chunk_size and len(separators) > 1:
                    sub_chunks = _split_recursive(part, separators[1:])
                    result.extend(sub_chunks)
                    current_chunk = ""
                else:
                    current_chunk = part.strip()
        
        if current_chunk:
            result.append(current_chunk)
        return result
    
    raw_chunks = _split_recursive(text, separators)
    
    # Add overlap between chunks
    if chunk_overlap > 0 and len(raw_chunks) > 1:
        overlapped = [raw_chunks[0]]
        for i in range(1, len(raw_chunks)):
            prev = raw_chunks[i - 1]
            overlap_text = prev[-chunk_overlap:] if len(prev) > chunk_overlap else prev
            overlapped.append(overlap_text + " " + raw_chunks[i])
        return overlapped
    
    return raw_chunks


# ── RAG Engine ───────────────────────────────────────────────────────────────

class RAGEngine:
    """Hybrid RAG engine using ChromaDB (vector search) + BM25 (keyword search)."""

    def __init__(self, persist_dir: str):
        """
        Args:
            persist_dir: Directory for ChromaDB persistent storage.
        """
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        
        self.client = chromadb.PersistentClient(path=persist_dir)
        self.openai_client = OpenAI()
        self._bm25_cache: dict[str, dict] = {}  # workspace_id -> {corpus, docs_meta, index}
        
        logger.info(f"RAG Engine initialized with storage at {persist_dir}")

    def _collection_name(self, workspace_id: str) -> str:
        """Generate a safe collection name from workspace_id."""
        # ChromaDB collection names: 3-63 chars, alphanumeric + underscores/hyphens
        safe = workspace_id.replace("/", "_").replace("\\", "_")
        if len(safe) > 60:
            safe = hashlib.md5(safe.encode()).hexdigest()[:16]
        return f"ws_{safe}"

    def _get_collection(self, workspace_id: str):
        return self.client.get_or_create_collection(
            name=self._collection_name(workspace_id),
            metadata={"hnsw:space": "cosine"},
        )

    def _embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings using OpenAI text-embedding-3-large."""
        if not texts:
            return []
        # OpenAI API supports batch embedding
        response = self.openai_client.embeddings.create(
            model="text-embedding-3-large",
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _embed_query(self, query: str) -> list[float]:
        """Embed a single query string."""
        return self._embed_texts([query])[0]

    # ── Ingestion ────────────────────────────────────────────────────────

    def ingest_file(self, workspace_id: str, filename: str, file_path: str) -> dict:
        """
        Extract text from a file, chunk it, embed, and store in ChromaDB.
        
        Returns: {filename, chunks_created, status}
        """
        ext = _get_file_type(filename)
        extractor = EXTRACTORS.get(ext)
        
        if not extractor:
            return {"filename": filename, "chunks_created": 0, "status": "unsupported_type"}

        if not os.path.exists(file_path):
            return {"filename": filename, "chunks_created": 0, "status": "file_not_found"}

        # Extract text pages
        pages = extractor(file_path)
        if not pages:
            return {"filename": filename, "chunks_created": 0, "status": "no_text_extracted"}

        # Remove any existing chunks for this file (re-indexing)
        self.delete_file_index(workspace_id, filename)

        # Chunk each page and prepare for insertion
        all_chunks = []
        all_ids = []
        all_metadatas = []

        for page_info in pages:
            page_num = page_info["page"]
            chunks = _chunk_text(page_info["text"])
            
            for chunk_idx, chunk_text in enumerate(chunks):
                chunk_id = f"{filename}__p{page_num}__c{chunk_idx}"
                all_chunks.append(chunk_text)
                all_ids.append(chunk_id)
                all_metadatas.append({
                    "filename": filename,
                    "page": page_num,
                    "chunk_index": chunk_idx,
                    "workspace_id": workspace_id,
                })

        if not all_chunks:
            return {"filename": filename, "chunks_created": 0, "status": "no_chunks"}

        # Generate embeddings
        try:
            embeddings = self._embed_texts(all_chunks)
        except Exception as e:
            logger.error(f"Embedding error for {filename}: {e}")
            return {"filename": filename, "chunks_created": 0, "status": f"embedding_error: {e}"}

        # Store in ChromaDB
        collection = self._get_collection(workspace_id)
        
        # ChromaDB has a batch limit; insert in batches of 100
        batch_size = 100
        for i in range(0, len(all_chunks), batch_size):
            end = min(i + batch_size, len(all_chunks))
            collection.add(
                ids=all_ids[i:end],
                documents=all_chunks[i:end],
                embeddings=embeddings[i:end],
                metadatas=all_metadatas[i:end],
            )

        # Invalidate BM25 cache for this workspace
        self._bm25_cache.pop(workspace_id, None)

        logger.info(f"Indexed {filename}: {len(all_chunks)} chunks in workspace {workspace_id}")
        return {"filename": filename, "chunks_created": len(all_chunks), "status": "success"}

    # ── Deletion ─────────────────────────────────────────────────────────

    def delete_file_index(self, workspace_id: str, filename: str):
        """Remove all chunks for a specific file from the index."""
        collection = self._get_collection(workspace_id)
        try:
            # Query for all chunks belonging to this file
            results = collection.get(
                where={"filename": filename},
            )
            if results["ids"]:
                collection.delete(ids=results["ids"])
                self._bm25_cache.pop(workspace_id, None)
                logger.info(f"Deleted {len(results['ids'])} chunks for {filename}")
        except Exception as e:
            logger.warning(f"Error deleting index for {filename}: {e}")

    # ── BM25 index management ────────────────────────────────────────────

    def _get_bm25_index(self, workspace_id: str):
        """Get or rebuild the BM25 index for a workspace."""
        if workspace_id in self._bm25_cache:
            return self._bm25_cache[workspace_id]

        collection = self._get_collection(workspace_id)
        all_data = collection.get(include=["documents", "metadatas"])

        if not all_data["documents"]:
            return None

        # Tokenize documents for BM25
        tokenized_corpus = [doc.lower().split() for doc in all_data["documents"]]
        bm25_index = BM25Okapi(tokenized_corpus)

        cache_entry = {
            "corpus": all_data["documents"],
            "metadatas": all_data["metadatas"],
            "ids": all_data["ids"],
            "index": bm25_index,
        }
        self._bm25_cache[workspace_id] = cache_entry
        return cache_entry

    # ── Hybrid Search ────────────────────────────────────────────────────

    def search(self, workspace_id: str, query: str, top_k: int = 8) -> list[dict]:
        """
        Hybrid search: vector (ChromaDB) + keyword (BM25) with Reciprocal Rank Fusion.
        
        Returns list of {text, filename, page, score, source} dicts, ranked by relevance.
        """
        collection = self._get_collection(workspace_id)
        
        # Check if collection has any documents
        if collection.count() == 0:
            return []

        n_candidates = min(20, collection.count())

        # ── Vector search ────────────────────────────────────────────────
        try:
            query_embedding = self._embed_query(query)
            vector_results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_candidates,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            vector_results = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

        # ── BM25 search ──────────────────────────────────────────────────
        bm25_data = self._get_bm25_index(workspace_id)
        bm25_results = {"ids": [], "documents": [], "metadatas": [], "scores": []}
        
        if bm25_data:
            tokenized_query = query.lower().split()
            scores = bm25_data["index"].get_scores(tokenized_query)
            
            # Get top-n by BM25 score
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_candidates]
            for idx in top_indices:
                if scores[idx] > 0:
                    bm25_results["ids"].append(bm25_data["ids"][idx])
                    bm25_results["documents"].append(bm25_data["corpus"][idx])
                    bm25_results["metadatas"].append(bm25_data["metadatas"][idx])
                    bm25_results["scores"].append(scores[idx])

        # ── Reciprocal Rank Fusion ───────────────────────────────────────
        k = 60  # RRF constant
        rrf_scores: dict[str, float] = {}
        doc_map: dict[str, dict] = {}

        # Score vector results
        for rank, doc_id in enumerate(vector_results["ids"][0]):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in doc_map:
                idx = vector_results["ids"][0].index(doc_id)
                doc_map[doc_id] = {
                    "text": vector_results["documents"][0][idx],
                    "filename": vector_results["metadatas"][0][idx]["filename"],
                    "page": vector_results["metadatas"][0][idx]["page"],
                }

        # Score BM25 results
        for rank, doc_id in enumerate(bm25_results["ids"]):
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in doc_map:
                idx = bm25_results["ids"].index(doc_id)
                doc_map[doc_id] = {
                    "text": bm25_results["documents"][idx],
                    "filename": bm25_results["metadatas"][idx]["filename"],
                    "page": bm25_results["metadatas"][idx]["page"],
                }

        # Sort by RRF score and return top_k
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        
        results = []
        for doc_id, score in ranked:
            entry = doc_map[doc_id].copy()
            entry["score"] = round(score, 4)
            entry["id"] = doc_id
            results.append(entry)

        return results

    # ── Status ───────────────────────────────────────────────────────────

    def get_indexed_files(self, workspace_id: str) -> list[dict]:
        """List all indexed files with their chunk counts."""
        collection = self._get_collection(workspace_id)
        all_data = collection.get(include=["metadatas"])
        
        if not all_data["metadatas"]:
            return []

        file_counts: dict[str, int] = {}
        for meta in all_data["metadatas"]:
            fname = meta["filename"]
            file_counts[fname] = file_counts.get(fname, 0) + 1

        return [{"filename": fname, "chunks": count} for fname, count in sorted(file_counts.items())]

    def get_total_chunks(self, workspace_id: str) -> int:
        """Get total number of chunks in a workspace's index."""
        return self._get_collection(workspace_id).count()
