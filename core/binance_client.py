import ccxt.pro as ccxt
from typing import Optional, Dict, Any
import pandas as pd
from loguru import logger


class BinanceClient:
    """Async Binance Futures USD-M client with precision handling."""
    
    def __init__(self, api_key: str, secret: str, sandbox: bool = True):
        """
        Initialize Binance Futures client.
        
        Args:
            api_key: Binance API key
            secret: Binance API secret
            sandbox: Use testnet if True
        """
        self.exchange = ccxt.binanceusdm({
            'apiKey': api_key,
            'secret': secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future',
                'adjustForTimeDifference': True
            }
        })
        if sandbox:
            self.exchange.set_sandbox_mode(True)
        self.markets: Optional[Dict] = None
    
    async def load_markets(self):
        """Load market metadata for precision handling."""
        if not self.markets:
            self.markets = await self.exchange.load_markets()
            logger.info("Markets loaded successfully")
    
    async def get_market_data(self, symbol: str, timeframe: str = '1h', limit: int = 100) -> pd.DataFrame:
        """
        Fetch OHLCV data from Binance Futures.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            timeframe: Candle timeframe (e.g., '1h', '15m')
            limit: Number of candles to fetch
            
        Returns:
            DataFrame with columns: timestamp, open, high, low, close, volume
        """
        await self.load_markets()
        ohlcv = await self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        return df
    
    def get_precision(self, symbol: str) -> Dict[str, int]:
        """Get amount and price precision for symbol."""
        if not self.markets:
            raise RuntimeError("Markets not loaded. Call load_markets() first.")
        market = self.markets[symbol]
        return {
            'amount': market['precision']['amount'],
            'price': market['precision']['price']
        }
    
    def round_amount(self, symbol: str, amount: float) -> float:
        """Round amount to valid lot size according to Binance rules."""
        return float(self.exchange.amount_to_precision(symbol, amount))
    
    def round_price(self, symbol: str, price: float) -> float:
        """Round price to valid tick size."""
        return float(self.exchange.price_to_precision(symbol, price))
