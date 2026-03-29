from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from adapters.llm.openai_client import OpenAIClient
from adapters.market.akshare_provider import AKShareProvider
from ai.agents.market_summary_agent import MarketSummaryAgent
from ai.agents.stock_explainer_agent import StockExplainerAgent
from ai.prompts.loader import PromptLoader
from ai.services.ai_orchestrator import AIOrchestrator
from ai.services.ai_result_store import AIResultStore
from data.collectors.index_daily_collector import IndexDailyCollector
from data.collectors.instrument_collector import InstrumentCollector
from data.collectors.price_daily_collector import PriceDailyCollector
from data.collectors.trade_calendar_collector import TradeCalendarCollector
from data.features.feature_pipeline import FeaturePipeline
from data.filters.stock_pool_builder import StockPoolBuilder
from data.storage.duckdb_client import DuckDBClient
from data.storage.repositories import LogRepository, MarketRepository, ResearchRepository
from infra.config.settings import Settings
from infra.exceptions import ProviderUnavailableError
from infra.logging.run_logger import RunLogger
from infra.utils.dates import add_days
from strategy.market_scan.market_scan_service import MarketScanService
from strategy.policy.policy_overlay_service import PolicyOverlayService
from strategy.rules.rule_engine import RuleEngine
from strategy.secretary.feishu_sync_service import FeishuSyncService
from strategy.secretary.markdown_writer import MarkdownWriter
from strategy.secretary.report_context_builder import ReportContextBuilder
from strategy.secretary.secretary_service import SecretaryService
from strategy.stock_selection.baseline_ranker import BaselineRanker
from strategy.stock_selection.selection_service import SelectionService
from strategy.validation.execution_model import ExecutionModel
from strategy.validation.validation_engine import ValidationEngine


