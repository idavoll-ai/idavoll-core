from __future__ import annotations

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage


def _estimate_tokens(text: str) -> int:
    """
    Rough token estimate for mixed CJK / Latin content.

    Uses len // 3 as a conservative approximation:
    - Latin: ~4 chars/token → len//4 would undercount
    - CJK:   ~1-2 chars/token → need a tighter bound
    """
    return max(1, len(text) // 3)


class ContextWindow:
    """
    Stores conversation history and formats it for a specific agent's perspective.

    In a multi-agent session there is no single "user" and "assistant" — instead,
    each agent sees other agents' messages as HumanMessages (prefixed with the
    speaker's name) and its own past messages as AIMessages.
    """

    def __init__(self, max_messages: int = 20) -> None:
        self.max_messages = max_messages
        # Each entry: (agent_id, agent_name, content)
        self._history: list[tuple[str, str, str]] = []

    def add(self, agent_id: str, agent_name: str, content: str) -> None:
        self._history.append((agent_id, agent_name, content))
        if len(self._history) > self.max_messages:
            self._history = self._history[-self.max_messages :]

    def get_for_agent(self, agent_id: str) -> list[BaseMessage]:
        """
        Return history formatted from the given agent's point of view:
        - Own past messages → AIMessage
        - Others' messages  → HumanMessage("[Name]: content")
        """
        messages: list[BaseMessage] = []
        for aid, aname, content in self._history:
            if aid == agent_id:
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=f"[{aname}]: {content}"))
        return messages

    def get_for_agent_with_budget(
        self, agent_id: str, token_budget: int
    ) -> list[BaseMessage]:
        """
        Return as many recent messages as fit within `token_budget`.

        Fills from newest to oldest (recency priority), then re-orders
        chronologically before returning so the model sees correct order.
        """
        selected: list[tuple[str, str, str]] = []
        remaining = token_budget

        for aid, aname, content in reversed(self._history):
            cost = _estimate_tokens(content)
            if cost > remaining:
                break
            selected.append((aid, aname, content))
            remaining -= cost

        # Restore chronological order
        selected.reverse()

        messages: list[BaseMessage] = []
        for aid, aname, content in selected:
            if aid == agent_id:
                messages.append(AIMessage(content=content))
            else:
                messages.append(HumanMessage(content=f"[{aname}]: {content}"))
        return messages

    def get_raw(self) -> list[tuple[str, str, str]]:
        return list(self._history)

    def clear(self) -> None:
        self._history.clear()

    def __len__(self) -> int:
        return len(self._history)
