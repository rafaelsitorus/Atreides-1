import asyncio
import os
from dotenv import load_dotenv
from loguru import logger
from core.binance_client import BinanceClient
from core.risk_manager import RiskManager
from agents.brain import Brain

load_dotenv()

# Symbols yang akan ditrading secara concurrent
SYMBOLS = os.getenv('TRADING_SYMBOLS', 'BTC/USDT,ETH/USDT,SOL/USDT').split(',')


class AtreidesTrader:
    """Main orchestrator untuk Atreides-1 Multi-Symbol Trading System."""

    def __init__(self):
        self.symbols = [s.strip() for s in SYMBOLS]
        self.timeframe = os.getenv('TIMEFRAME', '1h')
        self.risk_percent = float(os.getenv('RISK_PERCENT', '5.0'))
        self.leverage = int(os.getenv('LEVERAGE', '5'))
        self.rr_ratio = float(os.getenv('RR_RATIO', '2.0'))

        self.client = BinanceClient(
            api_key=os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('BINANCE_SECRET'),
            sandbox=True
        )
        self.risk_manager = RiskManager(self.client, risk_reward_ratio=self.rr_ratio)
        self.brain = Brain(api_key=os.getenv('OPENROUTER_API_KEY'))

        logger.info("🚀 Atreides-1 System Initialized")
        logger.info(f"Symbols: {self.symbols} | TF: {self.timeframe} | Lev: {self.leverage}x | RR: 1:{self.rr_ratio}")

    async def run(self):
        """Main async loop — semua symbol jalan concurrent."""
        await self.client.load_markets()

        # Set leverage untuk semua symbol
        for symbol in self.symbols:
            try:
                await self.client.exchange.set_leverage(self.leverage, symbol)
                logger.info(f"Leverage {self.leverage}x set for {symbol}")
            except Exception as e:
                logger.warning(f"Leverage skip {symbol}: {e}")

        logger.info(f"🔄 Starting concurrent trading loop for {len(self.symbols)} symbols...")

        while True:
            # Jalankan semua symbol secara bersamaan
            tasks = [self._trading_cycle(symbol) for symbol in self.symbols]
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(60)

    async def _has_open_position(self, symbol: str) -> bool:
        """Cek posisi terbuka untuk symbol tertentu."""
        try:
            positions = await self.client.exchange.fetch_positions([symbol])
            for pos in positions:
                contracts = float(pos.get('contracts') or 0)
                if abs(contracts) > 0:
                    pnl = float(pos.get('unrealizedPnl') or 0)
                    pnl_icon = '🟢' if pnl >= 0 else '🔴'
                    logger.info(
                        f"⏸️  [{symbol}] Posisi aktif: {pos['side'].upper()} | "
                        f"{contracts} contracts | "
                        f"{pnl_icon} PnL: ${pnl:.4f}"
                    )
                    return True
            return False
        except Exception as e:
            logger.warning(f"[{symbol}] Tidak bisa cek posisi: {e}")
            return False

    def _dynamic_position_size(
        self,
        symbol: str,
        usdt_balance: float,
        current_price: float,
        atr: float
    ) -> float:
        """
        Dynamic position sizing berbasis volatilitas (ATR).
        Semakin tinggi volatilitas, semakin kecil posisi.

        Formula: size = (balance × risk%) / (ATR × atr_multiplier)
        """
        atr_pct = atr / current_price
        base_risk = usdt_balance * self.risk_percent / 100

        # Sesuaikan risk berdasarkan volatilitas
        # ATR% normal ~0.5-1.5%, jika lebih tinggi kurangi posisi
        volatility_factor = min(1.0, 0.01 / max(atr_pct, 0.001))
        adjusted_risk = base_risk * volatility_factor * self.leverage

        amount = adjusted_risk / current_price

        logger.debug(
            f"[{symbol}] Dynamic sizing: "
            f"ATR%={atr_pct:.3f} | "
            f"Vol factor={volatility_factor:.2f} | "
            f"Adjusted risk=${adjusted_risk:.2f}"
        )
        return amount

    async def _trading_cycle(self, symbol: str):
        """Single trading cycle untuk satu symbol."""
        try:
            logger.info(f"📊 [{symbol}] Starting analysis...")

            # 1. Fetch Market Data
            data = await self.client.get_market_data(symbol, self.timeframe, limit=100)
            current_price = float(data['close'].iloc[-1])
            atr = float((data['high'] - data['low']).rolling(14).mean().iloc[-1])

            # 2. AI Regime Analysis
            regime = await self.brain.analyze_regime(data)

            if regime == 'SIDEWAYS':
                logger.info(f"⏸️  [{symbol}] Market sideways — skip")
                return

            # 3. Cek posisi existing
            if await self._has_open_position(symbol):
                logger.info(f"⏸️  [{symbol}] Posisi aktif — skip")
                return

            # 4. Dynamic Position Sizing
            balance = await self.client.exchange.fetch_balance()
            usdt_balance = float(balance.get('USDT', {}).get('free', 1000))

            amount = self._dynamic_position_size(symbol, usdt_balance, current_price, atr)

            # Guarantee minimum notional $110
            MIN_NOTIONAL = 110
            amount = max(amount, MIN_NOTIONAL / current_price)
            amount = self.client.round_amount(symbol, amount)

            # Validate exchange minimum
            market_info = self.client.exchange.market(symbol)
            min_amount = float(market_info['limits']['amount']['min'])
            final_amount = max(amount, min_amount)
            final_amount = self.client.round_amount(symbol, final_amount)

            # Validate margin
            required_margin = (final_amount * current_price) / self.leverage
            if required_margin > usdt_balance:
                logger.warning(
                    f"⚠️  [{symbol}] Insufficient margin — "
                    f"Available: ${usdt_balance:.2f} | Required: ${required_margin:.2f}"
                )
                return

            # 5. ATR-based Stop Loss
            if regime == 'BULL':
                side = 'buy'
                sl_price = current_price - (atr * 2)
                logger.info(f"🟢 [{symbol}] LONG | Entry: ~${current_price:.2f} | SL: ${sl_price:.2f}")
            else:
                side = 'sell'
                sl_price = current_price + (atr * 2)
                logger.info(f"🔴 [{symbol}] SHORT | Entry: ~${current_price:.2f} | SL: ${sl_price:.2f}")

            # 6. Atomic Execution: Entry + SL + TP
            result = await self.risk_manager.execute_atomic_order(
                symbol=symbol,
                side=side,
                amount=final_amount,
                stop_loss_price=sl_price,
                order_type='MARKET'
            )

            logger.info(
                f"✅ [{symbol}] Cycle done | "
                f"SL: ${result['sl_price']:.2f} | "
                f"TP: ${result['tp_price']:.2f} | "
                f"RR: 1:{self.rr_ratio}"
            )

        except Exception as e:
            logger.error(f"❌ [{symbol}] Cycle error: {e}")


if __name__ == '__main__':
    trader = AtreidesTrader()
    asyncio.run(trader.run())