@dataclass(slots=True)
class DailyWorkflow:
    settings: Settings
    run_logger: RunLogger
    db_client: DuckDBClient

    def run(self, trade_date: str) -> dict[str, Any]:
        run_id = self.run_logger.start_run(run_type="daily", config_hash="phase1-real-data")
        market_repo = MarketRepository(self.db_client)
        research_repo = ResearchRepository(self.db_client)
        provider = AKShareProvider(self.settings.data)

        try:
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Daily workflow started",
                payload={"requested_trade_date": trade_date},
            )

            calendar_count = TradeCalendarCollector(provider=provider, repo=market_repo).collect(
                start_date=self.settings.data.default_start_date,
                end_date=trade_date,
            )
            effective_trade_date = market_repo.get_latest_open_trade_date(trade_date)
            if effective_trade_date is None:
                raise ValueError(f"No open trade date found on or before {trade_date}.")

            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Trade calendar refreshed",
                payload={
                    "requested_trade_date": trade_date,
                    "effective_trade_date": effective_trade_date,
                    "rows": calendar_count,
                },
            )

            instrument_count = InstrumentCollector(provider=provider, repo=market_repo).collect()
            instrument_symbols = market_repo.get_instruments()["symbol"].tolist()
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Instrument universe refreshed",
                payload={"rows": instrument_count, "symbol_count": len(instrument_symbols)},
            )

            index_start_date = add_days(effective_trade_date, -40)
            index_count = IndexDailyCollector(provider=provider, repo=market_repo).collect(
                start_date=index_start_date,
                end_date=effective_trade_date,
                index_codes=self.settings.data.index_codes,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Index history refreshed",
                payload={
                    "rows": index_count,
                    "start_date": index_start_date,
                    "end_date": effective_trade_date,
                },
            )

            try:
                price_count = PriceDailyCollector(provider=provider, repo=market_repo).collect(
                    start_date=effective_trade_date,
                    end_date=effective_trade_date,
                    symbols=instrument_symbols,
                )
            except Exception as exc:
                cached_prices = market_repo.get_price_history(
                    start_date=effective_trade_date,
                    end_date=effective_trade_date,
                )
                if cached_prices.empty:
                    raise
                price_count = int(len(cached_prices))
                self.run_logger.log_event(
                    run_id=run_id,
                    module="daily_workflow",
                    level="WARNING",
                    message="Mainboard daily snapshot refresh failed; reused cached bars",
                    payload={
                        "trade_date": effective_trade_date,
                        "rows": price_count,
                        "error": str(exc),
                    },
                )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Mainboard daily snapshot refreshed",
                payload={"rows": price_count, "trade_date": effective_trade_date},
            )

            research_repo.delete_stock_pool_for_trade_date(effective_trade_date)
            pool_count = StockPoolBuilder(
                settings=self.settings.universe,
                market_repo=market_repo,
                repo=research_repo,
            ).build(trade_date=effective_trade_date)
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Stock pool built",
                payload={"rows": pool_count, "trade_date": effective_trade_date},
            )

            benchmark_index = (
                self.settings.data.index_codes[0]
                if self.settings.data.index_codes
                else "sh000001"
            )
            feature_count = FeaturePipeline(
                market_repo=market_repo,
                repo=research_repo,
                benchmark_index=benchmark_index,
            ).run(trade_date=effective_trade_date)
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Features calculated",
                payload={"rows": feature_count, "trade_date": effective_trade_date},
            )

            market_scan_count = MarketScanService(
                market_repo=market_repo,
                repo=research_repo,
                benchmark_index=benchmark_index,
            ).run(trade_date=effective_trade_date)
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Market regime generated",
                payload={"rows": market_scan_count, "trade_date": effective_trade_date},
            )

            score_count = SelectionService(
                repo=research_repo,
                baseline_ranker=BaselineRanker(
                    factor_weights=self.settings.strategy.baseline_weights
                ),
            ).run(
                trade_date=effective_trade_date,
                horizons=self.settings.strategy.horizons,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Model scores generated",
                payload={"rows": score_count, "trade_date": effective_trade_date},
            )

            research_repo.delete_signals_for_trade_date(effective_trade_date)
            signal_count = 0
            instruments_df = market_repo.get_instruments()[["symbol", "name", "industry_l1"]]
            policy_service = PolicyOverlayService(settings=self.settings.policy)
            rule_engine = RuleEngine(
                settings=self.settings.strategy,
                repo=research_repo,
                policy_service=policy_service,
                instruments_df=instruments_df,
            )
            policy_contexts: list[dict[str, Any]] = []
            for horizon in self.settings.strategy.horizons:
                signal_count += rule_engine.run(
                    trade_date=effective_trade_date,
                    horizon=horizon,
                )
                context = rule_engine.last_run_context.get(horizon, {}).copy()
                if context:
                    context["horizon"] = horizon
                    policy_contexts.append(context)
            policy_context = self._merge_policy_contexts(
                trade_date=effective_trade_date,
                policy_contexts=policy_contexts,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Signals generated",
                payload={"rows": signal_count, "trade_date": effective_trade_date},
            )

            signals_df = research_repo.get_signals(effective_trade_date)
            if not signals_df.empty:
                signals_df = signals_df.merge(
                    instruments_df[["symbol", "name", "industry_l1"]],
                    on="symbol",
                    how="left",
                )
                policy_context["matched_symbols"] = int(
                    signals_df["rule_tags"].fillna("").str.contains("policy_gate").sum()
                )
            market_regime_df = research_repo.get_market_regime(effective_trade_date)
            market_regime = (
                {}
                if market_regime_df.empty
                else market_regime_df.iloc[0].to_dict()
            )
            ai_outputs = self._run_ai_explanations(
                run_id=run_id,
                trade_date=effective_trade_date,
                market_regime=market_regime,
                signals_df=signals_df,
                research_repo=research_repo,
                policy_context=policy_context,
            )
            validation_outputs = self._run_validation_snapshot(
                run_id=run_id,
                trade_date=effective_trade_date,
                market_repo=market_repo,
                research_repo=research_repo,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Validation snapshot generated",
                payload=validation_outputs,
            )
            report_context = ReportContextBuilder().build(
                trade_date=effective_trade_date,
                market_regime=market_regime,
                signals_df=signals_df,
                ai_outputs=ai_outputs,
                validation_outputs=validation_outputs,
                policy_outputs=policy_context,
            )
            report_path = (
                self.db_client.db_path.parents[1]
                / "reports"
                / f"daily_{effective_trade_date}.md"
            )
            SecretaryService(
                repo=research_repo,
                markdown_writer=MarkdownWriter(),
            ).run(
                trade_date=effective_trade_date,
                context=report_context,
                report_path=report_path,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Daily report written",
                payload={"report_path": str(report_path)},
            )
            feishu_result = FeishuSyncService(settings=self.settings.feishu).send_daily_summary(
                trade_date=effective_trade_date,
                market_regime=market_regime,
                signals_df=signals_df,
                ai_outputs=ai_outputs,
                report_path=str(report_path),
                validation_outputs=validation_outputs,
                policy_outputs=policy_context,
            )
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="INFO",
                message="Feishu sync finished",
                payload=feishu_result,
            )

            result = {
                "status": "SUCCESS",
                "requested_trade_date": trade_date,
                "effective_trade_date": effective_trade_date,
                "run_id": run_id,
                "counts": {
                    "trade_calendar": calendar_count,
                    "instrument_basic": instrument_count,
                    "index_daily": index_count,
                    "price_daily": price_count,
                    "stock_pool_daily": pool_count,
                    "features_daily": feature_count,
                    "market_regime_daily": market_scan_count,
                    "model_scores_daily": score_count,
                    "signals_daily": signal_count,
                },
                "report_path": str(report_path),
                "ai_status": ai_outputs.get("status", "UNKNOWN") if isinstance(ai_outputs, dict) else "UNKNOWN",
                "feishu_status": feishu_result.get("status", "UNKNOWN"),
                "validation_status": validation_outputs.get("status", "UNKNOWN"),
                "validation_summary": validation_outputs.get("summaries", {}),
                "policy_status": policy_context.get("status", "UNKNOWN"),
                "policy_themes": policy_context.get("active_themes", []),
                "message": (
                    "Collected mainboard data and produced the policy-aware research chain."
                    if signal_count > 0
                    else "Collected mainboard data. No candidates passed the current sentiment and execution gates."
                ),
            }
            self.run_logger.finish_run(run_id=run_id, status="SUCCESS", summary=result)
            return result
        except Exception as exc:  # pragma: no cover - defensive logging path
            self.run_logger.log_event(
                run_id=run_id,
                module="daily_workflow",
                level="ERROR",
                message="Daily workflow failed",
                payload={"error": str(exc), "requested_trade_date": trade_date},
            )
            self.run_logger.finish_run(
                run_id=run_id,
                status="FAILED",
                summary={"requested_trade_date": trade_date, "error": str(exc)},
            )
            raise

    @staticmethod
    def _merge_policy_contexts(
        *,
        trade_date: str,
        policy_contexts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        if not policy_contexts:
            return {
                "status": "INACTIVE",
                "trade_date": trade_date,
                "theme_sentiment_label": "inactive",
                "active_bonus_count": 0,
                "active_themes": [],
                "matched_symbols": 0,
            }

        theme_map: dict[str, dict[str, Any]] = {}
        fallback_status = "INACTIVE"
        matched_symbols = 0
        for context in policy_contexts:
            status = str(context.get("status", "INACTIVE"))
            if fallback_status == "INACTIVE" and status != "INACTIVE":
                fallback_status = status
            matched_symbols = max(matched_symbols, int(context.get("matched_symbols", 0) or 0))
            horizon = context.get("horizon")
            for theme in context.get("active_themes", []):
                name = str(theme.get("name", ""))
                if not name:
                    continue
                candidate = dict(theme)
                candidate["horizons"] = [horizon] if horizon is not None else []
                existing = theme_map.get(name)
                if existing is None:
                    theme_map[name] = candidate
                    continue
                if float(candidate.get("sentiment_score", 0.0) or 0.0) > float(
                    existing.get("sentiment_score", 0.0) or 0.0
                ):
                    merged = candidate
                else:
                    merged = existing
                merged["horizons"] = sorted(
                    {
                        *existing.get("horizons", []),
                        *candidate.get("horizons", []),
                    }
                )
                merged["matched_count"] = max(
                    int(existing.get("matched_count", 0) or 0),
                    int(candidate.get("matched_count", 0) or 0),
                )
                merged["effective_bonus"] = max(
                    float(existing.get("effective_bonus", 0.0) or 0.0),
                    float(candidate.get("effective_bonus", 0.0) or 0.0),
                )
                merged["bonus_active"] = bool(
                    existing.get("bonus_active") or candidate.get("bonus_active")
                )
                theme_map[name] = merged

        active_themes = list(theme_map.values())
        return {
            "status": "ACTIVE" if active_themes else fallback_status,
            "trade_date": trade_date,
            "theme_sentiment_label": PolicyOverlayService._aggregate_sentiment_label(
                active_themes
            ),
            "active_bonus_count": sum(
                1 for theme in active_themes if bool(theme.get("bonus_active"))
            ),
            "active_themes": active_themes,
            "matched_symbols": matched_symbols,
        }

    def _run_ai_explanations(
        self,
        *,
        run_id: str,
        trade_date: str,
        market_regime: dict,
        signals_df,
        research_repo: ResearchRepository,
        policy_context: dict,
    ) -> dict:
        prompt_root = self.db_client.db_path.parents[2] / "src" / "ai" / "prompts"
        prompt_loader = PromptLoader(prompt_root)

        try:
            client = OpenAIClient(
                api_key=None,
                model=self.settings.ai.model,
                base_url=self.settings.ai.base_url,
                timeout_seconds=self.settings.ai.timeout_seconds,
                max_retries=self.settings.ai.max_retries,
            )
        except ProviderUnavailableError as exc:
            return {"status": "FALLBACK", "message": str(exc)}

        ai_orchestrator = AIOrchestrator(
            settings=self.settings.ai,
            market_agent=MarketSummaryAgent(client=client, prompt_loader=prompt_loader),
            stock_agent=StockExplainerAgent(client=client, prompt_loader=prompt_loader),
            result_store=AIResultStore(LogRepository(self.db_client)),
        )

        feature_lookup = {}
        for row in research_repo.get_features(trade_date).to_dict(orient="records"):
            feature_lookup[row["symbol"]] = json.loads(row["feature_values"]) if row["feature_values"] else {}

        stock_payloads: list[dict] = []
        if signals_df is not None and not signals_df.empty:
            for row in (
                signals_df.sort_values(["horizon", "final_rank"])
                .head(self.settings.ai.max_symbols_per_day)
                .to_dict(orient="records")
            ):
                stock_payloads.append(
                    {
                        "symbol": row.get("symbol"),
                        "name": row.get("name"),
                        "horizon": row.get("horizon"),
                        "final_rank": row.get("final_rank"),
                        "target_weight": row.get("target_weight"),
                        "features": feature_lookup.get(row.get("symbol"), {}),
                    }
                )

        return ai_orchestrator.run_daily(
            trade_date=trade_date,
            run_id=run_id,
            market_payload={
                "trade_date": trade_date,
                "market_regime": {key: str(value) for key, value in market_regime.items()},
                "policy_context": policy_context,
                "signal_count": 0 if signals_df is None else len(signals_df),
            },
            stock_payloads=stock_payloads,
        )

    def _run_validation_snapshot(
        self,
        *,
        run_id: str,
        trade_date: str,
        market_repo: MarketRepository,
        research_repo: ResearchRepository,
    ) -> dict:
        validation_start_date = add_days(trade_date, -60)
        benchmark_index = (
            self.settings.data.index_codes[0]
            if self.settings.data.index_codes
            else "sh000001"
        )
        return ValidationEngine(
            market_repo=market_repo,
            repo=research_repo,
            execution_model=ExecutionModel(
                execution_price_mode=self.settings.validation.execution_price_mode,
                cost_bps=self.settings.validation.cost_bps,
            ),
            settings=self.settings.validation,
            universe_settings=self.settings.universe,
            strategy_settings=self.settings.strategy,
            policy_settings=self.settings.policy,
            benchmark_index=benchmark_index,
        ).run(
            start_date=validation_start_date,
            end_date=trade_date,
            horizons=self.settings.strategy.horizons,
            run_id=run_id,
        )
