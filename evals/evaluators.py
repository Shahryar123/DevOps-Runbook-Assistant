"""Evaluators: score one (question, system output, reference) triple.

LangSmith calls each evaluator with arguments matched by name:
  inputs            -> {"question"}
  outputs           -> what the target returned: {"answer", "sources", "contexts"}
  reference_outputs -> {"reference_answer", "expected_sources"}
and records the returned {"key", "score", "comment"}. score=None means
"not applicable", so it is left out of averages.

An empty expected_sources list marks an out-of-scope question.
"""

from functools import lru_cache

from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import Runnable
from langchain_openai import ChatOpenAI
from pydantic import BaseModel, Field

from runbook_assistant.config import get_settings
from runbook_assistant.rag import is_refusal

EvalResult = dict[str, object]


def result(key: str, score: float | None, comment: str) -> EvalResult:
    return {"key": key, "score": score, "comment": comment}


def is_out_of_scope(reference_outputs: dict) -> bool:
    return not reference_outputs.get("expected_sources")


def run_failed(outputs: dict | None) -> bool:
    """True if the target crashed: LangSmith still calls evaluators, but with
    no answer. Scoring that would be wrong, so every evaluator skips it."""
    return not outputs or "answer" not in outputs


FAILED = "skipped: target run failed (see the run's error in LangSmith)"


# --- Code evaluators (deterministic, free) ----------------------------------


def retrieval_hit(outputs: dict, reference_outputs: dict) -> EvalResult:
    """Fraction of expected runbooks present in the chunks given to the model."""
    if run_failed(outputs):
        return result("retrieval_hit", None, FAILED)
    expected = reference_outputs.get("expected_sources") or []
    if not expected:
        return result("retrieval_hit", None, "skipped: out-of-scope question")

    found = set(outputs.get("sources", []))
    hits = [source for source in expected if source in found]
    missing = [source for source in expected if source not in found]
    comment = f"hit {len(hits)}/{len(expected)}"
    if missing:
        comment += f"; missing: {', '.join(missing)}"
    return result("retrieval_hit", len(hits) / len(expected), comment)


def correct_refusal(outputs: dict, reference_outputs: dict) -> EvalResult:
    """Out-of-scope questions must be refused; in-scope questions must not be."""
    if run_failed(outputs):
        return result("correct_refusal", None, FAILED)
    refused = is_refusal(outputs["answer"])
    out_of_scope = is_out_of_scope(reference_outputs)
    if out_of_scope:
        comment = (
            "correctly refused" if refused else "answered an out-of-scope question"
        )
    else:
        comment = "wrongly refused an in-scope question" if refused else "answered"
    return result("correct_refusal", float(refused == out_of_scope), comment)


# --- LLM-as-judge evaluators ------------------------------------------------


class JudgeScore(BaseModel):
    """A judge's verdict."""

    reason: str = Field(description="One or two sentences explaining the score")
    score: float = Field(description="Score from 0.0 (worst) to 1.0 (best)")


CORRECTNESS_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You grade a DevOps assistant's answer against reference key points
taken from the company's runbooks.

Score = the fraction of reference key points the answer covers (0.0 to 1.0).
- Paraphrases and equivalent commands count as covered.
- Extra correct detail is fine; do not judge style or length.
- A key point the answer contradicts counts as missed.
Give a short reason naming any missed key points.""",
        ),
        (
            "human",
            (
                "Question:\n{question}\n\nReference key points:\n{reference}"
                "\n\nAnswer to grade:\n{answer}"
            ),
        ),
    ]
)

GROUNDEDNESS_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            """You check whether a DevOps assistant's answer is grounded in the runbook
excerpts it was given.

Score = the fraction of the answer's factual claims (steps, commands, causes, facts)
that are supported by the excerpts (0.0 to 1.0). 1.0 means nothing is made up.
- Source citations and generic phrasing are not claims.
- Use only the excerpts; do not use your own knowledge to decide if a claim is true.
Give a short reason naming any unsupported claims.""",
        ),
        ("human", "Runbook excerpts:\n{contexts}\n\nAnswer to check:\n{answer}"),
    ]
)


@lru_cache
def get_judge() -> ChatOpenAI:
    settings = get_settings()
    return ChatOpenAI(model=settings.judge_model, api_key=settings.openai_api_key)


def judge(prompt: ChatPromptTemplate, values: dict) -> JudgeScore:
    chain: Runnable = prompt | get_judge().with_structured_output(JudgeScore)
    verdict: JudgeScore = chain.invoke(values)
    verdict.score = min(max(verdict.score, 0.0), 1.0)
    return verdict


def correctness(inputs: dict, outputs: dict, reference_outputs: dict) -> EvalResult:
    """Does the answer cover the reference answer's key points?"""
    if run_failed(outputs):
        return result("correctness", None, FAILED)
    if is_out_of_scope(reference_outputs):
        return result(
            "correctness", None, "skipped: out-of-scope (see correct_refusal)"
        )
    answer = outputs["answer"]
    if is_refusal(answer):
        return result("correctness", 0.0, "refused an in-scope question")

    verdict = judge(
        CORRECTNESS_PROMPT,
        {
            "question": inputs["question"],
            "reference": reference_outputs["reference_answer"],
            "answer": answer,
        },
    )
    return result("correctness", verdict.score, verdict.reason)


def groundedness(outputs: dict) -> EvalResult:
    """Is every claim in the answer supported by the chunks the model saw?"""
    if run_failed(outputs):
        return result("groundedness", None, FAILED)
    answer = outputs["answer"]
    if is_refusal(answer):
        return result("groundedness", None, "skipped: refusal makes no claims")
    contexts = outputs.get("contexts") or []
    if not contexts:
        return result("groundedness", 0.0, "answered without any retrieved context")

    verdict = judge(
        GROUNDEDNESS_PROMPT,
        {"contexts": "\n\n---\n\n".join(contexts), "answer": answer},
    )
    return result("groundedness", verdict.score, verdict.reason)


ALL_EVALUATORS = [retrieval_hit, correct_refusal, correctness, groundedness]
