from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


ReviewTargetType = Literal["agent_in_topic", "post", "thread"]

_LEGACY_REVIEW_PLAN_FIELDS = {
    "use_lead_planner",
    "lead_planner_timeout_seconds",
    "lead_max_selected_roles",
    "reviewer_roles",
    "default_roles_for_agent_in_topic",
    "default_roles_for_post",
    "default_roles_for_thread",
}


class ReviewRoleConfig(BaseModel):
    """One reviewer role in the configurable review role catalog."""

    enabled: bool = Field(
        default=True,
        description="Whether this reviewer role can be selected by ReviewTeam.",
    )
    dimension: str = Field(
        ...,
        description="Dimension label written into reviewer output.",
    )
    criteria: str = Field(
        ...,
        description="Detailed reviewer instruction injected into the system prompt.",
    )
    target_types: list[ReviewTargetType] = Field(
        default_factory=lambda: ["agent_in_topic", "post", "thread"],
        description="Which review targets this role may be used for.",
    )
    timeout_seconds: float | None = Field(
        default=None,
        description="Optional per-role timeout override. Falls back to reviewer_timeout_seconds.",
    )


def default_review_role_catalog() -> dict[str, ReviewRoleConfig]:
    """Built-in reviewer roles used when the user does not override config."""
    return {
        "DepthReviewer": ReviewRoleConfig(
            dimension="depth",
            criteria=(
                "- 发言是否有清晰的论证结构\n"
                "- 是否提供了事实、案例或逻辑推理作为支撑\n"
                "- 是否只给出结论而不展开说明\n"
                "- 是否对复杂问题给出了有层次的分析"
            ),
            target_types=["agent_in_topic", "post", "thread"],
        ),
        "EngagementReviewer": ReviewRoleConfig(
            dimension="engagement",
            criteria=(
                "- 是否真正在与他人对话（而非独白）\n"
                "- 是否引用或回应了其他 Agent 的具体观点\n"
                "- 是否推进了讨论而非重复已有观点\n"
                "- 是否提出了有价值的问题或角度"
            ),
            target_types=["agent_in_topic", "post", "thread"],
        ),
        "SafetyReviewer": ReviewRoleConfig(
            dimension="safety",
            criteria=(
                "- 发言是否有越界或不当内容\n"
                "- 是否存在过度自信、武断、或错误事实\n"
                "- 是否产生了对讨论质量有害的风险\n"
                "（安全评分：10=完全安全，1=严重问题）"
            ),
            target_types=["agent_in_topic", "post", "thread"],
        ),
        "RelevanceReviewer": ReviewRoleConfig(
            dimension="relevance",
            criteria=(
                "- 发言是否紧扣当前话题主旨\n"
                "- 是否回应了这个话题里真正被讨论的问题\n"
                "- 是否出现大段偏题、跑题或只是在重复背景信息\n"
                "- 是否把讨论往核心议题上拉回来了"
            ),
            target_types=["agent_in_topic", "post", "thread"],
        ),
        "OriginalityReviewer": ReviewRoleConfig(
            dimension="originality",
            criteria=(
                "- 是否提出了新的角度、框架或类比\n"
                "- 是否只是重复已有观点，还是带来了新的组织方式\n"
                "- 是否能在常规表达之外提供有辨识度的见解\n"
                "- 创新是否建立在合理推理之上，而不是空洞求新"
            ),
            target_types=["agent_in_topic", "post"],
        ),
        "ThreadReviewer": ReviewRoleConfig(
            dimension="thread",
            criteria=(
                "- 这条帖子 / 这个分支是否真的形成了讨论链路\n"
                "- 是否引发了后续回复、澄清或新的推进\n"
                "- 是否只是热闹，但没有形成有效讨论\n"
                "- 是否把分支讨论推向更具体、更清晰的方向"
            ),
            target_types=["post", "thread"],
        ),
    }


def default_agent_review_roles() -> list[str]:
    return ["DepthReviewer", "EngagementReviewer", "SafetyReviewer"]


def default_post_review_roles() -> list[str]:
    return ["DepthReviewer", "EngagementReviewer", "SafetyReviewer"]


def default_thread_review_roles() -> list[str]:
    return ["DepthReviewer", "EngagementReviewer", "SafetyReviewer"]


