"""DeepEval metric definitions. Imported lazily — DeepEval is an optional CI dep."""

from typing import Any


def build_metrics() -> list[Any]:
    from deepeval.metrics import (  # type: ignore
        AnswerRelevancyMetric,
        ContextualRecallMetric,
        FaithfulnessMetric,
        GEval,
    )
    from deepeval.test_case import LLMTestCaseParams  # type: ignore

    tone = GEval(
        name="ToneMatch",
        criteria=(
            "Reads like a confident senior engineer talking about Chetan in third person. "
            "No marketing fluff, no emojis, 1-3 short sentences."
        ),
        evaluation_params=[LLMTestCaseParams.ACTUAL_OUTPUT],
        threshold=0.7,
    )
    return [
        FaithfulnessMetric(threshold=0.85),
        AnswerRelevancyMetric(threshold=0.80),
        ContextualRecallMetric(threshold=0.70),
        tone,
    ]
