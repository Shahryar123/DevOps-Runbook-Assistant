"""Application settings loaded from environment variables and the project .env file."""

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py lives in src/runbook_assistant/, so the project root is two levels up.
PROJECT_ROOT: Path = Path(__file__).resolve().parents[2]


class CommandRule(BaseModel):
    """A risky command to warn about when it appears in an answer."""

    name: str
    pattern: str  # regex, matched case-insensitively
    severity: Literal["high", "medium"]
    warning: str  # one line: what running it does


# --- Guardrail pattern lists ------------------------------------------------------
# Kept here (not in the guardrail modules) so they can be tuned without code changes.

# Secrets: if a pattern has a (?P<secret>...) group, only that part is masked
# (e.g. "Password=[MASKED_SECRET]"); otherwise the whole match is.
# Values starting with <, $, %, { or [ are placeholders/variables, not secrets.
SECRET_PATTERNS: dict[str, str] = {
    "private_key": (
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----[\s\S]*?"
        r"(?:-----END (?:[A-Z0-9]+ )*PRIVATE KEY-----|\Z)"
    ),
    "jwt": r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}",
    "bearer_token": r"(?i)\bbearer\s+(?P<secret>(?![<$%{\[])[A-Za-z0-9\-._~+/]{16,}=*)",
    "openai_key": r"\bsk-(?:proj-|svcacct-|admin-)?[A-Za-z0-9_-]{20,}",
    "aws_access_key": r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
    "azure_account_key": (
        r"(?i)\bAccountKey\s*=\s*(?P<secret>(?![<$%{\[])[A-Za-z0-9+/=]{20,})"
    ),
    "connection_string_password": (
        r"(?i)\b(?:password|pwd)\s*=\s*(?P<secret>(?![<$%{\[])[^;\s'\"]{4,})"
    ),
    # Generic "key: value" pairs. To avoid false positives ("the token: expired")
    # the value must be 6+ characters and contain a digit or symbol.
    "generic_secret": (
        r"(?i)\b(?:passwd|password|secret|client_secret|token|access_token|"
        r"api[_-]?key)\s*[:=]\s*['\"]?(?P<secret>(?![<$%{\[])"
        r"(?=[^\s;'\",]*[0-9!@#$%^&*_+\-])[^\s;'\",]{6,})"
    ),
}

DANGEROUS_COMMANDS: list[CommandRule] = [
    CommandRule(
        name="Remove-Item -Recurse",
        pattern=r"\bRemove-Item\b[^\n]*-Recurse",
        severity="high",
        warning="deletes a folder and everything in it; there is no recycle bin.",
    ),
    CommandRule(
        name="rm -rf",
        pattern=r"\brm\s+-[a-z]*(?:rf|fr)[a-z]*\b",
        severity="high",
        warning="deletes files and folders recursively without asking.",
    ),
    CommandRule(
        name="iisreset",
        pattern=r"\biisreset\b",
        severity="medium",
        warning="restarts IIS and interrupts every site on the server, not just one.",
    ),
    CommandRule(
        name="Restart-Computer",
        pattern=r"\bRestart-Computer\b",
        severity="high",
        warning="reboots the whole server; everything on it goes down.",
    ),
    CommandRule(
        name="Stop-Service",
        pattern=r"\bStop-Service\b",
        severity="medium",
        warning="stops the service (and services that depend on it) until restarted.",
    ),
    CommandRule(
        name="format",
        pattern=r"\b(?:Format-Volume\b|format\s+[a-z]:)",
        severity="high",
        warning="erases everything on the drive.",
    ),
    CommandRule(
        name="DROP TABLE/DATABASE",
        pattern=r"\bDROP\s+(?:TABLE|DATABASE)\b",
        severity="high",
        warning="permanently deletes the table or database and all its data.",
    ),
    CommandRule(
        name="DELETE without WHERE",
        pattern=r"\bDELETE\s+FROM\s+[\w.\[\]\"`]+\s*(?:;|$)",
        severity="high",
        warning="deletes every row in the table because there is no WHERE clause.",
    ),
    CommandRule(
        name="terraform destroy",
        pattern=r"\bterraform\s+destroy\b",
        severity="high",
        warning="deletes all infrastructure managed by this Terraform state.",
    ),
    CommandRule(
        name="az group delete",
        pattern=r"\baz\s+group\s+delete\b",
        severity="high",
        warning="deletes the resource group and every resource inside it.",
    ),
    CommandRule(
        name="aws ec2 terminate-instances",
        pattern=r"\baws\s+ec2\s+terminate-instances\b",
        severity="high",
        warning="permanently deletes the instances; this cannot be undone.",
    ),
    CommandRule(
        name="kubectl delete",
        pattern=r"\bkubectl\s+delete\b",
        severity="medium",
        warning="removes the Kubernetes resources; their workloads stop running.",
    ),
    CommandRule(
        name="git push --force",
        pattern=r"\bgit\s+push\b[^\n]*(?:--force(?!-with-lease)\b|\s-f\b)",
        severity="medium",
        warning="overwrites the remote branch history; others can lose commits.",
    ),
]

