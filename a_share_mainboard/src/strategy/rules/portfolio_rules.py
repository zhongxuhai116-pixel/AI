from __future__ import annotations


class PortfolioRuleSet:
    def __init__(self, top_n: int) -> None:
        self.top_n = top_n

    def apply(self, payload):
        return payload

