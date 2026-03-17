from openai import AsyncOpenAI
from typing import Literal
import pandas as pd
from loguru import logger

MarketRegime = Literal['BULL', 'BEAR', 'SIDEWAYS']


class Brain:
    """AI Intelligence for market regime analysis using OpenRouter."""
    
    def __init__(self, api_key: str, model: str = "openai/gpt-4o-mini"):
        """
        Initialize AI brain.
        
        Args:
            api_key: OpenRouter API key
            model: Model identifier on OpenRouter
        """
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.model = model
    
    async def analyze_regime(self, data: pd.DataFrame) -> MarketRegime:
        """
        Analyze market regime based on OHLCV data.
        
        Args:
            data: DataFrame with recent OHLCV data (timestamp, open, high, low, close, volume)
            
        Returns:
            Market regime classification: BULL (Long), BEAR (Short), or SIDEWAYS (Wait)
        """
        # Prepare recent data summary (last 20 candles)
        recent = data.tail(20)
        summary = {
            'current_price': recent['close'].iloc[-1],
            'price_start': recent['close'].iloc[0],
            'high': recent['high'].max(),
            'low': recent['low'].min(),
            'volume_avg': recent['volume'].mean(),
            'volume_last': recent['volume'].iloc[-1],
            'change_pct': ((recent['close'].iloc[-1] - recent['close'].iloc[0]) / recent['close'].iloc[0]) * 100
        }
        
        prompt = f"""
        Analyze this Binance Futures market data:
        - Current Price: {summary['current_price']:.2f}
        - 20-Period Change: {summary['change_pct']:.2f}%
        - High: {summary['high']:.2f}, Low: {summary['low']:.2f}
        - Volume Trend: {'Increasing' if summary['volume_last'] > summary['volume_avg'] else 'Decreasing'}
        
        Classify market regime as exactly one of:
        BULL (strong uptrend, suitable for Long)
        BEAR (strong downtrend, suitable for Short)
        SIDEWAYS (ranging/consolidation, wait)
        
        Return only: BULL, BEAR, or SIDEWAYS
        """
        
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=10
            )
            
            result = response.choices[0].message.content.strip().upper()
            
            # Validate response
            if result not in ['BULL', 'BEAR', 'SIDEWAYS']:
                logger.warning(f"Invalid AI response: {result}, defaulting to SIDEWAYS")
                return 'SIDEWAYS'
                
            logger.info(f"AI Regime Analysis: {result}")
            return result
            
        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return 'SIDEWAYS'  # Safe default
