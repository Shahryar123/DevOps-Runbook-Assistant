"""Fixed replies for blocked questions.

Like NO_ANSWER, these are returned without any LLM call, so they cost nothing
and can't be talked into saying something else.
"""

BLOCKED_REPLIES: dict[str, str] = {
    "too_long": (
        "I can't process a question that long. Please shorten it to the main "
        "symptom, the error message and the system involved."
    ),
    "moderation": (
        "I can't help with that request. I only answer DevOps troubleshooting "
        "questions from the team's runbooks."
    ),
    "injection": (
        "I can't follow instructions that try to change how I work. Please ask "
        "a DevOps troubleshooting question instead."
    ),
}

OFF_TOPIC_REPLY = (
    "I can only help with DevOps troubleshooting questions (servers, deployments, "
    "networking, certificates, pipelines). Please ask about a technical problem."
)

ALL_FIXED_REPLIES: tuple[str, ...] = (*BLOCKED_REPLIES.values(), OFF_TOPIC_REPLY)
