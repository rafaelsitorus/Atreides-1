from openai import AsyncOpenAI
from typing import Literal
import pandas as pd
from loguru import logger

MarketRegime = Literal['BULL', 'BEAR', 'SIDEWAYS']


class Brain:
    """AI Intelligence untuk market regime analysis via OpenRouter."""

    def __init__(self, api_key: str, model: str = "qwen/qwen3-coder:free"):
        """
        Args:
            api_key: OpenRouter API key
            model: Model OpenRouter yang digunakan
        """
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.model = model

    async def analyze_regime(self, data: pd.DataFrame) -> MarketRegime:
        """
        Analisis regime pasar dari data OHLCV.

        Args:
            data: DataFrame dengan kolom timestamp, open, high, low, close, volume

        Returns:
            BULL (Long), BEAR (Short), atau SIDEWAYS (Wait)
        """
        recent = data.tail(20).copy()

        # Indikator teknikal sederhana
        ema_fast = recent['close'].ewm(span=8).mean().iloc[-1]
        ema_slow = recent['close'].ewm(span=21).mean().iloc[-1]
        atr = (recent['high'] - recent['low']).rolling(14).mean().iloc[-1]
        current_price = recent['close'].iloc[-1]
        change_pct = ((current_price - recent['close'].iloc[0]) / recent['close'].iloc[0]) * 100
        volume_ratio = recent['volume'].iloc[-1] / recent['volume'].mean()

        # Volatility relative to price
        atr_pct = (atr / current_price) * 100

        summary = {
            'current_price': current_price,
            'change_pct': change_pct,
            'ema_fast': ema_fast,
            'ema_slow': ema_slow,
            'ema_signal': 'BULLISH' if ema_fast > ema_slow else 'BEARISH',
            'atr_pct': atr_pct,
            'volume_ratio': volume_ratio,
            'high': recent['high'].max(),
            'low': recent['low'].min(),
        }

        prompt = f"""You are a professional crypto futures trader. Analyze this BTC/USDT market data:

Price: ${summary['current_price']:.2f}
20-candle change: {summary['change_pct']:.2f}%
EMA8 vs EMA21: {summary['ema_fast']:.2f} vs {summary['ema_slow']:.2f} ({summary['ema_signal']})
ATR%: {summary['atr_pct']:.2f}% (volatility)
Volume vs average: {summary['volume_ratio']:.2f}x
Range: ${summary['low']:.2f} - ${summary['high']:.2f}

Rules:
- BULL: EMA8 > EMA21, change > +0.5%, volume > 1.0x average
- BEAR: EMA8 < EMA21, change < -0.5%, volume > 1.0x average  
- SIDEWAYS: anything else, or ATR% < 0.3% (too quiet to trade)

Reply with exactly one word: BULL, BEAR, or SIDEWAYS"""

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=10
            )

            result = response.choices[0].message.content.strip().upper()

            # Bersihkan jika ada teks tambahan
            for word in ['BULL', 'BEAR', 'SIDEWAYS']:
                if word in result:
                    result = word
                    break

            if result not in ['BULL', 'BEAR', 'SIDEWAYS']:
                logger.warning(f"Invalid AI response: '{result}', defaulting to SIDEWAYS")
                return 'SIDEWAYS'

            logger.info(
                f"AI Regime: {result} | "
                f"EMA: {summary['ema_signal']} | "
                f"Δ{summary['change_pct']:.2f}% | "
                f"Vol: {summary['volume_ratio']:.2f}x"
            )
            return result

        except Exception as e:
            logger.error(f"AI analysis failed: {e}")
            return 'SIDEWAYS'