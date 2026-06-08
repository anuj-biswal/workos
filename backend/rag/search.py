import logging
from typing import List, Dict, Any, Tuple
from rank_bm25 import BM25Okapi
from openai import OpenAI
import json

# sentence-transformers is imported lazily in ReRanker

logger = logging.getLogger(__name__)

class QueryExpander:
    def __init__(self, openai_client: OpenAI, model: str = "gpt-4o-mini"):
        self.client = openai_client
        self.model = model

    def expand(self, query: str) -> List[str]:
        prompt = f"""You are a search query expansion expert.
Given the user's question, generate 3 alternative search queries that would help retrieve the most relevant documents.
Include synonyms, related terms, or rephrase the question to capture the core intent.
Return your answer as a JSON array of strings.

User Question: {query}
"""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=150,
                response_format={ "type": "json_object" }
            )
            content = response.choices[0].message.content
            # Handle if the response wraps the array in an object
            data = json.loads(content)
            if isinstance(data, list):
                expanded_queries = data
            elif isinstance(data, dict):
                # find the first list value
                expanded_queries = next((v for v in data.values() if isinstance(v, list)), [])
            else:
                expanded_queries = []
                
            return [query] + [q for q in expanded_queries if isinstance(q, str)]
        except Exception as e:
            logger.error(f"Query expansion failed: {e}")
            return [query]

class OpenAIReRanker:
    def __init__(self, openai_client: OpenAI, model: str = "gpt-4o-mini"):
        self.client = openai_client
        self.model = model

    def rerank(self, query: str, results: List[Dict[str, Any]], top_k: int = 5) -> List[Dict[str, Any]]:
        if not results:
            return results[:top_k]

        # Prepare prompt for batch scoring
        chunks_text = "\n\n".join([f"Chunk {i+1}:\n{res['text']}" for i, res in enumerate(results)])
        prompt = f"""You are a relevance scoring expert. Given a user query and a list of document chunks, score each chunk's relevance to answering the query on a scale of 0 to 10.
- 10: The chunk perfectly answers the query.
- 5: The chunk is somewhat relevant but incomplete.
- 0: The chunk is completely irrelevant.

Respond with ONLY a JSON array of floats corresponding to the scores for each chunk in order. Example: [8.5, 2.0, 0.0]

User Query: {query}

{chunks_text}"""

        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=200,
            )
            content = response.choices[0].message.content.strip()
            
            # Extract JSON from response
            if content.startswith("```"):
                content = content.split("```")[1]
                if content.startswith("json"):
                    content = content[4:]
                content = content.strip()
            
            scores = json.loads(content)
            
            if not isinstance(scores, list) or len(scores) != len(results):
                logger.warning("Reranker returned malformed scores. Falling back to original ordering.")
                return results[:top_k]
                
            for i, res in enumerate(results):
                # Normalize to 0-1 range
                res["rerank_score"] = min(max(float(scores[i]) / 10.0, 0.0), 1.0)
                
            results.sort(key=lambda x: x["rerank_score"], reverse=True)
            return results[:top_k]
            
        except Exception as e:
            logger.error(f"OpenAI re-ranking failed: {e}")
            return results[:top_k]

class HybridSearchEngine:
    def __init__(self):
        self.bm25_indices: Dict[str, BM25Okapi] = {}
        self.corpus_chunks: Dict[str, List[Dict[str, Any]]] = {}

    def rebuild_bm25_index(self, workspace_id: str, chunks: List[Dict[str, Any]]):
        self.corpus_chunks[workspace_id] = chunks
        if not chunks:
            self.bm25_indices[workspace_id] = None
            return
            
        # Simple tokenization for BM25
        tokenized_corpus = [c["text"].lower().split() for c in chunks]
        self.bm25_indices[workspace_id] = BM25Okapi(tokenized_corpus)

    def bm25_search(self, workspace_id: str, query: str, top_k: int = 20) -> List[Dict[str, Any]]:
        index = self.bm25_indices.get(workspace_id)
        corpus = self.corpus_chunks.get(workspace_id)
        
        if not index or not corpus:
            return []
            
        tokenized_query = query.lower().split()
        scores = index.get_scores(tokenized_query)
        
        # Sort and get top_k
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        
        results = []
        for i in top_indices:
            if scores[i] > 0:
                result = corpus[i].copy()
                result["bm25_score"] = float(scores[i])
                results.append(result)
                
        return results
        
    @staticmethod
    def reciprocal_rank_fusion(vector_results: List[Dict[str, Any]], bm25_results: List[Dict[str, Any]], alpha: float = 0.5, k: int = 60) -> List[Dict[str, Any]]:
        """
        Custom fusion combining RRF principles with alpha weighting.
        We expect vector_results to have a 'cosine_score' or 'score' and bm25_results to have 'bm25_score'.
        """
        combined_scores = {}
        items = {}
        
        # Process vector results
        for rank, item in enumerate(vector_results):
            item_id = item["id"]
            if item_id not in combined_scores:
                combined_scores[item_id] = 0
                items[item_id] = item.copy()
            
            # Using alpha weight for vector scores
            score = alpha * (1 / (k + rank + 1))
            combined_scores[item_id] += score
            items[item_id]["found_by_vector"] = True
            
        # Process BM25 results
        for rank, item in enumerate(bm25_results):
            item_id = item["id"]
            if item_id not in combined_scores:
                combined_scores[item_id] = 0
                items[item_id] = item.copy()
            
            # Using (1-alpha) weight for BM25 scores
            score = (1.0 - alpha) * (1 / (k + rank + 1))
            combined_scores[item_id] += score
            items[item_id]["found_by_bm25"] = True
            
        # Sort by combined score
        for item_id, score in combined_scores.items():
            items[item_id]["rrf_score"] = score
            
        final_results = list(items.values())
        final_results.sort(key=lambda x: x["rrf_score"], reverse=True)
        return final_results
