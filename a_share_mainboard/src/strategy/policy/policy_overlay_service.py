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
            match_mask = self._match_theme(frame=frame, theme=theme)
            watchlist_mask = self._match_watchlist(frame=frame, theme=theme)
            theme_frame = frame.loc[match_mask].copy()
            watchlist_frame = frame.loc[watchlist_mask].copy()
            theme_context = self._build_theme_context(
                theme=theme,
                trade_date=trade_date,
                theme_frame=theme_frame,
                watchlist_frame=watchlist_frame,
            )
            theme_contexts.append(theme_context)

            if match_mask.any():
                frame.loc[match_mask, "policy_matched"] = True

            if not match_mask.any() or theme_context["effective_bonus"] <= 0:
                continue

            frame.loc[match_mask, "policy_bonus"] = (
                frame.loc[match_mask, "policy_bonus"] + float(theme_context["effective_bonus"])
            )
            frame.loc[match_mask, "policy_tags"] = frame.loc[match_mask, "policy_tags"].apply(
                lambda value: self._append_tag(value, theme.name)
            )
            frame.loc[match_mask, "policy_sentiment_label"] = frame.loc[
                match_mask, "policy_sentiment_label"
            ].apply(
                lambda value: self._merge_sentiment_label(
                    value,
                    str(theme_context["sentiment_label"]),
                )
            )

        frame["policy_bonus"] = frame["policy_bonus"].clip(
            upper=self.settings.max_total_bonus
        )
        matched_candidates = int(frame["policy_matched"].sum())
        matched_bonus_candidates = int((frame["policy_bonus"] > 0).sum())
        context = self._build_applied_context(
            trade_date=trade_date,
            theme_contexts=theme_contexts,
            matched_candidates=matched_candidates,
            matched_bonus_candidates=matched_bonus_candidates,
        )
        return frame, context

    def build_context(self, *, trade_date: str) -> dict:
        active_themes = self._resolve_active_themes(trade_date)
        return self._build_applied_context(
            trade_date=trade_date,
            theme_contexts=[
                self._build_inactive_theme_context(theme=theme, trade_date=trade_date)
                for theme in active_themes
            ],
            matched_candidates=0,
            matched_bonus_candidates=0,
        )

    def _build_applied_context(
        self,
        *,
        trade_date: str,
        theme_contexts: list[dict],
        matched_candidates: int,
        matched_bonus_candidates: int,
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
            "matched_candidates": matched_candidates,
            "matched_bonus_candidates": matched_bonus_candidates,
            "matched_symbols": matched_bonus_candidates,
        }

    def _build_theme_context(
        self,
        *,
        theme: PolicyThemeSettings,
        trade_date: str,
        theme_frame: pd.DataFrame,
        watchlist_frame: pd.DataFrame,
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
        event_context = self._build_event_context(theme=theme, trade_date=trade_date)

        tradeable = (
            match_count >= self.settings.min_theme_match_count
            and positive_ratio >= self.settings.min_theme_positive_ratio
            and avg_amount_ratio_5d >= self.settings.min_theme_amount_ratio_5d
            and event_context["event_strength"] > 0
        )
        if not tradeable:
            sentiment_label = "cold"
            effective_bonus = 0.0
        elif sentiment_score >= 0.72:
            sentiment_label = "hot"
            effective_bonus = float(theme.weight) * min(
                self.settings.sentiment_multiplier_cap,
                0.60 + sentiment_score,
            ) * event_context["event_strength"]
        elif sentiment_score >= 0.58:
            sentiment_label = "warm"
            effective_bonus = float(theme.weight) * min(
                self.settings.sentiment_multiplier_cap,
                0.55 + sentiment_score,
            ) * event_context["event_strength"]
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
            "event_label": event_context["event_label"],
            "event_strength": event_context["event_strength"],
            "latest_event_date": event_context["latest_event_date"],
            "latest_event_title": event_context["latest_event_title"],
            "latest_event_source_url": event_context["latest_event_source_url"],
            "watchlist_candidates": self._build_watchlist_candidates(watchlist_frame),
        }

    def _build_inactive_theme_context(
        self,
        *,
        theme: PolicyThemeSettings,
        trade_date: str,
    ) -> dict:
        event_context = self._build_event_context(theme=theme, trade_date=trade_date)
        return {
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
            "event_label": event_context["event_label"],
            "event_strength": event_context["event_strength"],
            "latest_event_date": event_context["latest_event_date"],
            "latest_event_title": event_context["latest_event_title"],
            "latest_event_source_url": event_context["latest_event_source_url"],
            "watchlist_candidates": [],
        }

    def _build_event_context(
        self,
        *,
        theme: PolicyThemeSettings,
        trade_date: str,
    ) -> dict:
        if not theme.events:
            return {
                "event_label": "ongoing",
                "event_strength": 1.0,
                "latest_event_date": "",
                "latest_event_title": "",
                "latest_event_source_url": theme.source_url,
            }

        trade_day = pd.to_datetime(trade_date).date()
        sorted_events = sorted(theme.events, key=lambda item: item.date)
        latest_event = None
        for event in sorted_events:
            event_day = pd.to_datetime(event.date).date()
            if event_day <= trade_day:
                latest_event = event

        if latest_event is None:
            return {
                "event_label": "waiting",
                "event_strength": 0.0,
                "latest_event_date": "",
                "latest_event_title": "",
                "latest_event_source_url": theme.source_url,
            }

        latest_event_day = pd.to_datetime(latest_event.date).date()
        days_since = (trade_day - latest_event_day).days
        if days_since <= self.settings.fresh_event_days:
            event_label = "fresh"
            event_strength = 1.0
        elif days_since <= self.settings.decay_event_days:
            event_label = "decay"
            decay_span = max(
                self.settings.decay_event_days - self.settings.fresh_event_days,
                1,
            )
            progress = (days_since - self.settings.fresh_event_days) / decay_span
            event_strength = self.settings.event_decay_floor + (
                1.0 - self.settings.event_decay_floor
            ) * (1.0 - progress)
        else:
            event_label = "expired"
            event_strength = 0.0

        return {
            "event_label": event_label,
            "event_strength": float(event_strength),
            "latest_event_date": latest_event_day.isoformat(),
            "latest_event_title": latest_event.title,
            "latest_event_source_url": latest_event.source_url,
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
        if "policy_matched" not in frame.columns:
            frame["policy_matched"] = False
        return frame

    @classmethod
    def _match_theme(cls, frame: pd.DataFrame, theme: PolicyThemeSettings) -> pd.Series:
        mask = pd.Series(False, index=frame.index)

        if "industry_l1" in frame.columns and theme.industries:
            mask = mask | frame["industry_l1"].isin(set(theme.industries))
        if theme.industry_aliases:
            mask = mask | cls._match_text_columns(
                frame=frame,
                columns=["industry_l1", "industry_l2"],
                keywords=theme.industry_aliases,
            )
        if "name" in frame.columns and theme.name_keywords:
            mask = mask | cls._match_text_columns(
                frame=frame,
                columns=["name"],
                keywords=theme.name_keywords,
            )
        if theme.symbols:
            mask = mask | frame["symbol"].isin(set(theme.symbols))
        return mask

    @classmethod
    def _match_watchlist(cls, frame: pd.DataFrame, theme: PolicyThemeSettings) -> pd.Series:
        mask = cls._match_theme(frame=frame, theme=theme)
        if theme.watchlist_keywords:
            mask = mask | cls._match_text_columns(
                frame=frame,
                columns=["name", "industry_l1", "industry_l2"],
                keywords=theme.watchlist_keywords,
            )
        return mask

    @staticmethod
    def _match_text_columns(
        *,
        frame: pd.DataFrame,
        columns: list[str],
        keywords: list[str],
    ) -> pd.Series:
        available_columns = [column for column in columns if column in frame.columns]
        if not available_columns or not keywords:
            return pd.Series(False, index=frame.index)
        text_series = frame[available_columns].fillna("").astype(str).agg("|".join, axis=1)
        keyword_tuple = tuple(keyword for keyword in keywords if keyword)
        if not keyword_tuple:
            return pd.Series(False, index=frame.index)
        return text_series.apply(lambda value: any(keyword in value for keyword in keyword_tuple))

    @staticmethod
    def _build_watchlist_candidates(frame: pd.DataFrame) -> list[dict]:
        if frame.empty:
            return []
        ranked = frame.copy()
        for column in ["score_raw", "ret_5d", "amount_ratio_5d"]:
            if column in ranked.columns:
                ranked[column] = pd.to_numeric(ranked[column], errors="coerce")
        sort_columns: list[str] = []
        ascending: list[bool] = []
        if "score_raw" in ranked.columns:
            sort_columns.append("score_raw")
            ascending.append(False)
        if "ret_5d" in ranked.columns:
            sort_columns.append("ret_5d")
            ascending.append(False)
        sort_columns.append("symbol")
        ascending.append(True)
        ranked = ranked.sort_values(sort_columns, ascending=ascending, ignore_index=True)
        payload_columns = [
            column
            for column in [
                "symbol",
                "name",
                "industry_l1",
                "industry_l2",
                "score_raw",
                "ret_5d",
                "amount_ratio_5d",
            ]
            if column in ranked.columns
        ]
        watchlist = ranked[payload_columns].head(5).to_dict(orient="records")
        for item in watchlist:
            for key in ["score_raw", "ret_5d", "amount_ratio_5d"]:
                if key in item and item[key] is not None:
                    item[key] = float(item[key])
        return watchlist

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
