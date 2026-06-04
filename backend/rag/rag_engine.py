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

    def search(self, workspace_id: str, query: str, top_k: int = 8) -> dict:
        """
        Hybrid search: vector (ChromaDB) + keyword (BM25) with Reciprocal Rank Fusion.
        
        Returns dict with:
            results: list of {text, filename, page, score, id} ranked by relevance
            diagnostics: {query, latency_ms, vector/bm25 counts, agreement, per_result_debug, ...}
        """
        import time as _time

        t_start = _time.perf_counter()
        collection = self._get_collection(workspace_id)
        total_indexed = collection.count()

        # Empty collection fast-path
        if total_indexed == 0:
            return {"results": [], "diagnostics": self._empty_diagnostics(query, 0)}

        n_candidates = min(20, total_indexed)

        # ── Vector search ────────────────────────────────────────────────
        t_embed_start = _time.perf_counter()
        try:
            query_embedding = self._embed_query(query)
        except Exception as e:
            logger.error(f"Embedding error: {e}")
            return {"results": [], "diagnostics": self._empty_diagnostics(query, total_indexed, error=str(e))}
        t_embed_end = _time.perf_counter()

        t_vec_start = _time.perf_counter()
        try:
            vector_results = collection.query(
                query_embeddings=[query_embedding],
                n_results=n_candidates,
                include=["documents", "metadatas", "distances"],
            )
        except Exception as e:
            logger.error(f"Vector search error: {e}")
            vector_results = {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}
        t_vec_end = _time.perf_counter()

        # ── BM25 search ──────────────────────────────────────────────────
        t_bm25_start = _time.perf_counter()
        bm25_data = self._get_bm25_index(workspace_id)
        bm25_results = {"ids": [], "documents": [], "metadatas": [], "scores": []}
        
        if bm25_data:
            tokenized_query = query.lower().split()
            scores = bm25_data["index"].get_scores(tokenized_query)
            top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:n_candidates]
            for idx in top_indices:
                if scores[idx] > 0:
                    bm25_results["ids"].append(bm25_data["ids"][idx])
                    bm25_results["documents"].append(bm25_data["corpus"][idx])
                    bm25_results["metadatas"].append(bm25_data["metadatas"][idx])
                    bm25_results["scores"].append(float(scores[idx]))
        t_bm25_end = _time.perf_counter()

        # ── Reciprocal Rank Fusion ───────────────────────────────────────
        t_fusion_start = _time.perf_counter()
        k = 60  # RRF constant
        rrf_scores: dict[str, float] = {}
        doc_map: dict[str, dict] = {}
        vector_ids = set()
        bm25_ids = set()

        # Build per-result debug info
        per_result_debug: dict[str, dict] = {}

        # Score vector results
        for rank, doc_id in enumerate(vector_results["ids"][0]):
            vector_ids.add(doc_id)
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            idx = rank  # ids are in order
            cosine_distance = vector_results["distances"][0][idx] if vector_results["distances"][0] else 0
            cosine_similarity = round(1.0 - cosine_distance, 4)

            if doc_id not in doc_map:
                doc_map[doc_id] = {
                    "text": vector_results["documents"][0][idx],
                    "filename": vector_results["metadatas"][0][idx]["filename"],
                    "page": vector_results["metadatas"][0][idx]["page"],
                }
            per_result_debug[doc_id] = {
                "cosine_distance": round(cosine_distance, 4),
                "cosine_similarity": cosine_similarity,
                "bm25_score": 0.0,
                "vector_rank": rank + 1,
                "bm25_rank": None,
                "found_by": ["vector"],
            }

        # Score BM25 results
        for rank, doc_id in enumerate(bm25_results["ids"]):
            bm25_ids.add(doc_id)
            rrf_scores[doc_id] = rrf_scores.get(doc_id, 0) + 1.0 / (k + rank + 1)
            if doc_id not in doc_map:
                idx = bm25_results["ids"].index(doc_id)
                doc_map[doc_id] = {
                    "text": bm25_results["documents"][idx],
                    "filename": bm25_results["metadatas"][idx]["filename"],
                    "page": bm25_results["metadatas"][idx]["page"],
                }
            bm25_score = bm25_results["scores"][rank]
            if doc_id in per_result_debug:
                per_result_debug[doc_id]["bm25_score"] = round(bm25_score, 4)
                per_result_debug[doc_id]["bm25_rank"] = rank + 1
                per_result_debug[doc_id]["found_by"].append("bm25")
            else:
                per_result_debug[doc_id] = {
                    "cosine_distance": None,
                    "cosine_similarity": None,
                    "bm25_score": round(bm25_score, 4),
                    "vector_rank": None,
                    "bm25_rank": rank + 1,
                    "found_by": ["bm25"],
                }

        # Sort by RRF score and return top_k
        ranked = sorted(rrf_scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
        t_fusion_end = _time.perf_counter()

        results = []
        final_debug = []
        for doc_id, score in ranked:
            entry = doc_map[doc_id].copy()
            entry["score"] = round(score, 4)
            entry["id"] = doc_id
            results.append(entry)

            dbg = per_result_debug.get(doc_id, {}).copy()
            dbg["id"] = doc_id
            dbg["rrf_score"] = round(score, 4)
            dbg["filename"] = entry["filename"]
            dbg["page"] = entry["page"]
            final_debug.append(dbg)

        t_end = _time.perf_counter()

        # ── Compute diagnostics ──────────────────────────────────────────
        overlap = vector_ids & bm25_ids
        union = vector_ids | bm25_ids
        agreement_rate = round(len(overlap) / len(union), 4) if union else 0.0

        cosine_sims = [d["cosine_similarity"] for d in final_debug if d.get("cosine_similarity") is not None]
        bm25_scores_list = [d["bm25_score"] for d in final_debug if d.get("bm25_score", 0) > 0]
        rrf_scores_list = [d["rrf_score"] for d in final_debug]

        source_files = list(set(r["filename"] for r in results))

        diagnostics = {
            "query": query,
            "total_indexed_chunks": total_indexed,
            "vector_results_count": len(vector_ids),
            "bm25_results_count": len(bm25_ids),
            "vector_bm25_overlap": len(overlap),
            "agreement_rate": agreement_rate,
            "avg_cosine_similarity": round(sum(cosine_sims) / len(cosine_sims), 4) if cosine_sims else None,
            "max_cosine_similarity": round(max(cosine_sims), 4) if cosine_sims else None,
            "avg_bm25_score": round(sum(bm25_scores_list) / len(bm25_scores_list), 4) if bm25_scores_list else None,
            "max_bm25_score": round(max(bm25_scores_list), 4) if bm25_scores_list else None,
            "avg_rrf_score": round(sum(rrf_scores_list) / len(rrf_scores_list), 4) if rrf_scores_list else None,
            "source_diversity": len(source_files),
            "sources": source_files,
            "results_returned": len(results),
            "latency_ms": {
                "embedding": round((t_embed_end - t_embed_start) * 1000, 1),
                "vector_search": round((t_vec_end - t_vec_start) * 1000, 1),
                "bm25_search": round((t_bm25_end - t_bm25_start) * 1000, 1),
                "fusion": round((t_fusion_end - t_fusion_start) * 1000, 1),
                "total": round((t_end - t_start) * 1000, 1),
            },
            "per_result_debug": final_debug,
        }

        return {"results": results, "diagnostics": diagnostics}

    def _empty_diagnostics(self, query: str, total_indexed: int, error: str = None) -> dict:
        """Return an empty diagnostics dict for edge cases."""
        d = {
            "query": query,
            "total_indexed_chunks": total_indexed,
            "vector_results_count": 0,
            "bm25_results_count": 0,
            "vector_bm25_overlap": 0,
            "agreement_rate": 0.0,
            "avg_cosine_similarity": None,
            "max_cosine_similarity": None,
            "avg_bm25_score": None,
            "max_bm25_score": None,
            "avg_rrf_score": None,
            "source_diversity": 0,
            "sources": [],
            "results_returned": 0,
            "latency_ms": {"embedding": 0, "vector_search": 0, "bm25_search": 0, "fusion": 0, "total": 0},
            "per_result_debug": [],
        }
        if error:
            d["error"] = error
        return d

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
