from __future__ import annotations


class ExecutionModel:
    def __init__(self, execution_price_mode: str, cost_bps: float) -> None:
        self.execution_price_mode = execution_price_mode
        self.cost_bps = cost_bps

    def calculate_trade_return(self, *, entry_bar: dict, exit_bar: dict) -> float:
        entry_price = self._resolve_entry_price(entry_bar)
        exit_price = self._resolve_exit_price(exit_bar)
        gross_return = (exit_price / entry_price) - 1.0
        total_cost = (self.cost_bps * 2.0) / 10_000.0
        return gross_return - total_cost

    def _resolve_entry_price(self, bar: dict) -> float:
        if self.execution_price_mode == "next_open":
            return self._coerce_price(bar.get("open"))
        return self._coerce_price(bar.get("close"))

    @staticmethod
    def _resolve_exit_price(bar: dict) -> float:
        return ExecutionModel._coerce_price(bar.get("close"))

    @staticmethod
    def _coerce_price(value: float | int | None) -> float:
        price = float(value or 0.0)
        if price <= 0:
            raise ValueError("Encountered a non-positive execution price.")
        return price
