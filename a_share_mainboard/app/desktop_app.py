from __future__ import annotations

import json
import math
import queue
import threading
import traceback
from datetime import date, datetime, timedelta
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
from data.storage.duckdb_client import DuckDBClient
from data.storage.repositories import ResearchRepository
from infra.config.loader import load_settings
from infra.exceptions import RuntimeBusyError


class DesktopApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("A股 TradingAgents Lite")
        self.geometry("1260x820")
        self.minsize(1140, 760)

        self.settings = load_settings(PROJECT_ROOT / "config")
        self.event_queue: queue.Queue[dict[str, Any]] = queue.Queue()
        self.worker_thread: threading.Thread | None = None
        self.current_task: str | None = None
        self.run_buttons: list[ttk.Button] = []

        self.status_var = tk.StringVar(value="空闲")
        self.progress_var = tk.DoubleVar(value=0.0)
        self.progress_text_var = tk.StringVar(value="进度：0%")
        self.reco_var = tk.StringVar(value="推荐股票：暂无数据")

        self.daily_trade_date_var = tk.StringVar(value=date.today().isoformat())
        self.validate_start_var = tk.StringVar(value=(date.today() - timedelta(days=90)).isoformat())
        self.validate_end_var = tk.StringVar(value=date.today().isoformat())
        self.validate_horizon_var = tk.StringVar(value=str(self.settings.strategy.primary_horizon or 10))

        self.rolling_start_var = tk.StringVar(value=(date.today() - timedelta(days=140)).isoformat())
        self.rolling_end_var = tk.StringVar(value=date.today().isoformat())
        self.rolling_horizon_var = tk.StringVar(value=str(self.settings.strategy.primary_horizon or 10))
        self.rolling_window_var = tk.StringVar(value="20")
        self.rolling_step_var = tk.StringVar(value="5")

        self.rebuild_start_var = tk.StringVar(value=(date.today() - timedelta(days=180)).isoformat())
        self.rebuild_end_var = tk.StringVar(value=date.today().isoformat())

        self.summary_label: ttk.Label | None = None
        self.log_text: tk.Text | None = None
        self.reco_tree: ttk.Treeview | None = None
        self.progress_bar: ttk.Progressbar | None = None

        self._build_layout()
        self._render_settings_summary()
        self._load_latest_recommendations_from_db()
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
        output_panel.columnconfigure(0, weight=1)
        output_panel.rowconfigure(4, weight=1)

        self._build_daily_card(control_panel)
        self._build_validate_card(control_panel)
        self._build_rolling_card(control_panel)
        self._build_rebuild_card(control_panel)

        top_bar = ttk.Frame(output_panel)
        top_bar.grid(row=0, column=0, sticky="ew")
        top_bar.columnconfigure(1, weight=1)
        ttk.Label(top_bar, textvariable=self.status_var, font=("Segoe UI", 11, "bold")).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Label(top_bar, textvariable=self.progress_text_var, font=("Segoe UI", 10)).grid(
            row=0, column=2, sticky="e"
        )
        progress_bar = ttk.Progressbar(
            top_bar,
            orient=tk.HORIZONTAL,
            mode="determinate",
            maximum=100,
            variable=self.progress_var,
        )
        progress_bar.grid(row=0, column=1, sticky="ew", padx=10)
        self.progress_bar = progress_bar

        self.summary_label = ttk.Label(output_panel, text="", justify=tk.LEFT)
        self.summary_label.grid(row=1, column=0, sticky="w", pady=(4, 8))

        reco_frame = ttk.LabelFrame(output_panel, text="推荐股票清单（Daily）", padding=6)
        reco_frame.grid(row=2, column=0, sticky="nsew", pady=(0, 8))
        reco_frame.rowconfigure(1, weight=1)
        reco_frame.columnconfigure(0, weight=1)
        ttk.Label(reco_frame, textvariable=self.reco_var, font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, sticky="w", pady=(0, 4)
        )
        self._build_recommendation_table(reco_frame)

        action_row = ttk.Frame(output_panel)
        action_row.grid(row=3, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(action_row, text="刷新推荐", command=self._load_latest_recommendations_from_db).pack(
            side=tk.LEFT
        )

        log_frame = ttk.Frame(output_panel)
        log_frame.grid(row=4, column=0, sticky="nsew")
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        text_widget = tk.Text(log_frame, wrap="word", font=("Consolas", 10))
        text_widget.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=text_widget.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        text_widget.configure(yscrollcommand=scroll.set)
        self.log_text = text_widget
        self._append_log("桌面程序已启动。")

    def _build_recommendation_table(self, parent: ttk.Frame) -> None:
        columns = ("seq", "role", "horizon", "symbol", "name", "rank", "weight", "tags")
        tree = ttk.Treeview(parent, columns=columns, show="headings", height=9)
        tree.grid(row=1, column=0, sticky="nsew")
        headings = {
            "seq": ("序号", 54),
            "role": ("主辅", 70),
            "horizon": ("周期D", 70),
            "symbol": ("代码", 90),
            "name": ("名称", 120),
            "rank": ("排名", 70),
            "weight": ("权重", 80),
            "tags": ("标签", 280),
        }
        for key, (title, width) in headings.items():
            tree.heading(key, text=title)
            tree.column(key, width=width, stretch=True, anchor=tk.W)
        scroll = ttk.Scrollbar(parent, orient=tk.VERTICAL, command=tree.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        tree.configure(yscrollcommand=scroll.set)
        self.reco_tree = tree

    def _build_daily_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="每日流程", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="交易日").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.daily_trade_date_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="运行 Daily",
            command=lambda: self._run_async(
                task_name="daily",
                runner=run_daily_cli,
                kwargs={"trade_date": self.daily_trade_date_var.get().strip()},
            ),
        )
        button.grid(row=1, column=0, columnspan=2, sticky="we", pady=(8, 0))
        self.run_buttons.append(button)

    def _build_validate_card(self, parent: ttk.Frame) -> None:
        card = ttk.LabelFrame(parent, text="区间验证", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="开始日期").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="结束日期").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="周期").grid(row=2, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.validate_horizon_var, width=16).grid(
            row=2, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="运行验证",
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
        card = ttk.LabelFrame(parent, text="滚动验证", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="开始日期").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="结束日期").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="周期").grid(row=2, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_horizon_var, width=16).grid(
            row=2, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="窗口").grid(row=3, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_window_var, width=16).grid(
            row=3, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="步长").grid(row=4, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rolling_step_var, width=16).grid(
            row=4, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="运行滚动",
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
        card = ttk.LabelFrame(parent, text="历史回补", padding=10)
        card.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(card, text="开始日期").grid(row=0, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rebuild_start_var, width=16).grid(
            row=0, column=1, sticky="we", padx=(8, 0)
        )
        ttk.Label(card, text="结束日期").grid(row=1, column=0, sticky="w")
        ttk.Entry(card, textvariable=self.rebuild_end_var, width=16).grid(
            row=1, column=1, sticky="we", padx=(8, 0)
        )
        button = ttk.Button(
            card,
            text="运行回补",
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
            f"主周期: {profile.get('primary_horizon')}D | "
            f"辅周期: {profile.get('auxiliary_horizons')} | "
            f"AI启用: {_bool_cn(self.settings.ai.enabled)} | "
            f"飞书启用: {_bool_cn(self.settings.feishu.enabled)} | "
            f"日志目录: {self.settings.app.log_root}"
        )
        if self.summary_label is not None:
            self.summary_label.configure(text=summary)

    def _run_async(
        self,
        *,
        task_name: str,
        runner: Callable[..., dict[str, Any]],
        kwargs: dict[str, Any],
    ) -> None:
        if self.worker_thread and self.worker_thread.is_alive():
            self._append_log("当前已有任务在运行，请等待完成。")
            return

        self.current_task = task_name
        self._set_progress(2.0, text=f"进度：2%（已提交 {task_name}）")
        self.status_var.set(f"运行中：{task_name}")
        self._append_log(f"开始执行 {task_name}，参数：{kwargs}")
        self._set_buttons_enabled(False)

        def worker() -> None:
            try:
                result = runner(event_sink=self._runtime_event_sink, **kwargs)
                self.event_queue.put({"kind": "result", "task": task_name, "result": result})
            except RuntimeBusyError as exc:
                self.event_queue.put({"kind": "busy", "task": task_name, "message": str(exc)})
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
            event = item.get("event", {})
            self._append_runtime_event(event)
            self._update_progress_from_event(event)
            return

        if kind == "result":
            task_name = item.get("task", "task")
            result = item.get("result", {})
            self._refresh_recommendations(task_name=task_name, result=result)
            self._append_log(f"{task_name} 执行完成。\n{json.dumps(result, ensure_ascii=False, indent=2)}")
            self.status_var.set(f"完成：{task_name}")
            self._set_progress(100.0, text="进度：100%（完成）")
            self.current_task = None
            self._set_buttons_enabled(True)
            return

        if kind == "busy":
            task_name = item.get("task", "task")
            self.status_var.set(f"忙：{task_name}")
            self._set_progress(0.0, text="进度：0%（等待中）")
            self._append_log(f"{task_name} 未执行：已有任务在运行。{item.get('message', '')}")
            self.current_task = None
            self._set_buttons_enabled(True)
            return

        if kind == "error":
            task_name = item.get("task", "task")
            self.status_var.set(f"失败：{task_name}")
            self._set_progress(0.0, text="进度：0%（失败）")
            self._append_log(f"{task_name} 执行失败：{item.get('message', '')}")
            self._append_log(item.get("traceback", ""))
            self.current_task = None
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

    def _update_progress_from_event(self, event: dict[str, Any]) -> None:
        if self.current_task is None:
            return

        run_event = str(event.get("event", ""))
        if run_event == "RUN_STARTED":
            self._set_progress(max(self.progress_var.get(), 5.0))
            return
        if run_event == "RUN_FINISHED":
            self._set_progress(100.0, text="进度：100%（完成）")
            return

        module = str(event.get("module", ""))
        message = str(event.get("message", ""))
        progress = _resolve_progress(task=self.current_task, module=module, message=message)
        if progress is None:
            progress = min(95.0, self.progress_var.get() + 1.5)
        self._set_progress(progress)

    def _set_progress(self, value: float, text: str | None = None) -> None:
        bounded = max(0.0, min(100.0, value))
        self.progress_var.set(bounded)
        if text is None:
            self.progress_text_var.set(f"进度：{int(round(bounded))}%")
        else:
            self.progress_text_var.set(text)

    def _append_log(self, line: str) -> None:
        if self.log_text is None:
            return
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)

    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.run_buttons:
            button.configure(state=state)

    def _refresh_recommendations(self, *, task_name: str, result: dict[str, Any]) -> None:
        if task_name != "daily":
            return
        top_signals = result.get("top_signals", [])
        if isinstance(top_signals, list) and top_signals:
            self._render_recommendations(
                rows=top_signals,
                trade_date=_to_date_text(result.get("effective_trade_date")),
                subtitle_prefix="推荐股票",
            )
            return
        self._render_recommendations(
            rows=result.get("top_candidates", []),
            trade_date=_to_date_text(result.get("effective_trade_date")),
            subtitle_prefix="候选推荐（无信号）",
        )

    def _load_latest_recommendations_from_db(self) -> None:
        db_path = PROJECT_ROOT / self.settings.data.duckdb_path
        if not db_path.exists():
            self.reco_var.set("推荐股票：暂无数据（请先运行 Daily）")
            return

        db_client = DuckDBClient(db_path)
        try:
            repo = ResearchRepository(db_client)
            latest_signals_df = repo.read_dataframe(
                """
                SELECT
                    s.trade_date,
                    s.symbol,
                    s.horizon,
                    s.final_rank,
                    s.target_weight,
                    s.rule_tags,
                    COALESCE(i.name, '') AS name
                FROM signals_daily s
                LEFT JOIN instrument_basic i ON i.symbol = s.symbol
                WHERE s.trade_date = (SELECT MAX(trade_date) FROM signals_daily)
                ORDER BY s.horizon, s.final_rank, s.symbol
                """
            )
            latest_scores_df = repo.read_dataframe(
                """
                SELECT
                    s.trade_date,
                    s.symbol,
                    s.horizon,
                    s.score_rank,
                    s.model_name,
                    COALESCE(i.name, '') AS name
                FROM model_scores_daily s
                LEFT JOIN instrument_basic i ON i.symbol = s.symbol
                WHERE s.trade_date = (SELECT MAX(trade_date) FROM model_scores_daily)
                ORDER BY s.horizon, s.score_rank, s.symbol
                """
            )
        except Exception as exc:
            self.reco_var.set("推荐股票：读取失败")
            self._append_log(f"加载推荐失败：{exc}")
            return
        finally:
            db_client.close()

        primary = self.settings.strategy.strategy_profile().get("primary_horizon")

        if not latest_signals_df.empty:
            latest_date = _to_date_text(latest_signals_df.iloc[0].get("trade_date"))
            rows = _format_rows(
                rows=latest_signals_df.to_dict(orient="records"),
                primary_horizon=primary,
                rank_key="final_rank",
                tags_key="rule_tags",
                role_signal=True,
                limit=20,
            )
            self._render_recommendations(rows=rows, trade_date=latest_date, subtitle_prefix="推荐股票")
            return

        if not latest_scores_df.empty:
            latest_date = _to_date_text(latest_scores_df.iloc[0].get("trade_date"))
            rows = _format_rows(
                rows=latest_scores_df.to_dict(orient="records"),
                primary_horizon=primary,
                rank_key="score_rank",
                tags_key="model_name",
                role_signal=False,
                limit=20,
            )
            self._render_recommendations(rows=rows, trade_date=latest_date, subtitle_prefix="候选推荐（无信号）")
            return

        self.reco_var.set("推荐股票：暂无数据（请先运行 Daily）")

    def _render_recommendations(self, *, rows: Any, trade_date: str, subtitle_prefix: str) -> None:
        if self.reco_tree is None:
            return
        for child in self.reco_tree.get_children():
            self.reco_tree.delete(child)

        if not isinstance(rows, list) or not rows:
            self.reco_var.set(f"{subtitle_prefix}：{trade_date or '未知'} 无候选")
            return

        primary = self.settings.strategy.strategy_profile().get("primary_horizon")
        self.reco_var.set(f"{subtitle_prefix}：{len(rows)} 只 | 交易日 {trade_date} | 主周期 {primary}D")
        for idx, row in enumerate(rows, start=1):
            weight = row.get("target_weight")
            self.reco_tree.insert(
                "",
                tk.END,
                values=(
                    idx,
                    row.get("role", ""),
                    row.get("horizon", ""),
                    row.get("symbol", ""),
                    row.get("name", ""),
                    row.get("final_rank", ""),
                    f"{float(weight):.2%}" if isinstance(weight, (float, int)) else "",
                    row.get("rule_tags", ""),
                ),
            )


