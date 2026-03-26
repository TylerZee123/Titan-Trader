"""
FundamentalDataFetcher
========================
Pulls balance sheet, income statement, cash flow, and key ratios.
Uses yfinance Ticker.info + financial statements.
Also pulls insider transactions for management quality scoring.
"""

import logging
from typing import Dict, Optional
import yfinance as yf

logger = logging.getLogger("titan_trader")


class FundamentalDataFetcher:

    def __init__(self, config: Dict):
        self.config = config
        self._cache: Dict = {}

    def get_fundamentals(self, ticker: str) -> Dict:
        """
        Returns a comprehensive fundamentals dict for a ticker.
        Cached per session to avoid repeated API calls.
        """
        if ticker in self._cache:
            return self._cache[ticker]

        try:
            stock = yf.Ticker(ticker)
            info  = stock.info or {}

            # ── Balance Sheet Metrics ──────────────────────────────────────
            total_debt        = info.get("totalDebt", 0) or 0
            total_cash        = info.get("totalCash", 0) or 0
            total_assets      = info.get("totalAssets", 1) or 1
            current_ratio     = info.get("currentRatio", 1.0) or 1.0
            debt_to_equity    = info.get("debtToEquity", 50) or 50
            quick_ratio       = info.get("quickRatio", 1.0) or 1.0
            net_cash          = total_cash - total_debt
            net_cash_to_assets= net_cash / total_assets if total_assets else 0

            # ── Earnings & Valuation ───────────────────────────────────────
            pe_ratio          = info.get("trailingPE", 0) or 0
            forward_pe        = info.get("forwardPE", 0) or 0
            peg_ratio         = info.get("pegRatio", 0) or 0
            price_to_book     = info.get("priceToBook", 0) or 0
            price_to_sales    = info.get("priceToSalesTrailing12Months", 0) or 0
            ev_to_ebitda      = info.get("enterpriseToEbitda", 0) or 0
            eps_ttm           = info.get("trailingEps", 0) or 0
            eps_forward       = info.get("forwardEps", 0) or 0

            # ── Growth Metrics ─────────────────────────────────────────────
            revenue_growth    = info.get("revenueGrowth", 0) or 0          # YoY
            earnings_growth   = info.get("earningsGrowth", 0) or 0
            earnings_quarterly= info.get("earningsQuarterlyGrowth", 0) or 0
            revenue_ttm       = info.get("totalRevenue", 0) or 0

            # ── Profitability ──────────────────────────────────────────────
            profit_margin     = info.get("profitMargins", 0) or 0
            operating_margin  = info.get("operatingMargins", 0) or 0
            roe               = info.get("returnOnEquity", 0) or 0          # Return on equity
            roa               = info.get("returnOnAssets", 0) or 0
            free_cash_flow    = info.get("freeCashflow", 0) or 0

            # ── Dividends ─────────────────────────────────────────────────
            dividend_yield    = info.get("dividendYield", 0) or 0
            dividend_rate     = info.get("dividendRate", 0) or 0
            payout_ratio      = info.get("payoutRatio", 0) or 0
            five_year_avg_div = info.get("fiveYearAvgDividendYield", 0) or 0

            # ── Institutional / Insider Ownership ─────────────────────────
            institutional_pct = info.get("institutionalOwnershipPercentage", 0) or 0
            insider_pct       = info.get("heldPercentInsiders", 0) or 0
            short_ratio       = info.get("shortRatio", 0) or 0              # days to cover

            # ── Management ────────────────────────────────────────────────
            company_name      = info.get("longName", ticker)
            sector            = info.get("sector", "Unknown")
            industry          = info.get("industry", "Unknown")
            full_time_employees = info.get("fullTimeEmployees", 0) or 0
            ceo               = info.get("companyOfficers", [{}])[0].get("name", "Unknown") if info.get("companyOfficers") else "Unknown"

            # ── Analyst Sentiment ─────────────────────────────────────────
            target_mean_price = info.get("targetMeanPrice", 0) or 0
            current_price     = info.get("currentPrice", 0) or info.get("regularMarketPrice", 0) or 0
            analyst_upside    = ((target_mean_price / current_price) - 1) if current_price > 0 and target_mean_price > 0 else 0
            recommendation    = info.get("recommendationMean", 3.0) or 3.0  # 1=strong buy, 5=sell
            num_analysts      = info.get("numberOfAnalystOpinions", 0) or 0

            # ── Moat Indicators ───────────────────────────────────────────
            # High margins + high ROE + pricing power = strong moat
            gross_margin      = info.get("grossMargins", 0) or 0
            beta              = info.get("beta", 1.0) or 1.0
            market_cap        = info.get("marketCap", 0) or 0

            # ── Recent Insider Transactions ───────────────────────────────
            try:
                insider_trans = stock.insider_transactions
                if insider_trans is not None and not insider_trans.empty:
                    # Positive = more insider buying than selling (confidence signal)
                    insider_buys  = insider_trans[insider_trans.get("Shares", 0) > 0].shape[0] if "Shares" in insider_trans.columns else 0
                    insider_sells = insider_trans[insider_trans.get("Shares", 0) < 0].shape[0] if "Shares" in insider_trans.columns else 0
                    insider_buy_ratio = insider_buys / (insider_buys + insider_sells) if (insider_buys + insider_sells) > 0 else 0.5
                else:
                    insider_buy_ratio = 0.5
            except Exception:
                insider_buy_ratio = 0.5

            result = {
                # Identity
                "ticker":               ticker,
                "company_name":         company_name,
                "sector":               sector,
                "industry":             industry,
                "ceo":                  ceo,
                "market_cap":           market_cap,
                "employees":            full_time_employees,
                # Balance sheet
                "total_debt":           total_debt,
                "total_cash":           total_cash,
                "net_cash":             net_cash,
                "net_cash_to_assets":   net_cash_to_assets,
                "current_ratio":        current_ratio,
                "quick_ratio":          quick_ratio,
                "debt_to_equity":       debt_to_equity,
                # Valuation
                "pe_ratio":             pe_ratio,
                "forward_pe":           forward_pe,
                "peg_ratio":            peg_ratio,
                "price_to_book":        price_to_book,
                "price_to_sales":       price_to_sales,
                "ev_to_ebitda":         ev_to_ebitda,
                "eps_ttm":              eps_ttm,
                "eps_forward":          eps_forward,
                "analyst_upside":       analyst_upside,
                "recommendation":       recommendation,
                "num_analysts":         num_analysts,
                "target_price":         target_mean_price,
                # Growth
                "revenue_growth":       revenue_growth,
                "earnings_growth":      earnings_growth,
                "earnings_quarterly":   earnings_quarterly,
                "revenue_ttm":          revenue_ttm,
                # Profitability / moat signals
                "profit_margin":        profit_margin,
                "operating_margin":     operating_margin,
                "gross_margin":         gross_margin,
                "roe":                  roe,
                "roa":                  roa,
                "free_cash_flow":       free_cash_flow,
                "beta":                 beta,
                # Dividends
                "dividend_yield":       dividend_yield,
                "dividend_rate":        dividend_rate,
                "payout_ratio":         payout_ratio,
                "five_year_avg_div":    five_year_avg_div,
                # Ownership
                "institutional_pct":    institutional_pct,
                "insider_pct":          insider_pct,
                "insider_buy_ratio":    insider_buy_ratio,
                "short_ratio":          short_ratio,
            }

            self._cache[ticker] = result
            return result

        except Exception as e:
            logger.error(f"  {ticker} fundamentals error: {e}")
            return {"ticker": ticker, "sector": "Unknown", "error": str(e)}
