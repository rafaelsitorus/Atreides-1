"""
orchestrator.py — Fase 3 Complete
Multi-TF + Derivatives + 4-Source Sentiment + Weekly Throttle + Circuit Breaker
"""
import asyncio
import os
import time
from dotenv import load_dotenv
from loguru import logger
from core.binance_client import BinanceClient
from core.risk_manager import RiskManager
from core.circuit_breaker import CircuitBreaker
from core.weekly_throttle import WeeklyThrottle
from core.telegram_bot import TelegramC2
from agents.brain import Brain
from utils.news_fetcher import NewsFetcher

load_dotenv()

SYMBOLS          = os.getenv('TRADING_SYMBOLS', 'BTC/USDT,ETH/USDT,SOL/USDT').split(',')
BINANCE_TAKER_FEE = 0.0004


class AtreidesTrader:
    """
    Atreides-1 Fase 3 — Production-ready agentic trading system.
    Modal: ~$185 | Target: 1% daily alpha | Capital preservation first.
    """

    def __init__(self):
        self.symbols      = [s.strip() for s in SYMBOLS]
        self.timeframe    = os.getenv('TIMEFRAME', '1h')
        self.risk_percent = float(os.getenv('RISK_PERCENT', '5.0'))
        self.leverage     = int(os.getenv('LEVERAGE', '5'))
        self.rr_ratio     = float(os.getenv('RR_RATIO', '2.0'))

        self._paused             = False
        self._cached_positions   = []
        self._last_regime        = {}
        self._last_pnl_notify    = time.time()
        self._pnl_notify_interval = 3600
        self._prev_day_equity    = None  # Untuk weekly throttle daily record

        # Core components
        self.client = BinanceClient(
            api_key=os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('BINANCE_SECRET'),
            sandbox=True
        )
        self.risk_manager = RiskManager(self.client, risk_reward_ratio=self.rr_ratio)
        self.brain        = Brain(api_key=os.getenv('OPENROUTER_API_KEY'))

        # News fetcher — pass exchange untuk Long/Short ratio
        self.news_fetcher = NewsFetcher(exchange=self.client.exchange)

        # Risk management
        self.circuit_breaker = CircuitBreaker(
            max_daily_loss_pct=float(os.getenv('MAX_DAILY_LOSS_PCT', '2.0'))
        )
        self.weekly_throttle = WeeklyThrottle(
            target_daily_pct=float(os.getenv('TARGET_DAILY_PCT', '1.0')),
            consecutive_days=int(os.getenv('THROTTLE_DAYS', '7')),
            throttle_min_confidence=0.85,
            throttle_risk_pct=2.0,
            throttle_cycle_skip=2,
        )

        # Telegram C2
        self.telegram = TelegramC2(
            token=os.getenv('TELEGRAM_BOT_TOKEN', ''),
            chat_id=os.getenv('TELEGRAM_CHAT_ID', ''),
            on_pause=self._pause,
            on_resume=self._resume,
            on_close_position=self._close_position,
            on_close_all=self._close_all_positions,
            get_status=self._get_status_dict,
        )

        logger.info("⚔️  Atreides-1 Fase 3 Initialized")
        logger.info(
            f"Symbols: {self.symbols} | TF: 15m/1h/4h | "
            f"Lev: {self.leverage}x | RR: 1:{self.rr_ratio} | "
            f"CB: {os.getenv('MAX_DAILY_LOSS_PCT','2.0')}% | "
            f"Target: {os.getenv('TARGET_DAILY_PCT','1.0')}%/day"
        )

    # ------------------------------------------------------------------ #
    # Remote control                                                       #
    # ------------------------------------------------------------------ #

    async def _pause(self):
        self._paused = True

    async def _resume(self):
        self._paused = False
        self.circuit_breaker._state['tripped'] = False
        self.circuit_breaker._save_state()
        logger.info("Circuit breaker reset via /resume")

    async def _close_position(self, symbol: str) -> dict:
        """Close satu posisi via Telegram."""
        try:
            positions = await self.client.exchange.fetch_positions([symbol])
            for pos in positions:
                contracts = float(pos.get('contracts') or 0)
                if abs(contracts) > 0:
                    side = 'sell' if pos['side'] == 'long' else 'buy'
                    await self.client.exchange.create_market_order(
                        symbol=symbol, side=side,
                        amount=abs(contracts),
                        params={'reduceOnly': True}
                    )
                    pnl = float(pos.get('unrealizedPnl') or 0)
                    logger.warning(f"[{symbol}] Manually closed | PnL: ${pnl:.4f}")
                    return {'symbol': symbol, 'pnl': pnl}
            return {'symbol': symbol, 'pnl': 0}
        except Exception as e:
            logger.error(f"Manual close {symbol} failed: {e}")
            raise

    async def _close_all_positions(self) -> list:
        """Close semua posisi via Telegram."""
        results = []
        for symbol in self.symbols:
            try:
                result = await self._close_position(symbol)
                results.append(result)
            except Exception as e:
                logger.error(f"Close all — {symbol} failed: {e}")
        return results

    def _get_status_dict(self) -> dict:
        """Status untuk Telegram /status — gunakan cached data."""
        cb     = self.circuit_breaker.get_status()
        throttle = self.weekly_throttle.get_status()
        params = self.weekly_throttle.get_active_params(
            self.brain.min_confidence, self.risk_percent
        )
        return {
            "paused": self._paused,
            "circuit_breaker": cb,
            "weekly_throttle": throttle,
            "active_params": params,
            "positions": self._cached_positions,
        }

    async def _refresh_positions_cache(self):
        """Refresh position cache untuk /status."""
        try:
            all_positions = []
            for symbol in self.symbols:
                positions = await self.client.exchange.fetch_positions([symbol])
                for pos in positions:
                    if abs(float(pos.get('contracts') or 0)) > 0:
                        all_positions.append(pos)
            self._cached_positions = all_positions
            logger.debug(f"Position cache: {len(all_positions)} active")
        except Exception as e:
            logger.warning(f"Position cache failed: {e}")

    # ------------------------------------------------------------------ #
    # Derivatives                                                          #
    # ------------------------------------------------------------------ #

    async def _get_derivatives(self, symbol: str) -> dict:
        result = {'funding_rate': None, 'oi_change': None}
        try:
            funding = await self.client.exchange.fetch_funding_rate(symbol)
            result['funding_rate'] = float(funding.get('fundingRate', 0) or 0)
            oi_hist = await self.client.exchange.fetch_open_interest_history(
                symbol, timeframe='1h', limit=2
            )
            if oi_hist and len(oi_hist) >= 2:
                oi_prev = float(oi_hist[-2].get('openInterestAmount', 0) or 0)
                oi_curr = float(oi_hist[-1].get('openInterestAmount', 0) or 0)
                if oi_prev > 0:
                    result['oi_change'] = ((oi_curr - oi_prev) / oi_prev) * 100
        except Exception as e:
            logger.debug(f"[{symbol}] Derivatives failed (non-critical): {e}")
        return result

    # ------------------------------------------------------------------ #
    # Main loop                                                            #
    # ------------------------------------------------------------------ #

    async def run(self):
        """Main async loop — Fase 3 complete."""
        await self.client.load_markets()

        for symbol in self.symbols:
            try:
                await self.client.exchange.set_leverage(self.leverage, symbol)
                logger.info(f"Leverage {self.leverage}x set for {symbol}")
            except Exception as e:
                logger.warning(f"Leverage skip {symbol}: {e}")

        asyncio.create_task(self.telegram.start_polling())
        await asyncio.sleep(2)
        await self.telegram.notify_startup(self.symbols, self.leverage)

        logger.info(
            f"🔄 Fase 3 loop started | "
            f"Throttle: {'ACTIVE' if self.weekly_throttle.is_throttled() else 'NORMAL'}"
        )

        prev_date = None

        while True:
            try:
                # Fetch balance
                balance      = await self.client.exchange.fetch_balance()
                usdt_equity  = float(balance.get('USDT', {}).get('total', 0))
                usdt_free    = float(balance.get('USDT', {}).get('free', 0))

                # --- Daily rollover: record ke weekly throttle ---
                today = str(__import__('datetime').date.today())
                if prev_date is not None and prev_date != today:
                    if self._prev_day_equity is not None:
                        self.weekly_throttle.record_daily_result(
                            equity_start=self._prev_day_equity,
                            equity_end=usdt_equity
                        )
                        # Notify throttle state change via Telegram
                        throttle_st = self.weekly_throttle.get_status()
                        if throttle_st['throttled']:
                            await self.telegram.send(
                                f"🎯 <b>THROTTLE ACTIVATED</b>\n"
                                f"{throttle_st['consecutive_wins']} consecutive profitable days!\n"
                                f"Min confidence: 85% | Risk: 2% | Protecting profits."
                            )
                    self._prev_day_equity = usdt_equity
                elif prev_date is None:
                    self._prev_day_equity = usdt_equity
                prev_date = today

                # --- Circuit breaker check ---
                self.circuit_breaker.update_equity(usdt_equity)
                if self.circuit_breaker.is_tripped():
                    cb = self.circuit_breaker.get_status()
                    logger.critical(
                        f"🚨 Circuit breaker — "
                        f"Drawdown: {cb['drawdown_pct']:.2f}% | Halted"
                    )
                    await self.telegram.notify_circuit_breaker(
                        cb['drawdown_pct'], usdt_equity
                    )
                    await asyncio.sleep(60)
                    continue

                if self._paused:
                    logger.info("⏸️  Paused — /resume to continue")
                    await asyncio.sleep(60)
                    continue

                # --- Weekly throttle cycle skip ---
                if self.weekly_throttle.should_skip_cycle():
                    await asyncio.sleep(60)
                    continue

                # --- Get active params dari throttle ---
                params = self.weekly_throttle.get_active_params(
                    self.brain.min_confidence, self.risk_percent
                )
                active_min_conf = params['min_confidence']
                active_risk_pct = params['risk_pct']
                state           = params['state']

                if state == 'THROTTLED':
                    logger.info(
                        f"🎯 [THROTTLED] "
                        f"MinConf: {active_min_conf:.0%} | "
                        f"Risk: {active_risk_pct}%"
                    )

                # --- Periodic PnL notification ---
                if time.time() - self._last_pnl_notify >= self._pnl_notify_interval:
                    cb = self.circuit_breaker.get_status()
                    await self.telegram.notify_pnl_update(
                        pnl=cb['net_pnl_today'],
                        equity=usdt_equity,
                        trades=cb['trades_today'],
                        win_rate=cb['win_rate'],
                    )
                    self._last_pnl_notify = time.time()

                # --- Refresh position cache ---
                await self._refresh_positions_cache()

                # --- Sequential trading cycle ---
                for symbol in self.symbols:
                    await self._trading_cycle(
                        symbol, usdt_free,
                        active_min_conf, active_risk_pct
                    )
                    await asyncio.sleep(15)

                remaining = 60 - (len(self.symbols) * 15)
                if remaining > 0:
                    await asyncio.sleep(remaining)

            except Exception as e:
                logger.error(f"Main loop error: {e}")
                await asyncio.sleep(60)

    # ------------------------------------------------------------------ #
    # Position check                                                       #
    # ------------------------------------------------------------------ #

    async def _has_open_position(self, symbol: str) -> bool:
        try:
            positions = await self.client.exchange.fetch_positions([symbol])
            for pos in positions:
                contracts = float(pos.get('contracts') or 0)
                if abs(contracts) > 0:
                    pnl = float(pos.get('unrealizedPnl') or 0)
                    logger.info(
                        f"⏸️  [{symbol}] {pos['side'].upper()} | "
                        f"{contracts} contracts | "
                        f"{'🟢' if pnl >= 0 else '🔴'} PnL: ${pnl:.4f}"
                    )
                    return True
            return False
        except Exception as e:
            logger.warning(f"[{symbol}] Position check failed: {e}")
            return False

    # ------------------------------------------------------------------ #
    # Dynamic position sizing                                              #
    # ------------------------------------------------------------------ #

    def _dynamic_position_size(
        self,
        symbol: str,
        usdt_balance: float,
        current_price: float,
        atr: float,
        confidence: float,
        risk_pct: float,
    ) -> float:
        """Dynamic sizing: ATR volatility + confidence scaling."""
        atr_pct            = atr / current_price
        base_risk          = usdt_balance * risk_pct / 100
        volatility_factor  = min(1.0, 0.01 / max(atr_pct, 0.001))
        confidence_factor  = (confidence - self.brain.min_confidence) / (
            1.0 - self.brain.min_confidence
        )
        confidence_factor  = max(0.5, min(1.0, confidence_factor))
        adjusted_risk      = base_risk * volatility_factor * confidence_factor * self.leverage
        amount             = adjusted_risk / current_price

        logger.debug(
            f"[{symbol}] Sizing: "
            f"ATR%={atr_pct:.4f} | Vol={volatility_factor:.2f} | "
            f"Conf={confidence_factor:.2f} | Risk=${adjusted_risk:.2f}"
        )
        return amount

    # ------------------------------------------------------------------ #
    # Trading cycle                                                        #
    # ------------------------------------------------------------------ #

    async def _trading_cycle(
        self,
        symbol: str,
        usdt_free: float,
        active_min_conf: float,
        active_risk_pct: float,
    ):
        """
        Fase 3 trading cycle:
        Multi-TF → Derivatives → 4-Source Sentiment → R1 JSON
        → Confidence gate → Atomic execution
        """
        try:
            logger.info(f"📊 [{symbol}] Analysis started...")

            # 1. Fetch multi-TF concurrent
            data_15m, data_1h, data_4h = await asyncio.gather(
                self.client.get_market_data(symbol, '15m', limit=60),
                self.client.get_market_data(symbol, '1h',  limit=100),
                self.client.get_market_data(symbol, '4h',  limit=50),
            )

            current_price = float(data_1h['close'].iloc[-1])
            atr_1h        = float(
                (data_1h['high'] - data_1h['low']).rolling(14).mean().iloc[-1]
            )

            # 2. Derivatives + 4-source sentiment concurrent
            derivatives, sentiment_ctx = await asyncio.gather(
                self._get_derivatives(symbol),
                self.news_fetcher.get_sentiment_context(symbol),
            )

            sentiment_str = self.news_fetcher.format_for_prompt(sentiment_ctx, symbol)

            # Log sentiment summary
            fg     = sentiment_ctx.get('fear_greed', {})
            news   = sentiment_ctx.get('news', {})
            reddit = sentiment_ctx.get('reddit', {})
            obi    = sentiment_ctx.get('obi', {})
            logger.info(
                f"[{symbol}] Sentiment: "
                f"F&G={fg.get('value',50)}/100 | "
                f"News={news.get('sentiment','N/A')} | "
                f"Reddit={reddit.get('sentiment','N/A')} | "
                f"OI={sentiment_ctx.get('oi_sentiment',{}).get('signal','N/A')} | "
                f"OBI={obi.get('obi',0.0):+.3f}({obi.get('signal','N/A')})"
            )

            # 3. R1 analysis — semua data masuk sekaligus
            analysis = await self.brain.analyze_regime(
                data_15m=data_15m,
                data_1h=data_1h,
                data_4h=data_4h,
                funding_rate=derivatives['funding_rate'],
                open_interest_change=derivatives['oi_change'],
                sentiment_context=sentiment_str,
            )

            regime     = analysis['regime']
            confidence = analysis['confidence']
            source     = analysis['source']

            logger.info(
                f"[{symbol}] R1: {regime} | "
                f"Conf: {confidence:.0%} | "
                f"Threshold: {active_min_conf:.0%} | "
                f"Src: {source}"
            )

            # Notify regime shift
            old_regime = self._last_regime.get(symbol, 'UNKNOWN')
            if old_regime != 'UNKNOWN' and old_regime != regime:
                await self.telegram.notify_regime_shift(
                    symbol, old_regime, regime, confidence
                )
            self._last_regime[symbol] = regime

            # 4. Confidence gate dengan active threshold
            if regime == 'SIDEWAYS':
                logger.info(f"⏸️  [{symbol}] SIDEWAYS — skip")
                return

            if confidence < active_min_conf:
                logger.info(
                    f"⏸️  [{symbol}] Conf {confidence:.0%} < "
                    f"{active_min_conf:.0%} — skip"
                )
                return

            # 5. Cek posisi existing
            if await self._has_open_position(symbol):
                logger.info(f"⏸️  [{symbol}] Posisi aktif — skip")
                return

            # 6. Dynamic position sizing
            amount = self._dynamic_position_size(
                symbol, usdt_free, current_price, atr_1h,
                confidence, active_risk_pct
            )

            MIN_NOTIONAL = 110
            amount       = max(amount, MIN_NOTIONAL / current_price)
            amount       = self.client.round_amount(symbol, amount)

            market_info  = self.client.exchange.market(symbol)
            min_amount   = float(market_info['limits']['amount']['min'])
            final_amount = max(amount, min_amount)
            final_amount = self.client.round_amount(symbol, final_amount)

            required_margin = (final_amount * current_price) / self.leverage
            if required_margin > usdt_free:
                logger.warning(
                    f"⚠️  [{symbol}] Insufficient margin — "
                    f"Available: ${usdt_free:.2f} | Required: ${required_margin:.2f}"
                )
                return

            # 7. ATR-based SL
            if regime == 'BULL':
                side     = 'buy'
                sl_price = current_price - (atr_1h * 2)
                logger.info(
                    f"🟢 [{symbol}] LONG | "
                    f"Entry: ~${current_price:.2f} | SL: ${sl_price:.2f} | "
                    f"Conf: {confidence:.0%}"
                )
            else:
                side     = 'sell'
                sl_price = current_price + (atr_1h * 2)
                logger.info(
                    f"🔴 [{symbol}] SHORT | "
                    f"Entry: ~${current_price:.2f} | SL: ${sl_price:.2f} | "
                    f"Conf: {confidence:.0%}"
                )

            # 8. Atomic execution: Entry + SL + TP
            result = await self.risk_manager.execute_atomic_order(
                symbol=symbol,
                side=side,
                amount=final_amount,
                stop_loss_price=sl_price,
                order_type='MARKET'
            )

            estimated_fee = current_price * final_amount * BINANCE_TAKER_FEE * 2
            self.circuit_breaker.record_trade(pnl=0.0, fee=estimated_fee)

            logger.info(
                f"✅ [{symbol}] Executed | "
                f"SL: ${result['sl_price']:.2f} | "
                f"TP: ${result['tp_price']:.2f} | "
                f"RR: 1:{self.rr_ratio} | "
                f"Fee: ~${estimated_fee:.4f}"
            )

            await self.telegram.notify_trade(
                symbol=symbol, side=side,
                amount=final_amount, entry=current_price,
                sl=result['sl_price'], tp=result['tp_price'],
                rr=self.rr_ratio,
            )

        except Exception as e:
            logger.error(f"❌ [{symbol}] Cycle error: {e}")


if __name__ == '__main__':
    trader = AtreidesTrader()
    asyncio.run(trader.run())
