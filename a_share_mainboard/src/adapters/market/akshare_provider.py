from __future__ import annotations

import asyncio
import math
from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO

import httpx
import pandas as pd

from infra.config.settings import DataSettings
from infra.exceptions import ProviderUnavailableError


class AKShareProvider:
    _SZSE_URL = "https://www.szse.cn/api/report/ShowReport"
    _EASTMONEY_SNAPSHOT_URL = "https://82.push2.eastmoney.com/api/qt/clist/get"
    _EASTMONEY_HISTORY_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    _EASTMONEY_SNAPSHOT_FIELDS = "f2,f5,f6,f8,f12,f14,f15,f16,f17,f18"
    _EASTMONEY_MAINBOARD_FS = "m:1 t:2,m:0 t:6"
    _HTTP_TIMEOUT = 30.0
    _SNAPSHOT_BATCH_SIZE = 8
    _HISTORY_WORKERS = 10
    _HTTP_RETRIES = 3

    def __init__(self, config: DataSettings) -> None:
        self.config = config

    def _require_akshare(self):
        try:
            import akshare as ak
        except ImportError as exc:  # pragma: no cover - environment dependent
            raise ProviderUnavailableError(
                "akshare is not installed. Install project dependencies before fetching market data."
            ) from exc
        return ak

    def fetch_trade_calendar(self, start_date: str, end_date: str) -> pd.DataFrame:
        ak = self._require_akshare()
        start = pd.to_datetime(start_date).date()
        end = pd.to_datetime(end_date).date()

        calendar = ak.tool_trade_date_hist_sina().copy()
        calendar["trade_date"] = pd.to_datetime(calendar["trade_date"], errors="coerce").dt.date
        calendar = calendar[
            (calendar["trade_date"] >= start) & (calendar["trade_date"] <= end)
        ].sort_values("trade_date", ignore_index=True)

        if calendar.empty:
            return pd.DataFrame(
                columns=["trade_date", "is_open", "prev_trade_date", "next_trade_date"]
            )

        calendar["is_open"] = True
        calendar["prev_trade_date"] = calendar["trade_date"].shift(1)
        calendar["next_trade_date"] = calendar["trade_date"].shift(-1)
        return calendar[["trade_date", "is_open", "prev_trade_date", "next_trade_date"]]

    def fetch_instruments(self) -> pd.DataFrame:
        ak = self._require_akshare()

        sh_df = ak.stock_info_sh_name_code(symbol="主板A股").copy()
        sh_df = sh_df.rename(
            columns={
                "证券代码": "symbol",
                "证券简称": "name",
                "上市日期": "list_date",
            }
        )
        sh_df["symbol"] = sh_df["symbol"].astype(str).str.zfill(6)
        sh_df["exchange"] = "SSE"
        sh_df["board"] = "MAIN"
        sh_df["is_st"] = sh_df["name"].astype(str).str.contains("ST", na=False)
        sh_df["industry_l1"] = pd.NA
        sh_df["industry_l2"] = pd.NA
        sh_df = sh_df[
            ["symbol", "exchange", "board", "name", "list_date", "is_st", "industry_l1", "industry_l2"]
        ]

        sz_df = self._fetch_szse_mainboard_instruments()

        instruments = pd.concat([sh_df, sz_df], ignore_index=True)
        instruments["list_date"] = pd.to_datetime(instruments["list_date"], errors="coerce").dt.date
        instruments = instruments.drop_duplicates(subset=["symbol"]).sort_values(
            ["exchange", "symbol"], ignore_index=True
        )
        return instruments

    def fetch_price_daily(
        self,
        symbols: list[str] | None,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        if start_date == end_date:
            return self._fetch_latest_mainboard_snapshot(trade_date=end_date, symbols=symbols)

        symbol_list = self._normalize_symbols(symbols)
        if not symbol_list:
            symbol_list = self.fetch_instruments()["symbol"].tolist()

        start_compact = pd.to_datetime(start_date).strftime("%Y%m%d")
        end_compact = pd.to_datetime(end_date).strftime("%Y%m%d")
        frames: list[pd.DataFrame] = []

        with ThreadPoolExecutor(max_workers=self._HISTORY_WORKERS) as executor:
            futures = {
                executor.submit(
                    self._fetch_price_history_for_symbol,
                    symbol=symbol,
                    start_date=start_compact,
                    end_date=end_compact,
                ): symbol
                for symbol in symbol_list
            }
            for future in as_completed(futures):
                df = future.result()
                if not df.empty:
                    frames.append(df)

        if not frames:
            return self._empty_price_daily()

        combined = pd.concat(frames, ignore_index=True)
        combined = combined.sort_values(["trade_date", "symbol"], ignore_index=True)
        return combined

    def fetch_index_daily(
        self,
        index_codes: list[str],
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        ak = self._require_akshare()
        frames: list[pd.DataFrame] = []

        start_compact = pd.to_datetime(start_date).strftime("%Y%m%d")
        end_compact = pd.to_datetime(end_date).strftime("%Y%m%d")

        for index_code in index_codes:
            df = ak.stock_zh_index_daily_em(
                symbol=index_code,
                start_date=start_compact,
                end_date=end_compact,
            )
            if df.empty:
                continue

            normalized = df.rename(columns={"date": "trade_date"}).copy()
            normalized["trade_date"] = pd.to_datetime(
                normalized["trade_date"], errors="coerce"
            ).dt.date
            normalized["index_code"] = index_code
            normalized = normalized[
                ["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount"]
            ]
            frames.append(normalized)

        if not frames:
            return pd.DataFrame(
                columns=["trade_date", "index_code", "open", "high", "low", "close", "volume", "amount"]
            )

        return pd.concat(frames, ignore_index=True).sort_values(
            ["trade_date", "index_code"], ignore_index=True
        )

    def _fetch_szse_mainboard_instruments(self) -> pd.DataFrame:
        params = {
            "SHOWTYPE": "xlsx",
            "CATALOGID": "1110",
            "TABKEY": "tab1",
            "random": "0.6935816432433362",
        }
        headers = {"User-Agent": "Mozilla/5.0"}

        with httpx.Client(
            timeout=self._HTTP_TIMEOUT,
            headers=headers,
            follow_redirects=True,
        ) as client:
            response = client.get(self._SZSE_URL, params=params)
            response.raise_for_status()

        df = pd.read_excel(BytesIO(response.content))
        df["A股代码"] = (
            df["A股代码"]
            .astype(str)
            .str.split(".", expand=True)
            .iloc[:, 0]
            .str.zfill(6)
            .str.replace("000nan", "", regex=False)
        )
        df = df[df["板块"].astype(str).str.contains("主板", na=False)].copy()
        df["所属行业"] = df["所属行业"].astype(str).str.replace(r"\s+", " ", regex=True).str.strip()
        df["industry_l1"] = df["所属行业"].where(~df["所属行业"].isin(["", "nan", "None"]), pd.NA)

        normalized = pd.DataFrame(
            {
                "symbol": df["A股代码"],
                "exchange": "SZSE",
                "board": "MAIN",
                "name": df["A股简称"],
                "list_date": pd.to_datetime(df["A股上市日期"], errors="coerce").dt.date,
                "is_st": df["A股简称"].astype(str).str.contains("ST", na=False),
                "industry_l1": df["industry_l1"],
                "industry_l2": pd.NA,
            }
        )
        normalized = normalized[normalized["symbol"].str.len() == 6].copy()
        return normalized

    def _fetch_price_history_for_symbol(
        self,
        *,
        symbol: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        market_code = 1 if symbol.startswith("6") else 0
        params = {
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "klt": "101",
            "fqt": "1",
            "secid": f"{market_code}.{symbol}",
            "beg": start_date,
            "end": end_date,
        }
        response = self._http_get_json(self._EASTMONEY_HISTORY_URL, params=params)
        klines = response.get("data", {}).get("klines") or []
        if not klines:
            return self._empty_price_daily()

        normalized = pd.DataFrame([item.split(",") for item in klines])
        normalized["symbol"] = symbol
        normalized.columns = [
            "trade_date",
            "open",
            "close",
            "high",
            "low",
            "volume",
            "amount",
            "amplitude",
            "pct_chg",
            "chg",
            "turnover_rate",
            "symbol",
        ]
        normalized["trade_date"] = pd.to_datetime(normalized["trade_date"], errors="coerce").dt.date
        for column in ["open", "close", "high", "low", "volume", "amount", "turnover_rate"]:
            normalized[column] = pd.to_numeric(normalized[column], errors="coerce")
        normalized["adj_factor"] = pd.NA
        normalized["upper_limit_price"] = pd.NA
        normalized["lower_limit_price"] = pd.NA
        normalized["is_suspended"] = False
        return normalized[
            [
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
                "adj_factor",
                "upper_limit_price",
                "lower_limit_price",
                "is_suspended",
            ]
        ]

    def _http_get_json(self, url: str, *, params: dict[str, str]) -> dict:
        headers = {"User-Agent": "Mozilla/5.0"}
        last_error: Exception | None = None
        for _ in range(self._HTTP_RETRIES):
            try:
                with httpx.Client(
                    timeout=self._HTTP_TIMEOUT,
                    headers=headers,
                    follow_redirects=True,
                ) as client:
                    response = client.get(url, params=params)
                    response.raise_for_status()
                    return response.json()
            except Exception as exc:  # pragma: no cover - network-dependent
                last_error = exc
        if last_error is not None:
            raise last_error
        raise ProviderUnavailableError("Unexpected HTTP failure without an exception.")

    def _fetch_latest_mainboard_snapshot(
        self,
        *,
        trade_date: str,
        symbols: list[str] | None,
    ) -> pd.DataFrame:
        rows = self._run_async(self._fetch_all_snapshot_rows())
        if not rows:
            return self._empty_price_daily()

        df = pd.DataFrame(rows)
        df = df.rename(
            columns={
                "f12": "symbol",
                "f17": "open",
                "f2": "close",
                "f15": "high",
                "f16": "low",
                "f5": "volume",
                "f6": "amount",
                "f8": "turnover_rate",
            }
        )
        df = df[
            ["symbol", "open", "high", "low", "close", "volume", "amount", "turnover_rate"]
        ].copy()
        df["symbol"] = df["symbol"].astype(str).str.zfill(6)

        symbol_filter = set(self._normalize_symbols(symbols))
        if symbol_filter:
            df = df[df["symbol"].isin(symbol_filter)].copy()

        for column in ["open", "high", "low", "close", "volume", "amount", "turnover_rate"]:
            df[column] = pd.to_numeric(df[column], errors="coerce")

        df["trade_date"] = pd.to_datetime(trade_date).date()
        df["adj_factor"] = pd.NA
        df["upper_limit_price"] = pd.NA
        df["lower_limit_price"] = pd.NA
        df["is_suspended"] = df["close"].isna()
        return df[
            [
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
                "adj_factor",
                "upper_limit_price",
                "lower_limit_price",
                "is_suspended",
            ]
        ].sort_values("symbol", ignore_index=True)

    async def _fetch_all_snapshot_rows(self) -> list[dict]:
        headers = {"User-Agent": "Mozilla/5.0"}
        base_params = {
            "pn": "1",
            "pz": "100",
            "po": "1",
            "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2",
            "invt": "2",
            "fid": "f12",
            "fs": self._EASTMONEY_MAINBOARD_FS,
            "fields": self._EASTMONEY_SNAPSHOT_FIELDS,
        }

        async with httpx.AsyncClient(
            timeout=self._HTTP_TIMEOUT,
            headers=headers,
            follow_redirects=True,
        ) as client:
            first_response = await client.get(self._EASTMONEY_SNAPSHOT_URL, params=base_params)
            first_response.raise_for_status()
            payload = first_response.json()["data"]
            per_page = len(payload["diff"])
            total_pages = math.ceil(payload["total"] / per_page)
            rows: list[dict] = list(payload["diff"])

            for batch_start in range(2, total_pages + 1, self._SNAPSHOT_BATCH_SIZE):
                batch_pages = range(
                    batch_start,
                    min(batch_start + self._SNAPSHOT_BATCH_SIZE, total_pages + 1),
                )
                batch_rows = await asyncio.gather(
                    *[
                        self._fetch_snapshot_page(client=client, page=page, base_params=base_params)
                        for page in batch_pages
                    ]
                )
                for page_rows in batch_rows:
                    rows.extend(page_rows)

        return rows

    async def _fetch_snapshot_page(
        self,
        *,
        client: httpx.AsyncClient,
        page: int,
        base_params: dict[str, str],
    ) -> list[dict]:
        params = dict(base_params)
        params["pn"] = str(page)
        response = await client.get(self._EASTMONEY_SNAPSHOT_URL, params=params)
        response.raise_for_status()
        return response.json()["data"]["diff"]

    def _run_async(self, coroutine):
        try:
            return asyncio.run(coroutine)
        except RuntimeError:  # pragma: no cover - defensive fallback for nested loops
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(coroutine)
            finally:
                loop.close()

    @staticmethod
    def _normalize_symbols(symbols: list[str] | None) -> list[str]:
        if not symbols:
            return []
        return sorted({str(symbol).zfill(6) for symbol in symbols})

    @staticmethod
    def _empty_price_daily() -> pd.DataFrame:
        return pd.DataFrame(
            columns=[
                "trade_date",
                "symbol",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "amount",
                "turnover_rate",
                "adj_factor",
                "upper_limit_price",
                "lower_limit_price",
                "is_suspended",
            ]
        )
