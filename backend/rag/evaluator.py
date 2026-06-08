"""
LLM-as-Judge Evaluator for RAG outputs.

Scores:
  - Faithfulness (0-1): Is the answer grounded in the retrieved context?
  - Answer Relevancy (0-1): Does the answer address the user's question?
  - Context Precision (0-1): Are the top-ranked chunks actually relevant?

Uses the same OpenAI key already configured in the project.
"""

import logging
import json
import time
from openai import OpenAI

logger = logging.getLogger(__name__)

FAITHFULNESS_PROMPT = """You are an impartial evaluator. Given a user QUESTION, the CONTEXT (retrieved document chunks), and the AI ANSWER, score the answer's faithfulness.

Faithfulness = the degree to which EVERY claim in the ANSWER is supported by the CONTEXT.
- 1.0 = every claim is directly supported by the context
- 0.5 = some claims are supported, some are not
- 0.0 = the answer is entirely fabricated / not grounded in context

Respond with ONLY a JSON object: {{"score": <float 0-1>, "reasoning": "<1-2 sentence explanation>"}}

QUESTION: {question}

CONTEXT:
{context}

ANSWER: {answer}"""

RELEVANCY_PROMPT = """You are an impartial evaluator. Given a user QUESTION and the AI ANSWER, score the answer's relevancy.

Relevancy = the degree to which the ANSWER directly addresses what the user asked.
- 1.0 = the answer fully and directly addresses the question
- 0.5 = the answer partially addresses the question or includes irrelevant info
- 0.0 = the answer does not address the question at all

Respond with ONLY a JSON object: {{"score": <float 0-1>, "reasoning": "<1-2 sentence explanation>"}}

QUESTION: {question}

ANSWER: {answer}"""

CONTEXT_PRECISION_PROMPT = """You are an impartial evaluator. Given a user QUESTION and a list of RETRIEVED CHUNKS, score how many of the top-ranked chunks are actually relevant to answering the question.

Context Precision = the proportion of retrieved chunks that are relevant.
- 1.0 = all retrieved chunks are relevant to the question
- 0.5 = about half of the chunks are relevant
- 0.0 = none of the chunks are relevant

Respond with ONLY a JSON object: {{"score": <float 0-1>, "reasoning": "<1-2 sentence explanation>", "relevant_chunks": [<list of 1-indexed positions that are relevant>]}}

QUESTION: {question}

RETRIEVED CHUNKS:
{chunks}"""

CONTEXT_RECALL_PROMPT = """You are an impartial evaluator. Given a user QUESTION, the CONTEXT (retrieved document chunks), and the AI ANSWER, score the context recall.

Context Recall = the degree to which the retrieved CONTEXT is sufficient to answer the QUESTION.
- 1.0 = context contains all information needed
- 0.5 = context contains some information but is missing key parts
- 0.0 = context is completely useless for answering the question

Respond with ONLY a JSON object: {{"score": <float 0-1>, "reasoning": "<1-2 sentence explanation>"}}

QUESTION: {question}

CONTEXT:
{context}"""

ANSWER_CORRECTNESS_PROMPT = """You are an impartial evaluator. Given a user QUESTION, the CONTEXT (retrieved document chunks), and the AI ANSWER, score the answer's factual correctness.

Answer Correctness = the degree to which the ANSWER is factually correct based on the CONTEXT and free of hallucinations or logical errors.
- 1.0 = fully correct and accurate
- 0.5 = partially correct or contains minor errors
- 0.0 = completely incorrect or major hallucination

Respond with ONLY a JSON object: {{"score": <float 0-1>, "reasoning": "<1-2 sentence explanation>"}}

QUESTION: {question}

CONTEXT:
{context}

ANSWER: {answer}"""