class ReviewPlanConfig(BaseModel):
    """Planning-time config for reviewer selection and role catalog."""

    use_lead_planner: bool = Field(
        default=True,
        description=(
            "When True, ReviewTeam first asks the lead/orchestrator agent to choose "
            "which reviewer roles to spawn for the current target."
        ),
    )
    lead_planner_timeout_seconds: float = Field(
        default=15.0,
        description="Timeout for the lead-agent reviewer planning step.",
    )
    lead_max_selected_roles: int = Field(
        default=4,
        description="Soft upper bound for how many reviewer roles the lead planner should select.",
    )
    reviewer_roles: dict[str, ReviewRoleConfig] = Field(
        default_factory=default_review_role_catalog,
        description=(
            "Configurable reviewer role catalog. ReviewTeam selects a subset "
            "from this catalog for each target type."
        ),
    )
    default_roles_for_agent_in_topic: list[str] = Field(
        default_factory=default_agent_review_roles,
        description="Preferred reviewer roles when reviewing an agent across a topic.",
    )
    default_roles_for_post: list[str] = Field(
        default_factory=default_post_review_roles,
        description="Preferred reviewer roles when reviewing a single hot post.",
    )
    default_roles_for_thread: list[str] = Field(
        default_factory=default_thread_review_roles,
        description="Preferred reviewer roles when reviewing one discussion thread.",
    )

    @model_validator(mode="after")
    def _validate_role_selection(self) -> "ReviewPlanConfig":
        selections = {
            "default_roles_for_agent_in_topic": self.default_roles_for_agent_in_topic,
            "default_roles_for_post": self.default_roles_for_post,
            "default_roles_for_thread": self.default_roles_for_thread,
        }
        catalog_names = set(self.reviewer_roles.keys())
        for field_name, names in selections.items():
            missing = [name for name in names if name not in catalog_names]
            if missing:
                raise ValueError(
                    f"{field_name} contains unknown reviewer roles: {', '.join(missing)}"
                )
        return self


class ReviewConfig(BaseModel):
    """Settings for the ReviewPlugin.

    Weight semantics
    ----------------
    ``composite_weight`` governs the "content quality" dimension — either the
    LLM-evaluated score (when ``use_llm=True`` and the call succeeds) or the
    deterministic post-count formula used as a fallback.
    ``likes_weight`` governs community engagement (accumulated likes).
    The two must sum to 1.0.
    """

    max_post_chars: int = Field(
        default=3000,
        description="Max characters of agent posts fed into the LLM context per review call.",
    )
    composite_weight: float = Field(default=0.6)
    likes_weight: float = Field(default=0.4)
    use_llm: bool = Field(
        default=True,
        description=(
            "When True, the ReviewPlugin calls the LLM to produce multi-dimensional "
            "content scores.  Falls back to deterministic scoring on any LLM error."
        ),
    )
    min_score_for_memory_candidate: float = Field(
        default=7.0,
        description=(
            "Minimum final_score (0–10) to classify a review as a memory_candidate "
            "directive.  Scores below this threshold produce a reflection_candidate instead."
        ),
    )
    use_team: bool = Field(
        default=False,
        description=(
            "When True, use the multi-reviewer team approach (Phase 2). "
            "Each reviewer runs as an ephemeral subagent; a Moderator aggregates results. "
            "When False, falls back to the single-LLM review path."
        ),
    )
    reviewer_timeout_seconds: float = Field(
        default=30.0,
        description="Per-reviewer subagent timeout when use_team=True.",
    )
    hot_interaction_enabled: bool = Field(
        default=False,
        description=(
            "When True, ReviewPlugin listens for topic.post.liked events and "
            "triggers a targeted review when a post reaches the likes threshold."
        ),
    )
    hot_interaction_likes_threshold: int = Field(
        default=5,
        description=(
            "Minimum like count on a single post to trigger a HotInteractionReview. "
            "Only used when hot_interaction_enabled=True."
        ),
    )

    @model_validator(mode="after")
    def _weights_sum_to_one(self) -> "ReviewConfig":
        total = self.composite_weight + self.likes_weight
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                f"composite_weight + likes_weight must equal 1.0, got {total}"
            )
        return self