# Prompt injection: phrases, not single words, so "how do I act on event 5002"
# or "configure IIS to act as a reverse proxy" are not flagged.
INJECTION_PATTERNS: list[str] = [
    (
        r"\b(?:ignore|disregard|forget|override|bypass)\s+(?:all\s+|any\s+)?"
        r"(?:of\s+)?(?:the\s+|your\s+|my\s+|these\s+)?"
        r"(?:previous\s+|prior\s+|above\s+|earlier\s+|system\s+)?"
        r"(?:instructions|rules|prompts?|guidelines|directions|guardrails)\b"
    ),
    r"\byou\s+are\s+now\s+(?:a|an|in|my|no\s+longer|DAN)\b",
    r"\bsystem\s+prompt\b",
    (
        r"\b(?:reveal|show|print|repeat|output|display|tell\s+me)\s+(?:me\s+)?"
        r"(?:your|the)\s+(?:initial\s+|hidden\s+)?(?:instructions|prompt)\b"
    ),
    (
        r"\b(?:act|behave)\s+as\s+(?:if\s+you\s+(?:are|were|have)|my\s+|"
        r"an?\s+(?:unrestricted|unfiltered|jailbroken|different|new)\b)"
    ),
    r"\bpretend\s+(?:to\s+be|you\s+are)\b",
    r"\b(?:jailbreak|DAN\s+mode|developer\s+mode)\b",
    r"\bnew\s+instructions\s*:",
    r"</?\s*(?:system|runbook_context)\s*>",
]