def _resolve_progress(*, task: str, module: str, message: str) -> float | None:
    progress_maps: dict[str, dict[str, float]] = {
        "daily": {
            "Daily workflow started": 5.0,
            "Trade calendar refreshed": 12.0,
            "Instrument universe refreshed": 20.0,
            "Index history refreshed": 30.0,
            "Mainboard daily snapshot refreshed": 40.0,
            "Stock pool built": 52.0,
            "Features calculated": 63.0,
            "Market regime generated": 72.0,
            "Model scores generated": 80.0,
            "Signals generated": 88.0,
            "Validation snapshot generated": 94.0,
            "Daily report written": 97.0,
            "Feishu sync finished": 100.0,
        },
        "validate": {
            "Validation workflow started": 12.0,
            "Validation workflow completed": 100.0,
        },
        "validate_rolling": {
            "Rolling validation workflow started": 10.0,
            "Rolling validation workflow completed": 100.0,
        },
        "rebuild": {
            "Historical rebuild started": 12.0,
            "Historical rebuild completed": 100.0,
        },
    }
    by_task = progress_maps.get(task, {})
    if message in by_task:
        return by_task[message]
    if module == "daily_workflow" and message in progress_maps["daily"]:
        return progress_maps["daily"][message]
    return None


def _format_rows(
    *,
    rows: list[dict[str, Any]],
    primary_horizon: int | None,
    rank_key: str,
    tags_key: str,
    role_signal: bool,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0:
        return []

    formatted: list[dict[str, Any]] = []
    for row in rows:
        horizon = _safe_int(row.get("horizon"))
        role = "候选"
        if role_signal:
            role = "主" if primary_horizon is not None and horizon == int(primary_horizon) else "辅"
        formatted.append(
            {
                "symbol": str(row.get("symbol", "") or ""),
                "name": str(row.get("name", "") or ""),
                "horizon": horizon,
                "role": role,
                "final_rank": _safe_int(row.get(rank_key)),
                "target_weight": _safe_float(row.get("target_weight")),
                "rule_tags": str(row.get(tags_key, "") or ""),
            }
        )

    formatted.sort(
        key=lambda item: (
            0 if item.get("role") == "主" else 1,
            int(item.get("horizon") or 999),
            int(item.get("final_rank") or 9999),
            str(item.get("symbol") or ""),
        )
    )
    return formatted[:limit]


def _to_date_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if " " in text:
        return text.split(" ", 1)[0]
    return text


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


def _bool_cn(value: bool) -> str:
    return "是" if value else "否"


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def main() -> None:
    app = DesktopApp()
    app.mainloop()


if __name__ == "__main__":
    main()