class TopicConfig(BaseModel):
    """Parameters for TopicPlugin — tuned for event-driven discussion."""

    num_seeds: int = Field(
        default=5,
        description="Number of initiative posts TopicPlugin publishes to seed the discussion",
    )
    seed_interval: float = Field(
        default=1.0,
        description="Seconds to wait between seed posts",
    )
    max_context_messages: int = Field(
        default=20,
        description="How many recent posts are visible to agents in their context window",
    )
    initiative_quota: int = Field(
        default=5,
        description="Max unprompted posts per agent per topic",
    )
    reply_quota: int = Field(
        default=10,
        description="Max reply posts per agent per topic",
    )
    cooldown_seconds: float = Field(
        default=0.0,
        description="Min seconds between any two posts by the same agent",
    )
    max_reply_depth: int = Field(
        default=3,
        description="Max reply chain depth; agents won't respond beyond this depth",
    )
    max_concurrent_responses: int = Field(
        default=3,
        description="Max agents that can respond to a single post simultaneously",
    )
    max_agents: int = Field(default=10, description="Hard cap on participants per topic")


class LevelingConfig(BaseModel):
    """Settings for the LevelingPlugin."""

    xp_per_point: int = Field(
        default=10,
        description="XP awarded per 1.0 of final_score",
    )
    base_xp_per_level: int = Field(
        default=100,
        description="XP needed to advance from level N to N+1: base * N",
    )
    budget_increment_per_level: int = Field(
        default=512,
        description="Tokens added to ContextBudget.total on each level-up",
    )


GrowthConfig = LevelingConfig


class VingolfConfig(BaseModel):
    """Top-level configuration for Vingolf plugins."""

    review: ReviewConfig = Field(default_factory=ReviewConfig)
    review_plan: ReviewPlanConfig = Field(default_factory=ReviewPlanConfig)
    topic: TopicConfig = Field(default_factory=TopicConfig)
    leveling: LevelingConfig = Field(default_factory=LevelingConfig)
    db_path: str = Field(
        default="vingolf.db",
        description="Path to the SQLite database file. Relative paths are resolved from CWD.",
    )

    @model_validator(mode="before")
    @classmethod
    def _support_aliases_and_legacy_review_plan(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "growth" in data and "leveling" not in data:
                data["leveling"] = data.pop("growth")

            review = data.get("review")
            if isinstance(review, dict):
                review = dict(review)
                legacy_plan = {}
                for key in list(review.keys()):
                    if key in _LEGACY_REVIEW_PLAN_FIELDS:
                        legacy_plan[key] = review.pop(key)
                if legacy_plan:
                    review_plan = data.get("review_plan")
                    merged_plan = dict(review_plan) if isinstance(review_plan, dict) else {}
                    for key, value in legacy_plan.items():
                        merged_plan.setdefault(key, value)
                    data["review_plan"] = merged_plan
                data["review"] = review
        return data

    @classmethod
    def from_yaml(cls, path: str | Path) -> "VingolfConfig":
        try:
            import yaml
        except ImportError as exc:
            raise ImportError(
                "pyyaml is required to load config from YAML. "
                "Install it with: pip install pyyaml"
            ) from exc

        path = Path(path)
        with open(path) as f:
            data = yaml.safe_load(f) or {}
        payload = data.get("vingolf", data)
        if not isinstance(payload, dict):
            payload = {}

        review_plan_path = cls._default_review_plan_path(path)
        if review_plan_path.exists():
            with open(review_plan_path) as f:
                review_plan_data = yaml.safe_load(f) or {}
            review_plan_payload = review_plan_data.get("vingolf", review_plan_data)
            if isinstance(review_plan_payload, dict):
                extracted = (
                    review_plan_payload.get("review_plan")
                    if isinstance(review_plan_payload.get("review_plan"), dict)
                    else review_plan_payload
                )
                payload = dict(payload)
                payload["review_plan"] = extracted

        return cls.model_validate(payload)

    @staticmethod
    def _default_review_plan_path(config_path: Path) -> Path:
        if config_path.name == "config.example.yaml":
            return config_path.with_name("review_plan.example.yaml")
        return config_path.with_name("review_plan.yaml")

    @classmethod
    def defaults(cls) -> "VingolfConfig":
        return cls()
