import os
import hashlib
import logging
import time
from typing import Optional, List, Dict, Any

from openai import OpenAI
import fitz
import io
from PIL import Image

from .parsers import DocumentParserFactory
from .chunker import TableAwareChunker
from .search import HybridSearchEngine, QueryExpander, OpenAIReRanker

logger = logging.getLogger(__name__)

class RAGEngine:
    """Expert-Level Hybrid RAG engine using ChromaDB (vector) + BM25 (keyword) + ReRanking."""

    def __init__(self, persist_dir: str):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        
        self._client = None
        self.openai_client = OpenAI()
        self.hybrid_search_engine = HybridSearchEngine()
        self.query_expander = QueryExpander(self.openai_client)
        self.reranker = OpenAIReRanker(self.openai_client)
        self.chunker = TableAwareChunker()
        
        # In-memory LRU cache for PDF page images could be added here
        self._pdf_page_cache = {}
        
        # Load all workspaces BM25 indexes lazily when searched
        logger.info(f"Expert RAG Engine initialized with storage at {persist_dir}")

    @property
    def client(self):
        if self._client is None:
            import chromadb
            self._client = chromadb.PersistentClient(path=self.persist_dir)
        return self._client

    def _collection_name(self, workspace_id: str) -> str:
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
        if not texts:
            return []
        response = self.openai_client.embeddings.create(
            model="text-embedding-3-large",
            input=texts,
        )
        return [item.embedding for item in response.data]

    def _embed_query(self, query: str) -> list[float]:
        return self._embed_texts([query])[0]

    # ── Ingestion ────────────────────────────────────────────────────────

    def ingest_file(self, workspace_id: str, filename: str, file_path: str) -> dict:
        if not os.path.exists(file_path):
            return {"filename": filename, "chunks_created": 0, "status": "file_not_found"}

        try:
            # 1. Parse document using multi-parser strategy
            sections = DocumentParserFactory.parse(file_path)
            if not sections:
                return {"filename": filename, "chunks_created": 0, "status": "no_text_extracted"}
                
            # 2. Chunk document (table-aware)
            chunks = self.chunker.chunk(sections, filename, workspace_id)
            if not chunks:
                return {"filename": filename, "chunks_created": 0, "status": "no_chunks"}

            # Remove any existing chunks for this file
            self.delete_file_index(workspace_id, filename)

            all_chunks = [c.text for c in chunks]
            all_ids = [c.id for c in chunks]
            all_metadatas = [c.metadata for c in chunks]

            # 3. Generate embeddings
            embeddings = self._embed_texts(all_chunks)

            # 4. Store in ChromaDB
            collection = self._get_collection(workspace_id)
            batch_size = 100
            for i in range(0, len(all_chunks), batch_size):
                end = min(i + batch_size, len(all_chunks))
                collection.add(
                    ids=all_ids[i:end],
                    documents=all_chunks[i:end],
                    embeddings=embeddings[i:end],
                    metadatas=all_metadatas[i:end],
                )

            # 5. Invalidate BM25 cache
            self.hybrid_search_engine.bm25_indices.pop(workspace_id, None)

            logger.info(f"Indexed {filename}: {len(all_chunks)} chunks")
            return {"filename": filename, "chunks_created": len(all_chunks), "status": "success"}
            
        except Exception as e:
            logger.error(f"Ingestion failed for {filename}: {e}")
            return {"filename": filename, "chunks_created": 0, "status": f"error: {str(e)}"}

    def ingest_folder(self, workspace_id: str, folder_path: str) -> dict:
        results = []
        total_chunks = 0
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                res = self.ingest_file(workspace_id, file, file_path)
                results.append(res)
                total_chunks += res.get("chunks_created", 0)
        return {"files_processed": len(results), "total_chunks": total_chunks, "results": results}

    def delete_file_index(self, workspace_id: str, filename: str):
        collection = self._get_collection(workspace_id)
        try:
            results = collection.get(where={"filename": filename})
            if results["ids"]:
                collection.delete(ids=results["ids"])
                self.hybrid_search_engine.bm25_indices.pop(workspace_id, None)
                logger.info(f"Deleted {len(results['ids'])} chunks for {filename}")
        except Exception as e:
            logger.warning(f"Error deleting index for {filename}: {e}")

    # ── Chunk CRUD ───────────────────────────────────────────────────────

    def get_chunks(self, workspace_id: str, document_name: Optional[str] = None, page: int = 1, per_page: int = 50) -> dict:
        collection = self._get_collection(workspace_id)
        where_clause = {"filename": document_name} if document_name else None
        
        # Note: ChromaDB get doesn't natively support pagination easily, we get all and slice
        results = collection.get(where=where_clause, include=["documents", "metadatas"])
        
        total = len(results["ids"])
        start_idx = (page - 1) * per_page
        end_idx = start_idx + per_page
        
        chunks = []
        for i in range(start_idx, min(end_idx, total)):
            chunks.append({
                "id": results["ids"][i],
                "text": results["documents"][i],
                "metadata": results["metadatas"][i]
            })
            
        return {"total": total, "page": page, "per_page": per_page, "chunks": chunks}

    def get_chunk(self, workspace_id: str, chunk_id: str) -> Optional[dict]:
        collection = self._get_collection(workspace_id)
        result = collection.get(ids=[chunk_id], include=["documents", "metadatas"])
        if result["ids"]:
            return {
                "id": result["ids"][0],
                "text": result["documents"][0],
                "metadata": result["metadatas"][0]
            }
        return None

    def update_chunk(self, workspace_id: str, chunk_id: str, new_text: str) -> bool:
        collection = self._get_collection(workspace_id)
        result = collection.get(ids=[chunk_id], include=["metadatas"])
        if not result["ids"]:
            return False
            
        # Re-embed the new text
        embedding = self._embed_texts([new_text])[0]
        
        # Update in ChromaDB
        collection.update(
            ids=[chunk_id],
            documents=[new_text],
            embeddings=[embedding],
            metadatas=[result["metadatas"][0]]
        )
        
        # Invalidate BM25 cache
        self.hybrid_search_engine.bm25_indices.pop(workspace_id, None)
        return True

    def delete_chunk(self, workspace_id: str, chunk_id: str) -> bool:
        collection = self._get_collection(workspace_id)
        collection.delete(ids=[chunk_id])
        self.hybrid_search_engine.bm25_indices.pop(workspace_id, None)
        return True

    def re_embed_all(self, workspace_id: str) -> dict:
        """Re-embed all chunks (useful when changing embedding models)."""
        collection = self._get_collection(workspace_id)
        all_data = collection.get(include=["documents", "metadatas"])
        
        if not all_data["documents"]:
            return {"status": "success", "chunks_processed": 0}
            
        embeddings = self._embed_texts(all_data["documents"])
        
        batch_size = 100
        for i in range(0, len(all_data["documents"]), batch_size):
            end = min(i + batch_size, len(all_data["documents"]))
            collection.update(
                ids=all_data["ids"][i:end],
                documents=all_data["documents"][i:end],
                embeddings=embeddings[i:end],
                metadatas=all_data["metadatas"][i:end]
            )
            
        return {"status": "success", "chunks_processed": len(all_data["documents"])}

    # ── Search ───────────────────────────────────────────────────────────

    def _ensure_bm25_index(self, workspace_id: str):
        if workspace_id not in self.hybrid_search_engine.bm25_indices:
            collection = self._get_collection(workspace_id)
            all_data = collection.get(include=["documents", "metadatas"])
            if all_data["documents"]:
                chunks = []
                for i in range(len(all_data["ids"])):
                    chunks.append({
                        "id": all_data["ids"][i],
                        "text": all_data["documents"][i],
                        "metadata": all_data["metadatas"][i]
                    })
                self.hybrid_search_engine.rebuild_bm25_index(workspace_id, chunks)

    def search(self, workspace_id: str, query: str, top_k: int = 8, expand_query: bool = True, use_reranker: bool = True) -> dict:
        t_start = time.perf_counter()
        collection = self._get_collection(workspace_id)
        total_indexed = collection.count()

        if total_indexed == 0:
            return {"results": [], "diagnostics": self._empty_diagnostics(query, 0)}

        n_candidates = min(30, total_indexed)
        self._ensure_bm25_index(workspace_id)

        # 1. Query Expansion
        t_expand_start = time.perf_counter()
        queries = self.query_expander.expand(query) if expand_query else [query]
        t_expand_end = time.perf_counter()

        # 2. Vector Search (multi-query)
        t_vec_start = time.perf_counter()
        vector_results_raw = []
        for q in queries:
            try:
                emb = self._embed_query(q)
                res = collection.query(
                    query_embeddings=[emb],
                    n_results=n_candidates,
                    include=["documents", "metadatas", "distances"]
                )
                if res["ids"] and res["ids"][0]:
                    for i, doc_id in enumerate(res["ids"][0]):
                        vector_results_raw.append({
                            "id": doc_id,
                            "text": res["documents"][0][i],
                            "metadata": res["metadatas"][0][i],
                            "cosine_distance": res["distances"][0][i],
                            "score": 1.0 - res["distances"][0][i]  # cosine similarity
                        })
            except Exception as e:
                logger.error(f"Vector search error for query '{q}': {e}")
        
        # Deduplicate vector results by taking highest score
        vector_dedup = {}
        for r in vector_results_raw:
            if r["id"] not in vector_dedup or r["score"] > vector_dedup[r["id"]]["score"]:
                vector_dedup[r["id"]] = r
        
        vector_results = sorted(list(vector_dedup.values()), key=lambda x: x["score"], reverse=True)
        t_vec_end = time.perf_counter()

        # 3. BM25 Search (multi-query)
        t_bm25_start = time.perf_counter()
        bm25_results_raw = []
        for q in queries:
            bm25_results_raw.extend(self.hybrid_search_engine.bm25_search(workspace_id, q, top_k=n_candidates))
            
        # Deduplicate bm25 results
        bm25_dedup = {}
        for r in bm25_results_raw:
            if r["id"] not in bm25_dedup or r["bm25_score"] > bm25_dedup[r["id"]]["bm25_score"]:
                bm25_dedup[r["id"]] = r
                
        bm25_results = sorted(list(bm25_dedup.values()), key=lambda x: x["bm25_score"], reverse=True)
        t_bm25_end = time.perf_counter()

        # 4. Fusion
        t_fusion_start = time.perf_counter()
        fused_results = self.hybrid_search_engine.reciprocal_rank_fusion(vector_results, bm25_results)
        t_fusion_end = time.perf_counter()

        # 5. Re-ranking
        t_rerank_start = time.perf_counter()
        if use_reranker and len(fused_results) > 0:
            final_results = self.reranker.rerank(query, fused_results[:n_candidates], top_k=top_k)
        else:
            final_results = fused_results[:top_k]
        t_rerank_end = time.perf_counter()

        t_end = time.perf_counter()

        # Format output
        output_results = []
        per_result_debug = []
        for r in final_results:
            output_results.append({
                "id": r["id"],
                "text": r["text"],
                "filename": r.get("metadata", {}).get("filename", ""),
                "page": r.get("metadata", {}).get("page", 1),
                "score": r.get("rerank_score", r.get("rrf_score", 0.0))
            })
            
            found_by = []
            if r.get("found_by_vector"): found_by.append("vector")
            if r.get("found_by_bm25"): found_by.append("bm25")
            
            per_result_debug.append({
                "id": r["id"],
                "filename": r.get("metadata", {}).get("filename", ""),
                "page": r.get("metadata", {}).get("page", 1),
                "cosine_similarity": r.get("score"),
                "bm25_score": r.get("bm25_score"),
                "rrf_score": r.get("rrf_score"),
                "rerank_score": r.get("rerank_score"),
                "found_by": found_by
            })

        # Calculate metrics
        cosine_scores = [r["score"] for r in vector_results if "score" in r]
        bm25_scores = [r["bm25_score"] for r in bm25_results if "bm25_score" in r]
        
        vector_ids = {r["id"] for r in vector_results}
        bm25_ids = {r["id"] for r in bm25_results}
        overlap = len(vector_ids.intersection(bm25_ids))
        union_len = len(vector_ids.union(bm25_ids))
        agreement_rate = overlap / union_len if union_len > 0 else 0.0
        
        source_files = {r.get("filename", "") for r in output_results}

        diagnostics = {
            "query": query,
            "expanded_queries": queries,
            "total_indexed_chunks": total_indexed,
            "results_returned": len(output_results),
            "vector_results_count": len(vector_results),
            "bm25_results_count": len(bm25_results),
            "vector_bm25_overlap": overlap,
            "agreement_rate": agreement_rate,
            "avg_cosine_similarity": sum(cosine_scores)/len(cosine_scores) if cosine_scores else 0.0,
            "max_cosine_similarity": max(cosine_scores) if cosine_scores else 0.0,
            "avg_bm25_score": sum(bm25_scores)/len(bm25_scores) if bm25_scores else 0.0,
            "max_bm25_score": max(bm25_scores) if bm25_scores else 0.0,
            "source_diversity": len(source_files),
            "per_result_debug": per_result_debug,
            "latency_ms": {
                "expansion": round((t_expand_end - t_expand_start) * 1000, 1),
                "vector_search": round((t_vec_end - t_vec_start) * 1000, 1),
                "bm25_search": round((t_bm25_end - t_bm25_start) * 1000, 1),
                "fusion": round((t_fusion_end - t_fusion_start) * 1000, 1),
                "rerank": round((t_rerank_end - t_rerank_start) * 1000, 1),
                "total": round((t_end - t_start) * 1000, 1),
            }
        }

        return {"results": output_results, "diagnostics": diagnostics}

    def _empty_diagnostics(self, query: str, total_indexed: int, error: str = None) -> dict:
        d = {
            "query": query,
            "total_indexed_chunks": total_indexed,
            "results_returned": 0,
            "latency_ms": {"total": 0},
        }
        if error:
            d["error"] = error
        return d

    # ── File info ────────────────────────────────────────────────────────

    def get_indexed_files(self, workspace_id: str) -> list[dict]:
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
        return self._get_collection(workspace_id).count()

    # ── PDF Image Rendering ──────────────────────────────────────────────

    def render_pdf_page(self, file_path: str, page_number: int, highlight_text: Optional[str] = None) -> Optional[bytes]:
        """Render a specific PDF page to a PNG image byte string."""
        cache_key = f"{file_path}_{page_number}_{hash(highlight_text)}"
        if cache_key in self._pdf_page_cache:
            return self._pdf_page_cache[cache_key]
            
        if not os.path.exists(file_path):
            return None
            
        try:
            doc = fitz.open(file_path)
            # PyMuPDF is 0-indexed, our page_number is 1-indexed
            if page_number < 1 or page_number > len(doc):
                return None
                
            page = doc.load_page(page_number - 1)
            
            # Apply highlights if requested
            if highlight_text:
                # Split text into lines to maximize match probability
                lines = [line.strip() for line in highlight_text.split('\n') if len(line.strip()) > 4]
                if not lines and highlight_text.strip():
                    lines = [highlight_text.strip()]
                    
                for line in lines:
                    instances = page.search_for(line)
                    for inst in instances:
                        # Draw a yellow semi-transparent rectangle
                        page.draw_rect(inst, color=(1, 1, 0), fill=(1, 1, 0), fill_opacity=0.3, width=0)
            
            # Render at 150 DPI for good web display
            pix = page.get_pixmap(dpi=150)
            
            # Convert to PNG
            img_data = pix.tobytes("png")
            
            # Cache it
            self._pdf_page_cache[cache_key] = img_data
            
            # Simple cache eviction if too large
            if len(self._pdf_page_cache) > 100:
                self._pdf_page_cache.pop(next(iter(self._pdf_page_cache)))
                
            return img_data
        except Exception as e:
            logger.error(f"Failed to render PDF page {file_path}:{page_number} - {e}")
            return None
