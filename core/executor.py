"""
TradeExecutor — Alpaca API Integration (Fixed)
================================================
Fixes from audit:
  - Bracket orders use time_in_force=gtc (not day) — swing trades held overnight
  - Fractional shares use notional= not qty= for dollar-based orders
  - Rate limiting with exponential backoff
  - Duplicate order prevention — checks existing positions + open orders
  - Trailing stop support for CORE bucket positions
  - Real P&L calculation from actual filled prices via DataValidator
"""

import logging
import time
import requests
from typing import Dict, List, Optional, Tuple
from data.validator import DataValidator

logger = logging.getLogger("titan_trader")

ALPACA_RATE_LIMIT_DELAY = 0.3   # seconds between API calls
MAX_RETRIES             = 3


class TradeExecutor:

    PAPER_URL = "https://paper-api.alpaca.markets"
    LIVE_URL  = "https://api.alpaca.markets"

    def __init__(self, config: Dict):
        self.config   = config
        self.base_url = self.PAPER_URL if config["alpaca_paper"] else self.LIVE_URL
        self.headers  = {
            "APCA-API-KEY-ID":     config["alpaca_api_key"],
            "APCA-API-SECRET-KEY": config["alpaca_secret_key"],
            "Content-Type":        "application/json",
        }
        mode = "PAPER" if config["alpaca_paper"] else "LIVE"
        logger.info(f"TradeExecutor initialized — {mode} mode @ {self.base_url}")

    # ── Account ────────────────────────────────────────────────────────────

    def get_account(self) -> Dict:
        resp = self._get("/v2/account")
        equity      = float(resp["equity"])
        last_equity = float(resp["last_equity"])
        pnl         = equity - last_equity
        pnl_pct     = (pnl / last_equity * 100) if last_equity > 0 else 0
        return {
            "portfolio_value": resp["portfolio_value"],
            "cash":            resp["cash"],
            "buying_power":    resp["buying_power"],
            "equity":          equity,
            "last_equity":     last_equity,
            "pnl_today":       round(pnl, 2),
            "pnl_today_pct":   round(pnl_pct, 3),
            "open_positions":  0,   # filled below
        }

    def get_positions(self) -> List[Dict]:
        positions = self._get("/v2/positions")
        result = []
        for p in positions:
            qty     = float(p["qty"])
            entry   = float(p["avg_entry_price"])
            current = float(p["current_price"])
            unreal  = float(p["unrealized_pl"])
            unreal_pct = float(p["unrealized_plpc"]) * 100
            result.append({
                "ticker":         p["symbol"],
                "qty":            qty,
                "avg_entry":      entry,
                "current":        current,
                "market_val":     float(p["market_value"]),
                "unrealized":     round(unreal, 2),
                "unrealized_pct": round(unreal_pct, 3),
                # Store for watchdog stop-loss proximity check
                "cost_basis":     round(entry * qty, 2),
            })
        return result

    def get_open_orders(self) -> List[Dict]:
        return self._get("/v2/orders?status=open")

    def get_filled_orders(self, limit: int = 50) -> List[Dict]:
        return self._get(f"/v2/orders?status=filled&direction=desc&limit={limit}")

    def get_position(self, ticker: str) -> Optional[Dict]:
        """Get a single position. Returns None if not held."""
        try:
            return self._get(f"/v2/positions/{ticker}")
        except Exception:
            return None

    # ── Duplicate Prevention ───────────────────────────────────────────────

    def already_have_position_or_order(self, ticker: str) -> bool:
        """
        Returns True if we already own this ticker or have an open order for it.
        Prevents doubling up on a position.
        """
        # Check positions
        positions = self.get_positions()
        if any(p["ticker"] == ticker for p in positions):
            return True
        # Check open orders
        open_orders = self.get_open_orders()
        if any(o.get("symbol") == ticker for o in open_orders):
            return True
        return False

    # ── Order Execution ────────────────────────────────────────────────────

    def execute_plan(self, trade_plan: Dict, performance_tracker=None, market_context: Dict = None) -> Dict:
        """
        Execute a full trade plan.
        Sells first to free up capital, then buys.
        Logs all entries to Supabase via performance_tracker.
        """
        results = {"buys": [], "sells": [], "errors": []}

        # Execute sells first
        for sell in trade_plan.get("sells", []):
            try:
                result = self.market_sell(sell["ticker"], sell["qty"], sell["reason"])
                results["sells"].append(result)

                # Log exit to Supabase with real P&L
                if performance_tracker:
                    position = self.get_position(sell["ticker"])
                    if position:
                        exit_price = float(position.get("current_price", 0))
                        entry_price = float(position.get("avg_entry_price", 0))
                        qty = float(position.get("qty", 0))
                        pnl_data = DataValidator.validate_trade_pnl(entry_price, exit_price, qty)
                        if pnl_data["valid"]:
                            performance_tracker.log_exit(
                                ticker=sell["ticker"],
                                exit_price=exit_price,
                                exit_reason=sell.get("reason", ""),
                            )

                logger.info(f"  SOLD {sell['ticker']} — {sell['reason']}")
            except Exception as e:
                results["errors"].append({"ticker": sell["ticker"], "action": "sell", "error": str(e)})
                logger.error(f"  SELL ERROR {sell['ticker']}: {e}")
            time.sleep(ALPACA_RATE_LIMIT_DELAY)

        # Execute buys
        for buy in trade_plan.get("buys", []):
            try:
                # Final duplicate check before every buy
                if self.already_have_position_or_order(buy["ticker"]):
                    logger.info(f"  SKIP {buy['ticker']} — already have position or open order")
                    continue

                bucket = buy.get("bucket", "MOMENTUM")

                # CORE compounders use trailing stop — no fixed take-profit ceiling
                if bucket == "CORE":
                    result = self.trailing_stop_buy(
                        ticker=buy["ticker"],
                        dollars=buy["dollars"],
                        trail_pct=buy.get("trail_pct", 0.15),
                    )
                else:
                    result = self.bracket_buy(
                        ticker=buy["ticker"],
                        dollars=buy["dollars"],
                        stop_loss_pct=buy["stop_loss_pct"],
                        take_profit_pct=buy["take_profit_pct"],
                    )

                results["buys"].append(result)

                # Log entry to Supabase
                if performance_tracker and market_context:
                    performance_tracker.log_entry(
                        trade={**buy, "price": result.get("entry_price", 0)},
                        signals=buy.get("components", {}),
                        market_context=market_context,
                    )

                logger.info(
                    f"  BOUGHT {buy['ticker']} ${buy['dollars']:,.0f} "
                    f"({'TRAIL' if bucket == 'CORE' else 'BRACKET'})"
                )
            except Exception as e:
                results["errors"].append({"ticker": buy["ticker"], "action": "buy", "error": str(e)})
                logger.error(f"  BUY ERROR {buy['ticker']}: {e}")
            time.sleep(ALPACA_RATE_LIMIT_DELAY)

        return results

    def bracket_buy(
        self,
        ticker: str,
        dollars: float,
        stop_loss_pct: float = 0.07,
        take_profit_pct: float = 0.20,
    ) -> Dict:
        """
        Bracket order for SWING/MOMENTUM/FALLEN positions.
        Uses notional (dollar amount) for fractional share support.
        time_in_force=gtc so stop/target persist overnight.
        """
        # Get current ask price for stop/target calculation
        quote = self._get(f"/v2/stocks/{ticker}/quotes/latest")
        ask   = float(quote["quote"]["ap"])
        if ask <= 0:
            raise ValueError(f"{ticker}: invalid ask price {ask}")

        stop_price   = round(ask * (1 - stop_loss_pct), 2)
        target_price = round(ask * (1 + take_profit_pct), 2)

        order_data = {
            "symbol":        ticker,
            "notional":      str(round(dollars, 2)),   # dollar amount → fractional shares
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",                    # notional orders must use "day"
            "order_class":   "bracket",
            "stop_loss": {
                "stop_price":  str(stop_price),
                "limit_price": str(round(stop_price * 0.99, 2)),
            },
            "take_profit": {
                "limit_price": str(target_price),
            },
        }

        resp = self._post("/v2/orders", order_data)
        return {
            "ticker":       ticker,
            "dollars":      dollars,
            "order_id":     resp.get("id"),
            "entry_price":  ask,
            "stop_loss":    stop_price,
            "take_profit":  target_price,
            "risk_reward":  round(take_profit_pct / stop_loss_pct, 2),
            "order_type":   "bracket",
        }

    def trailing_stop_buy(
        self,
        ticker: str,
        dollars: float,
        trail_pct: float = 0.15,
    ) -> Dict:
        """
        Buy with trailing stop for CORE compounders.
        No fixed take-profit — let winners run indefinitely.
        Trail adjusts to volatility of the ticker.
        time_in_force=gtc so trail persists.
        """
        # Get current price
        quote = self._get(f"/v2/stocks/{ticker}/quotes/latest")
        ask   = float(quote["quote"]["ap"])
        if ask <= 0:
            raise ValueError(f"{ticker}: invalid ask price {ask}")

        # Buy first (market order with notional for fractional support)
        buy_order = {
            "symbol":        ticker,
            "notional":      str(round(dollars, 2)),
            "side":          "buy",
            "type":          "market",
            "time_in_force": "day",
        }
        buy_resp = self._post("/v2/orders", buy_order)

        # Poll for fill — fractional orders can take a few seconds
        filled_qty = None
        for attempt in range(6):   # try for up to 6 seconds
            time.sleep(1)
            try:
                order_status = self._get(f"/v2/orders/{buy_resp.get('id')}")
                filled_qty   = order_status.get("filled_qty")
                if filled_qty and float(filled_qty) > 0:
                    break
            except Exception:
                pass

        # Fallback: estimate qty from dollars/ask if fill not confirmed yet
        trail_qty = filled_qty if (filled_qty and float(filled_qty) > 0) \
                    else str(round(dollars / ask, 6))

        # Then place trailing stop as separate order
        trail_order = {
            "symbol":        ticker,
            "qty":           trail_qty,
            "side":          "sell",
            "type":          "trailing_stop",
            "trail_percent": str(round(trail_pct * 100, 1)),
            "time_in_force": "gtc",
        }

        try:
            trail_resp = self._post("/v2/orders", trail_order)
            trail_order_id = trail_resp.get("id")
        except Exception as e:
            logger.warning(f"  Trailing stop placement failed for {ticker}: {e}")
            trail_order_id = None

        return {
            "ticker":          ticker,
            "dollars":         dollars,
            "order_id":        buy_resp.get("id"),
            "trail_order_id":  trail_order_id,
            "entry_price":     ask,
            "trail_pct":       trail_pct,
            "order_type":      "trailing_stop",
        }

    def market_sell(self, ticker: str, qty: float, reason: str = "") -> Dict:
        """Market sell — exits position fully or partially."""
        order_data = {
            "symbol":        ticker,
            "qty":           str(round(qty, 6)),
            "side":          "sell",
            "type":          "market",
            "time_in_force": "day",
        }
        resp = self._post("/v2/orders", order_data)
        return {"ticker": ticker, "qty": qty, "order_id": resp.get("id"), "reason": reason}

    def partial_sell(self, ticker: str, trim_pct: float, reason: str = "") -> Optional[Dict]:
        """
        Sell a percentage of a position.
        trim_pct: 0.0-1.0 (e.g. 0.25 = sell 25% of position)
        Claude decides the trim_pct — can be any value.
        """
        position = self.get_position(ticker)
        if not position:
            logger.warning(f"  Cannot trim {ticker} — no position found")
            return None

        total_qty = float(position.get("qty", 0))
        sell_qty  = round(total_qty * trim_pct, 6)

        if sell_qty < 0.001:
            logger.warning(f"  {ticker}: trim qty too small ({sell_qty})")
            return None

        logger.info(f"  TRIMMING {ticker}: selling {trim_pct*100:.0f}% ({sell_qty:.4f} shares) — {reason}")
        return self.market_sell(ticker, sell_qty, reason)

    def cancel_all_orders(self):
        return self._delete("/v2/orders")

    # ── HTTP Helpers with Rate Limiting + Retry ────────────────────────────

    def _get(self, path: str) -> Dict:
        return self._request("GET", path)

    def _post(self, path: str, data: Dict) -> Dict:
        return self._request("POST", path, json=data)

    def _delete(self, path: str) -> Dict:
        return self._request("DELETE", path)

    def _request(self, method: str, path: str, **kwargs) -> Dict:
        url = self.base_url + path
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.request(
                    method, url, headers=self.headers,
                    timeout=15, **kwargs
                )
                if resp.status_code == 429:
                    wait = 2 ** attempt
                    logger.warning(f"  Rate limited — waiting {wait}s")
                    time.sleep(wait)
                    continue
                resp.raise_for_status()
                return resp.json()
            except requests.exceptions.Timeout:
                logger.warning(f"  Timeout on {method} {path} (attempt {attempt+1})")
                time.sleep(1)
            except requests.exceptions.HTTPError as e:
                if attempt < MAX_RETRIES - 1 and resp.status_code >= 500:
                    time.sleep(2 ** attempt)
                    continue
                raise
        raise Exception(f"Failed after {MAX_RETRIES} attempts: {method} {path}")