class Settings(BaseSettings):
    """Typed settings. Field names map to env vars case-insensitively
    (e.g. ``openai_api_key`` <- ``OPENAI_API_KEY``)."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Secrets: SecretStr hides the value in repr/str/logs.
    openai_api_key: SecretStr

    # LangSmith tracing
    langsmith_tracing: bool = False
    langsmith_api_key: SecretStr | None = None
    langsmith_project: str = "runbook-assistant"
    # Web UI used for trace links (EU accounts: https://eu.smith.langchain.com).
    langsmith_web_url: str = "https://smith.langchain.com"

    # Models
    answer_model: str = "gpt-5.4-mini"
    router_model: str = "gpt-5.4-mini"
    grader_model: str = "gpt-5.4-mini"
    rewrite_model: str = "gpt-5.4-mini"
    judge_model: str = "gpt-5.4-mini"
    embedding_model: str = "text-embedding-3-small"

    # Paths
    runbooks_dir: Path = PROJECT_ROOT / "data" / "runbooks"
    chroma_dir: Path = PROJECT_ROOT / "chroma_db"
    chroma_collection: str = "runbooks"

    # Ingest / retrieval
    chunk_size: int = 800
    chunk_overlap: int = 100
    retriever_k: int = 4

    # Topics: the single source of truth. Keys are valid runbook `topic` values;
    # descriptions are shown to the router so it can classify questions.
    runbook_topics: dict[str, str] = {
        "deployment": "Application hosting and releases: IIS sites and app pools, "
        "web servers, apps crashing or stopping after a deploy.",
        "networking": "Connectivity: DNS, firewalls, security groups, routing, "
        "servers or VMs that cannot be reached.",
        "security": "SSL/TLS certificates, authentication, secrets, "
        "permissions and access errors.",
        "cicd": "Build and release pipelines: Azure DevOps, GitHub Actions, "
        "agents/runners, failing pipeline steps.",
    }
    # Router fallback when no topic fits; never valid as a runbook topic.
    general_topic: str = "general"
    # Router choice for questions that are not about IT at all (no retrieval).
    off_topic_topic: str = "off_topic"

    # LangGraph feature flags: switch setups without code changes
    routing_enabled: bool = True
    grading_enabled: bool = True
    max_retries: int = 1
    graph_run_name: str = "runbook-graph"
    graph_tags: list[str] = ["milestone-4", "graph"]
    graph_diagram_path: Path = PROJECT_ROOT / "docs" / "graph.md"
    chain_run_name: str = "runbook-chain"

    # Streamlit UI
    ui_title: str = "DevOps Runbook Assistant"
    ui_default_setup: str = "graph_routing"
    ui_tags: list[str] = ["ui"]  # added to UI runs, to filter them in LangSmith
    max_question_chars: int = 500
    feedback_key: str = "user_rating"

    # Guardrails: each one can be switched off (e.g. for eval ablations)
    secrets_masking_enabled: bool = True
    command_warnings_enabled: bool = True
    injection_check_enabled: bool = True
    injection_classifier_enabled: bool = True  # LLM check after the keyword check
    moderation_enabled: bool = True
    off_topic_enabled: bool = True
    rate_limit_enabled: bool = True

    secret_mask: str = "[MASKED_SECRET]"
    secret_patterns: dict[str, str] = SECRET_PATTERNS
    dangerous_commands: list[CommandRule] = DANGEROUS_COMMANDS
    injection_patterns: list[str] = INJECTION_PATTERNS
    injection_model: str = "gpt-5.4-mini"
    moderation_model: str = "omni-moderation-latest"

    # Rate limits (sliding windows, plus a daily cap that resets at midnight UTC)
    rate_limit_window_s: int = 600
    rate_limit_session_max: int = 10  # per browser session
    rate_limit_ip_max: int = 30  # per client IP (several people can share one IP)
    rate_limit_daily_cap: int = 200  # all users together: a cost safety net
    # Only behind a trusted reverse proxy: take the client IP from X-Forwarded-For.
    rate_limit_trust_forwarded_for: bool = False

    # Evaluation (LangSmith)
    eval_dataset_name: str = "runbook-assistant-eval"
    eval_dataset_path: Path = PROJECT_ROOT / "evals" / "dataset.jsonl"
    eval_max_concurrency: int = 4
    eval_categories: list[str] = [
        "clear",
        "vague",
        "out_of_scope",
        "multi_runbook",
        "adversarial",
    ]

    @property
    def router_topics(self) -> tuple[str, ...]:
        """Every topic the router may choose: runbook topics, the general
        fallback and (if enabled) off_topic."""
        extra = (self.off_topic_topic,) if self.off_topic_enabled else ()
        return (*self.runbook_topics, self.general_topic, *extra)


@lru_cache
def get_settings() -> Settings:
    """Return a single cached Settings instance."""
    return Settings()


def configure_tracing(settings: Settings) -> None:
    """Export LangSmith settings to os.environ.

    pydantic-settings reads .env into the Settings object but does not set
    environment variables. LangChain's LangSmith integration reads LANGSMITH_*
    from os.environ, so we copy them there explicitly.
    """
    if not settings.langsmith_tracing or settings.langsmith_api_key is None:
        os.environ["LANGSMITH_TRACING"] = "false"
        return
    os.environ["LANGSMITH_TRACING"] = "true"
    os.environ["LANGSMITH_API_KEY"] = settings.langsmith_api_key.get_secret_value()
    os.environ["LANGSMITH_PROJECT"] = settings.langsmith_project
