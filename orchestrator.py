import asyncio
import os
from dotenv import load_dotenv
from loguru import logger
from core.binance_client import BinanceClient
from core.risk_manager import RiskManager
from agents.brain import Brain

# Load environment variables
load_dotenv()


class AtreidesTrader:
    """Main orchestrator for Atreides-1 Quant Trading System."""
    
    def __init__(self):
        self.symbol = os.getenv('TRADING_SYMBOL', 'BTC/USDT')
        self.timeframe = os.getenv('TIMEFRAME', '1h')
        self.risk_percent = float(os.getenv('RISK_PERCENT', '1.0'))
        self.leverage = int(os.getenv('LEVERAGE', '5'))
        
        # Initialize core components
        self.client = BinanceClient(
            api_key=os.getenv('BINANCE_API_KEY'),
            secret=os.getenv('BINANCE_SECRET'),
            sandbox=True  # Set False for production
        )
        self.risk_manager = RiskManager(self.client)
        self.brain = Brain(api_key=os.getenv('OPENROUTER_API_KEY'))
        
        logger.info("🚀 Atreides-1 System Initialized")
        logger.info(f"Symbol: {self.symbol} | Timeframe: {self.timeframe} | Leverage: {self.leverage}x")
    
    async def run(self):
        """Main async trading loop."""
        await self.client.load_markets()
        
        # Set leverage
        try:
            await self.client.exchange.set_leverage(self.leverage, self.symbol)
            logger.info(f"Leverage set to {self.leverage}x")
        except Exception as e:
            logger.warning(f"Leverage setting skipped: {e}")
        
        while True:
            try:
                await self._trading_cycle()
                await asyncio.sleep(60)  # 1 minute between cycles
                
            except Exception as e:
                logger.error(f"Critical error in trading cycle: {e}")
                await asyncio.sleep(60)
    
    async def _trading_cycle(self):
        """Single trading cycle: Data → AI → Execution."""
        logger.info("📊 Starting analysis cycle...")
        
        # 1. Fetch Market Data
        data = await self.client.get_market_data(self.symbol, self.timeframe, limit=100)
        current_price = data['close'].iloc[-1]
        
        # 2. AI Regime Analysis
        regime = await self.brain.analyze_regime(data)
        
        if regime == 'SIDEWAYS':
            logger.info("⏸️  Market sideways. No action.")
            return
        
        # 3. Calculate Position Size & Stop Loss
        balance = await self.client.exchange.fetch_balance()
        usdt_balance = balance['USDT']['free'] if 'USDT' in balance else 1000
        
        # Simple position sizing: risk% of balance
        position_value = usdt_balance * self.risk_percent / 100
        amount = position_value / current_price
        
        # ATR-based Stop Loss (2x ATR)
        atr = (data['high'] - data['low']).rolling(14).mean().iloc[-1]
        
        if regime == 'BULL':
            side = 'buy'
            sl_price = current_price - (atr * 2)
            logger.info(f"🟢 SIGNAL: LONG | Entry: ~{current_price:.2f} | SL: {sl_price:.2f}")
        else:  # BEAR
            side = 'sell'
            sl_price = current_price + (atr * 2)
            logger.info(f"🔴 SIGNAL: SHORT | Entry: ~{current_price:.2f} | SL: {sl_price:.2f}")
        
        # 4. Atomic Execution via Risk Manager
        await self.risk_manager.execute_atomic_order(
            symbol=self.symbol,
            side=side,
            amount=amount,
            stop_loss_price=sl_price,
            order_type='MARKET'
        )
        
        logger.info("✅ Cycle completed successfully")


if __name__ == '__main__':
    trader = AtreidesTrader()
    asyncio.run(trader.run())
