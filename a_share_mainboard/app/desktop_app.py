from __future__ import annotations

import json
import queue
import threading
import traceback
from datetime import date, timedelta
from typing import Any, Callable

import tkinter as tk
from tkinter import ttk

from app._bootstrap import bootstrap_paths

PROJECT_ROOT = bootstrap_paths()

from app.launcher import (
    run_daily_cli,
    run_rebuild_cli,
    run_validate_cli,
    run_validate_rolling_cli,
)
from infra.config.loader import load_settings


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("A-Share TradingAgents Lite")
        self.geometry("1180x760")
        self.minsize(1080, 700)

        self.settings = load_settings(PROJECT_ROOT / "config")
        self.event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.run_buttons: list[ttk.Button] = []
        self.status_var = tk.StringVar(value="Idle")

        self.daily_trade_date_var = tk.StringVar(value=date.today().isoformat())
        self.validate_start_var = tk.StringVar(
            value=(date.today() - timedelta(days=90)).isoformat()
        )
        self.validate_end_var = tk.StringVar(value=date.today().isoformat())
        self.validate_horizon_var = tk.StringVar(value=str(self.settings.strategy.primary_horizon or 10))

        self.rolling_start_var = tk.StringVar(
            value=(date.today() - timedelta(days=140)).isoformat()
        )
        self.rolling_end_var = tk.StringVar(value=date.today().isoformat())
        self.rolling_horizon_var = tk.StringVar(value=str(self.settings.strategy.primary_horizon or 10))
        self.rolling_window_var = tk.StringVar(value="20")
        self.rolling_step_var = tk.StringVar(value="5")

        self.rebuild_start_var = tk.StringVar(
            value=(date.today() - timedelta(days=180)).isoformat()
        )
        self.rebuild_end_var = tk.StringVar(value=date.today().isoformat())

        self.log_text: tk.Text | None = None
        self._build_layout()
        self._render_settings_summary()
        self.after(200, self._drain_event_queue)

    def _build_layout(self) -> None:
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=0)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(0, weight=1)

        control_panel = ttk.Frame(root)
        control_panel.grid(row=0, column=0, sticky="nsw", padx=(0, 12))

        output_panel = ttk.Frame(root)
        output_panel.grid(row=0, column=1, sticky="nsew")
        output_panel.rowconfigure(2, weight=1)
        output_panel.columnconfigure(0, weight=1)

        self._build_daily_card(control_panel)
        self._build_validate_card(control_panel)
        self._build_rolling_card(control_panel)
        self._build_rebuild_card(control_panel)

        ttk.Label(
            output_panel,
            textvariable=self.status_var,
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, sticky="w")

        self.summary_label = ttk.Label(output_panel, text="", justify=tk.LEFT)
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(4, 8))

        log_frame = ttk.Frame(output_panel)
        log_frame.grid(row=2, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        text_widget = tk.Text(log_frame, wrap="word", font=("Consolas", 10))
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text_widget.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scroll.set)
        self.log_text = text_widget
        self._append_log("Desktop launcher initialized.")

    def _build_daily_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Daily Workflow", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="Trade Date").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.daily_trade_date_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="Run Daily",
            command=lambda: self._run_async(
                task_name="daily",
                runner=run_daily_cli,
                kwargs={"trade_date": self.daily_trade_date_var.get().strip()},
            ),
        )
        button.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.run_buttons.append(button)

    def _build_validate_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Single Validation", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="Start Date").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="End Date").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="Horizon").grid(row=2, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_horizon_var, width=16).grid(
            row=2, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="Run Validate",
            command=lambda: self._run_async(
                task_name="validate",
                runner=run_validate_cli,
                kwargs={
                    "start_date": self.validate_start_var.get().strip(),
                    "end_date": self.validate_end_var.get().strip(),
                    "horizon": int(self.validate_horizon_var.get().strip()),
                },
            ),
        )
        button.grid(row=3, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.run_buttons.append(button)

    def _build_rolling_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="Rolling Validation", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="Start Date").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="End Date").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="Horizon").grid(row=2, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_horizon_var, width=16).grid(
            row=2, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="Window").grid(row=3, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_window_var, width=16).grid(
            row=3, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="Step").grid(row=4, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_step_var, width=16).grid(
            row=4, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="Run Rolling",
            command=lambda: self._run_async(
                task_name="validate_rolling",
                runner=run_validate_rolling_cli,
                kwargs={
                    "start_date": self.rolling_start_var.get().strip(),
                    "end_date": self.rolling_end_var.get().strip(),
                    "horizon": int(self.rolling_horizon_var.get().strip()),
                    "window_size": int(self.rolling_window_var.get().strip()),
                    "step_size": int(self.rolling_step_var.get().strip()),
                },
            ),
        )
        button.grid(row=5, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.run_buttons.append(button)

    def _build_rebuild_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="History Rebuild", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="Start Date").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rebuild_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="End Date").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rebuild_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="Run Rebuild",
            command=lambda: self._run_async(
                task_name="rebuild",
                runner=run_rebuild_cli,
                kwargs={
                    "start_date": self.rebuild_start_var.get().strip(),
                    "end_date": self.rebuild_end_var.get().strip(),
                },
            ),
        )
        button.grid(row=2, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.run_buttons.append(button)

    def _render_settings_summary(self) -> None:
        profile = self.settings.strategy.strategy_profile()
        summary = (
            f"Primary horizon: {profile.get('primary_horizon')}D | "
            f"Auxiliary: {profile.get('auxiliary_horizons')} | "
            f"AI enabled: {self.settings.ai.enabled} | "
            f"Feishu enabled: {self.settings.feishu.enabled} | "
            f"Log root: {self.settings.app.log_root}"
        )
        self.summary_label.configure(text=summary)

    def _run_async(
        self,
        *,
        task_name: str,
        runner: Callable[..., dict[str, Any]],
        kwargs: dict[str, Any],
    ) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._append_log("Another run is in progress. Wait for it to finish.")
            return

        self._set_buttons_enabled(False)
        self.status_var.set(f"Running: {task_name}")
        self._append_log(f"Starting {task_name} with args: {kwargs}")

        def worker() -> None:
            try:
                result = runner(event_sink=self._runtime_event_sink, **kwargs)
                self.event_queue.put({"kind": "result", "task": task_name, "result": result})
            except Exception as exc:
                self.event_queue.put(
                    {
                        "kind": "error",
                        "task": task_name,
                        "message": str(exc),
                        "traceback": traceback.format_exc(),
                    }
                )

        self.worker_thread = threading.Thread(target=worker, daemon=True)
        self.worker_thread.start()

    def _runtime_event_sink(self, event: dict[str, Any]) -> None:
        self.event_queue.put({"kind": "event", "event": event})

    def _drain_event_queue(self) -> None:
        while True:
            try:
                item = self.event_queue.get_nowait()
            except queue.Empty:
                break
            self._handle_event_item(item)
        self.after(200, self._drain_event_queue)

    def _handle_event_item(self, item: dict[str, Any]) -> None:
        kind = item.get("kind")
        if kind == "event":
            self._append_runtime_event(item.get("event", {}))
            return
        if kind == "result":
            task_name = item.get("task", "task")
            result = item.get("result", {})
            self._append_log(
                f"{task_name} finished.\n{json.dumps(result, ensure_ascii=False, indent=2)}"
            )
            self.status_var.set(f"Completed: {task_name}")
            self._set_buttons_enabled(True)
            return
        if kind == "error":
            task_name = item.get("task", "task")
            self._append_log(
                f"{task_name} failed: {item.get('message')}\n{item.get('traceback', '')}"
            )
            self.status_var.set(f"Failed: {task_name}")
            self._set_buttons_enabled(True)

    def _append_runtime_event(self, event: dict[str, Any]) -> None:
        event_type = event.get("event") or event.get("type", "event")
        stamp = event.get("timestamp_utc", "")
        run_id = event.get("run_id", "")
        module = event.get("module", "")
        level = event.get("level", "")
        message = event.get("message", "")
        payload = event.get("payload", {})
        payload_text = _compact_payload(payload)

        if module:
            line = f"{stamp} [{level}] {module} ({run_id}) {message} {payload_text}".strip()
        else:
            line = f"{stamp} [{event_type}] {run_id} {message}".strip()
        self._append_log(line)

    def _append_log(self, line: str) -> None:
        if self.log_text is None:
            return
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.run_buttons:
            button.configure(state=state)


def _compact_payload(payload: Any, limit: int = 280) -> str:
    if not payload:
        return ""
    try:
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = str(payload)
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def main() -> None:
    app = DesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
