from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class StructuredReply:
    type: str = "text"
    text: str = ""
    buttons: list[dict[str, object] | str] = field(default_factory=list)
    list_items: list[dict[str, object] | str] = field(default_factory=list)
    flow: str | None = None
    state: str | None = None
    requires_manager: bool = False
    manager_alert: dict[str, object] | None = None
    events: list[dict[str, object]] = field(default_factory=list)
    metadata: dict[str, object] = field(default_factory=dict)
    next_flow: str | None = None
    flow_state: str | None = None

    def to_dict(self) -> dict[str, object]:
        flow = self.flow or self.next_flow
        state = self.state or self.flow_state
        return {
            "type": self.type,
            "text": self.text,
            "buttons": self.buttons,
            "list": self.list_items,
            "flow": flow,
            "state": state,
            "requires_manager": self.requires_manager,
            "manager_alert": self.manager_alert,
            "events": self.events,
            "metadata": self.metadata,
        }

    def to_text(self) -> str:
        return self.text

    def append_event(self, event: dict[str, object]) -> None:
        self.events.append(event)

    @property
    def next_flow_or_flow(self) -> str | None:
        return self.flow or self.next_flow

    @property
    def flow_or_state(self) -> str | None:
        return self.state or self.flow_state
