from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from infra.config.settings import PolicySettings, PolicyThemeSettings


@dataclass(slots=True)
class PolicyOverlayService:
    settings: PolicySettings

    def apply(
        self,
        *,
        trade_date: str,
        candidates_df: pd.DataFrame,
    ) -> tuple[pd.DataFrame, dict]:
        active_themes = self._resolve_active_themes(trade_date)
        frame = self._initialize_frame(candidates_df)
        if not self.settings.enabled or frame.empty or not active_themes:
            return frame, self.build_context(trade_date=trade_date)

        theme_contexts: list[dict] = []
        for theme in active_themes:
            mask = self._match_theme(frame=frame, theme=theme)
            theme_frame = frame.loc[mask].copy()
            theme_context = self._build_theme_context(theme=theme, theme_frame=theme_frame)
            theme_contexts.append(theme_context)
            if not mask.any() or theme_context["effective_bonus"] <= 0:
                continue

            frame.loc[mask, "policy_bonus"] = (
                frame.loc[mask, "policy_bonus"] + float(theme_context["effective_bonus"])
            )
            frame.loc[mask, "policy_tags"] = frame.loc[mask, "policy_tags"].apply(
                lambda value: self._append_tag(value, theme.name)
            )
            frame.loc[mask, "policy_sentiment_label"] = frame.loc[
                mask, "policy_sentiment_label"
            ].apply(
                lambda value: self._merge_sentiment_label(
                    value,
                    str(theme_context["sentiment_label"]),
                )
            )

        frame["policy_bonus"] = frame["policy_bonus"].clip(
            upper=self.settings.max_total_bonus
        )
        matched_symbols = int((frame["policy_bonus"] > 0).sum())
        context = self._build_applied_context(
            trade_date=trade_date,
            theme_contexts=theme_contexts,
            matched_symbols=matched_symbols,
        )
        return frame, context

    def build_context(self, *, trade_date: str) -> dict:
        active_themes = self._resolve_active_themes(trade_date)
        return self._build_applied_context(
            trade_date=trade_date,
            theme_contexts=[
                {
                    "name": theme.name,
                    "label": theme.label,
                    "weight": float(theme.weight),
                    "effective_bonus": 0.0,
                    "summary": theme.summary,
                    "source_url": theme.source_url,
                    "matched_count": 0,
                    "positive_ratio": 0.0,
                    "avg_ret_5d": 0.0,
                    "avg_rs_index_10d": 0.0,
                    "avg_amount_ratio_5d": 0.0,
                    "sentiment_score": 0.0,
                    "sentiment_label": "inactive",
                    "bonus_active": False,
                }
                for theme in active_themes
            ],
            matched_symbols=0,
        )

    def _build_applied_context(
        self,
        *,
        trade_date: str,
        theme_contexts: list[dict],
        matched_symbols: int,
    ) -> dict:
        overall_label = self._aggregate_sentiment_label(theme_contexts)
        active_bonus_count = sum(1 for item in theme_contexts if item["bonus_active"])
        if not self.settings.enabled:
            status = "INACTIVE"
        elif theme_contexts:
            status = "ACTIVE"
        else:
            status = "INACTIVE"
        return {
            "status": status,
            "trade_date": trade_date,
            "theme_sentiment_label": overall_label,
            "active_bonus_count": active_bonus_count,
            "active_themes": theme_contexts,
            "matched_symbols": matched_symbols,
        }

    def _build_theme_context(
        self,
        *,
        theme: PolicyThemeSettings,
        theme_frame: pd.DataFrame,
    ) -> dict:
        match_count = int(len(theme_frame))
        positive_ratio = self._safe_mean(theme_frame.get("ret_5d", pd.Series(dtype=float)) > 0)
        avg_ret_5d = self._safe_mean(theme_frame.get("ret_5d", pd.Series(dtype=float)))
        avg_rs_index_10d = self._safe_mean(
            theme_frame.get("rs_index_10d", pd.Series(dtype=float))
        )
        avg_amount_ratio_5d = self._safe_mean(
            theme_frame.get("amount_ratio_5d", pd.Series(dtype=float))
        )
        sentiment_score = self._score_theme_sentiment(
            positive_ratio=positive_ratio,
            avg_ret_5d=avg_ret_5d,
            avg_rs_index_10d=avg_rs_index_10d,
            avg_amount_ratio_5d=avg_amount_ratio_5d,
        )

        tradeable = (
            match_count >= self.settings.min_theme_match_count
            and positive_ratio >= self.settings.min_theme_positive_ratio
            and avg_amount_ratio_5d >= self.settings.min_theme_amount_ratio_5d
        )
        if not tradeable:
            sentiment_label = "cold"
            effective_bonus = 0.0
        elif sentiment_score >= 0.72:
            sentiment_label = "hot"
            effective_bonus = float(theme.weight) * min(
                self.settings.sentiment_multiplier_cap,
                0.60 + sentiment_score,
            )
        elif sentiment_score >= 0.58:
            sentiment_label = "warm"
            effective_bonus = float(theme.weight) * min(
                self.settings.sentiment_multiplier_cap,
                0.55 + sentiment_score,
            )
        else:
            sentiment_label = "cold"
            effective_bonus = 0.0

        return {
            "name": theme.name,
            "label": theme.label,
            "weight": float(theme.weight),
            "effective_bonus": float(effective_bonus),
            "summary": theme.summary,
            "source_url": theme.source_url,
            "matched_count": match_count,
            "positive_ratio": positive_ratio,
            "avg_ret_5d": avg_ret_5d,
            "avg_rs_index_10d": avg_rs_index_10d,
            "avg_amount_ratio_5d": avg_amount_ratio_5d,
            "sentiment_score": sentiment_score,
            "sentiment_label": sentiment_label,
            "bonus_active": effective_bonus > 0,
        }

    def _resolve_active_themes(self, trade_date: str) -> list[PolicyThemeSettings]:
        if not self.settings.enabled:
            return []
        return [
            theme
            for theme in self.settings.themes
            if theme.start_date <= trade_date <= theme.end_date
        ]

    @staticmethod
    def _initialize_frame(candidates_df: pd.DataFrame) -> pd.DataFrame:
        frame = candidates_df.copy()
        if "policy_bonus" not in frame.columns:
            frame["policy_bonus"] = 0.0
        if "policy_tags" not in frame.columns:
            frame["policy_tags"] = ""
        if "policy_sentiment_label" not in frame.columns:
            frame["policy_sentiment_label"] = ""
        return frame

    @staticmethod
    def _match_theme(frame: pd.DataFrame, theme: PolicyThemeSettings) -> pd.Series:
        mask = pd.Series(False, index=frame.index)

        if "industry_l1" in frame.columns and theme.industries:
            mask = mask | frame["industry_l1"].isin(set(theme.industries))
        if "name" in frame.columns and theme.name_keywords:
            keywords = tuple(theme.name_keywords)
            mask = mask | frame["name"].fillna("").apply(
                lambda value: any(keyword in value for keyword in keywords)
            )
        if theme.symbols:
            mask = mask | frame["symbol"].isin(set(theme.symbols))
        return mask

    @staticmethod
    def _score_theme_sentiment(
        *,
        positive_ratio: float,
        avg_ret_5d: float,
        avg_rs_index_10d: float,
        avg_amount_ratio_5d: float,
    ) -> float:
        ret_score = PolicyOverlayService._clip((avg_ret_5d + 0.02) / 0.08)
        rs_score = PolicyOverlayService._clip((avg_rs_index_10d + 0.01) / 0.06)
        amount_score = PolicyOverlayService._clip((avg_amount_ratio_5d - 0.80) / 0.80)
        return (
            0.45 * PolicyOverlayService._clip(positive_ratio)
            + 0.25 * ret_score
            + 0.20 * rs_score
            + 0.10 * amount_score
        )

    @staticmethod
    def _safe_mean(series: pd.Series) -> float:
        numeric = pd.to_numeric(series, errors="coerce")
        if numeric.empty or numeric.notna().sum() == 0:
            return 0.0
        return float(numeric.mean())

    @staticmethod
    def _clip(value: float, lower: float = 0.0, upper: float = 1.0) -> float:
        return max(lower, min(upper, float(value)))

    @classmethod
    def _aggregate_sentiment_label(cls, theme_contexts: list[dict]) -> str:
        if not theme_contexts:
            return "inactive"
        ranking = max(
            cls._sentiment_rank(str(item.get("sentiment_label", "inactive")))
            for item in theme_contexts
        )
        return {
            3: "hot",
            2: "warm",
            1: "cold",
        }.get(ranking, "inactive")

    @staticmethod
    def _sentiment_rank(label: str) -> int:
        return {
            "inactive": 0,
            "cold": 1,
            "warm": 2,
            "hot": 3,
        }.get(label, 0)

    @classmethod
    def _merge_sentiment_label(cls, existing: str, new: str) -> str:
        if cls._sentiment_rank(new) >= cls._sentiment_rank(existing):
            return new
        return existing

    @staticmethod
    def _append_tag(existing: str, tag: str) -> str:
        if not existing:
            return tag
        parts = existing.split("|")
        if tag in parts:
            return existing
        return f"{existing}|{tag}"