class RAGEvaluator:
    """Evaluate RAG outputs using LLM-as-judge."""

    def __init__(self, model: str = "gpt-4o-mini"):
        self.client = OpenAI()
        self.model = model
        self._eval_history: list[dict] = []

    def _call_judge(self, prompt: str) -> dict:
        """Make a single judge call and parse the JSON response."""
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0,
                max_tokens=200,
            )
            text = response.choices[0].message.content.strip()
            # Extract JSON from response (handle markdown code blocks)
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            return json.loads(text)
        except json.JSONDecodeError:
            logger.warning(f"Judge returned non-JSON: {text[:200]}")
            return {"score": None, "reasoning": "Failed to parse judge response"}
        except Exception as e:
            logger.error(f"Judge call failed: {e}")
            return {"score": None, "reasoning": f"Judge error: {str(e)}"}

    def evaluate(
        self,
        question: str,
        answer: str,
        context_chunks: list[dict],
    ) -> dict:
        """
        Run all three evaluation metrics.

        Args:
            question: The user's original query
            answer: The LLM's generated answer
            context_chunks: List of {text, filename, page} dicts from RAG retrieval

        Returns:
            {
                faithfulness: {score, reasoning},
                relevancy: {score, reasoning},
                context_precision: {score, reasoning, relevant_chunks},
                overall_score: float (weighted average),
                latency_ms: float
            }
        """
        t_start = time.perf_counter()

        # Format context for prompts
        context_text = "\n\n".join(
            f"[Chunk {i+1} — {c.get('filename', '?')}, page {c.get('page', '?')}]\n{c.get('text', '')[:500]}"
            for i, c in enumerate(context_chunks)
        )

        chunks_text = "\n\n".join(
            f"Chunk {i+1} (from {c.get('filename', '?')}, page {c.get('page', '?')}):\n{c.get('text', '')[:300]}"
            for i, c in enumerate(context_chunks)
        )

        # Run evaluations
        faithfulness = self._call_judge(
            FAITHFULNESS_PROMPT.format(question=question, context=context_text, answer=answer)
        )
        relevancy = self._call_judge(
            RELEVANCY_PROMPT.format(question=question, answer=answer)
        )
        context_precision = self._call_judge(
            CONTEXT_PRECISION_PROMPT.format(question=question, chunks=chunks_text)
        )
        context_recall = self._call_judge(
            CONTEXT_RECALL_PROMPT.format(question=question, context=context_text)
        )
        answer_correctness = self._call_judge(
            ANSWER_CORRECTNESS_PROMPT.format(question=question, context=context_text, answer=answer)
        )

        t_end = time.perf_counter()

        # Compute overall weighted score
        scores = []
        for metric in [faithfulness, relevancy, context_precision, context_recall, answer_correctness]:
            if metric.get("score") is not None:
                scores.append(metric["score"])
        overall = round(sum(scores) / len(scores), 4) if scores else None

        result = {
            "faithfulness": faithfulness,
            "relevancy": relevancy,
            "context_precision": context_precision,
            "context_recall": context_recall,
            "answer_correctness": answer_correctness,
            "overall_score": overall,
            "eval_latency_ms": round((t_end - t_start) * 1000, 1),
            "timestamp": time.time(),
            "question": question,
        }

        # Store in history (keep last 50)
        self._eval_history.append(result)
        if len(self._eval_history) > 50:
            self._eval_history = self._eval_history[-50:]

        return result

    def get_history(self) -> list[dict]:
        """Return evaluation history."""
        return list(self._eval_history)

    def get_summary(self) -> dict:
        """Return aggregate stats from evaluation history."""
        if not self._eval_history:
            return {"total_evals": 0}

        faith_scores = [e["faithfulness"]["score"] for e in self._eval_history if e["faithfulness"].get("score") is not None]
        rel_scores = [e["relevancy"]["score"] for e in self._eval_history if e["relevancy"].get("score") is not None]
        cp_scores = [e["context_precision"]["score"] for e in self._eval_history if e["context_precision"].get("score") is not None]
        cr_scores = [e.get("context_recall", {}).get("score") for e in self._eval_history if e.get("context_recall", {}).get("score") is not None]
        ac_scores = [e.get("answer_correctness", {}).get("score") for e in self._eval_history if e.get("answer_correctness", {}).get("score") is not None]
        overall_scores = [e["overall_score"] for e in self._eval_history if e.get("overall_score") is not None]

        def _avg(lst):
            return round(sum(lst) / len(lst), 4) if lst else None

        return {
            "total_evals": len(self._eval_history),
            "avg_faithfulness": _avg(faith_scores),
            "avg_relevancy": _avg(rel_scores),
            "avg_context_precision": _avg(cp_scores),
            "avg_context_recall": _avg(cr_scores),
            "avg_answer_correctness": _avg(ac_scores),
            "avg_overall": _avg(overall_scores),
        }
