"""
agents/brain.py
Fase 3 Complete — DeepSeek R1 dengan:
1. JSON structured output (regime + confidence + reasoning)
2. Multi-timeframe analysis (15m + 1h + 4h)
3. Derivatives context (funding rate + open interest)
4. 5-source sentiment (Fear&Greed + News + Reddit + OI + OBI)
"""
from openai import AsyncOpenAI
from typing import Literal, TypedDict, Optional
import pandas as pd
import json
import re
from loguru import logger

MarketRegime = Literal['BULL', 'BEAR', 'SIDEWAYS']
MIN_CONFIDENCE = float(0.75)


class RegimeAnalysis(TypedDict):
    regime: MarketRegime
    confidence: float
    reasoning: str
    source: str


def _compute_indicators(data: pd.DataFrame) -> dict:
    """Hitung semua indikator teknikal dari satu timeframe."""
    recent = data.tail(20).copy()
    close  = recent['close']
    high   = recent['high']
    low    = recent['low']
    volume = recent['volume']

    ema8  = close.ewm(span=8).mean().iloc[-1]
    ema21 = close.ewm(span=21).mean().iloc[-1]
    ema50 = close.ewm(span=50).mean().iloc[-1] if len(close) >= 50 else ema21
    atr   = (high - low).rolling(14).mean().iloc[-1]

    current_price = close.iloc[-1]
    atr_pct       = (atr / current_price) * 100
    change_pct    = ((current_price - close.iloc[0]) / close.iloc[0]) * 100
    volume_ratio  = volume.iloc[-1] / volume.mean()

    delta = close.diff()
    gain  = delta.clip(lower=0).rolling(14).mean().iloc[-1]
    loss  = (-delta.clip(upper=0)).rolling(14).mean().iloc[-1]
    rsi   = 100 - (100 / (1 + gain / loss)) if loss != 0 else 50

    return {
        'price': current_price,
        'ema8': ema8, 'ema21': ema21, 'ema50': ema50,
        'atr': atr, 'atr_pct': atr_pct,
        'change_pct': change_pct,
        'volume_ratio': volume_ratio,
        'rsi': rsi,
        'ema_signal': 'BULLISH' if ema8 > ema21 else 'BEARISH',
    }


class Brain:
    """
    Cognitive engine Atreides-1 — Fase 3 Complete.
    DeepSeek R1 dengan multi-TF + derivatives + 5-source sentiment + OBI.
    """

    def __init__(self, api_key: str):
        self.client = AsyncOpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=api_key
        )
        self.model         = "deepseek/deepseek-r1"
        self.min_confidence = MIN_CONFIDENCE

    # ------------------------------------------------------------------ #
    # Fallback TA                                                          #
    # ------------------------------------------------------------------ #

    def _fallback_regime(self, ind: dict) -> RegimeAnalysis:
        """Pure TA fallback saat R1 tidak tersedia."""
        atr_pct      = ind['atr_pct']
        change_pct   = ind['change_pct']
        volume_ratio = ind['volume_ratio']
        ema_bull     = ind['ema8'] > ind['ema21']

        if atr_pct < 0.3:
            regime, confidence = 'SIDEWAYS', 0.60
            reasoning = f"ATR% {atr_pct:.3f} below threshold — too quiet"
        elif ema_bull and change_pct > 0.5 and volume_ratio > 1.0:
            regime, confidence = 'BULL', 0.70
            reasoning = f"EMA bullish, +{change_pct:.2f}%, vol {volume_ratio:.2f}x"
        elif not ema_bull and change_pct < -0.5 and volume_ratio > 1.0:
            regime, confidence = 'BEAR', 0.70
            reasoning = f"EMA bearish, {change_pct:.2f}%, vol {volume_ratio:.2f}x"
        else:
            regime, confidence = 'SIDEWAYS', 0.65
            reasoning = f"Mixed — EMA {ind['ema_signal']}, Δ{change_pct:.2f}%"

        logger.info(f"[FALLBACK TA] {regime} ({confidence:.0%}) | {reasoning}")
        return RegimeAnalysis(
            regime=regime, confidence=confidence,
            reasoning=reasoning, source='fallback_ta'
        )

    # ------------------------------------------------------------------ #
    # Prompt builder                                                       #
    # ------------------------------------------------------------------ #

    def _build_prompt(
        self,
        tf_15m: dict,
        tf_1h: dict,
        tf_4h: dict,
        funding_rate: Optional[float],
        open_interest_change: Optional[float],
        sentiment_context: str = "",
    ) -> str:
        """Prompt komprehensif — multi-TF + derivatives + 5-source sentiment + OBI."""
        funding_str = (f"{funding_rate*100:.4f}%"
                       if funding_rate is not None else "N/A")
        oi_str      = (f"{open_interest_change:+.2f}%"
                       if open_interest_change is not None else "N/A")

        funding_sentiment = "NEUTRAL"
        if funding_rate is not None:
            if funding_rate > 0.001:
                funding_sentiment = "OVERLEVERAGED_LONG (bearish contrarian)"
            elif funding_rate < -0.001:
                funding_sentiment = "OVERLEVERAGED_SHORT (bullish contrarian)"

        return f"""You are DeepSeek-R1, a Senior Quantitative Analyst for a crypto futures trading system.
Analyze ALL data below. Pay special attention to Order Book Imbalance (OBI) as it is the most real-time signal.

=== MULTI-TIMEFRAME TECHNICAL DATA ===

[15M — Short-term momentum]
Price: ${tf_15m['price']:.4f}
EMA8/21: {tf_15m['ema8']:.4f} / {tf_15m['ema21']:.4f} ({tf_15m['ema_signal']})
Change: {tf_15m['change_pct']:+.2f}% | ATR%: {tf_15m['atr_pct']:.3f}%
RSI: {tf_15m['rsi']:.1f} | Volume: {tf_15m['volume_ratio']:.2f}x avg

[1H — Primary trend]
Price: ${tf_1h['price']:.4f}
EMA8/21/50: {tf_1h['ema8']:.4f} / {tf_1h['ema21']:.4f} / {tf_1h['ema50']:.4f} ({tf_1h['ema_signal']})
Change: {tf_1h['change_pct']:+.2f}% | ATR%: {tf_1h['atr_pct']:.3f}%
RSI: {tf_1h['rsi']:.1f} | Volume: {tf_1h['volume_ratio']:.2f}x avg

[4H — Macro structure]
Price: ${tf_4h['price']:.4f}
EMA8/21/50: {tf_4h['ema8']:.4f} / {tf_4h['ema21']:.4f} / {tf_4h['ema50']:.4f} ({tf_4h['ema_signal']})
Change: {tf_4h['change_pct']:+.2f}% | ATR%: {tf_4h['atr_pct']:.3f}%
RSI: {tf_4h['rsi']:.1f} | Volume: {tf_4h['volume_ratio']:.2f}x avg

=== DERIVATIVES CONTEXT ===
Funding Rate: {funding_str} → {funding_sentiment}
Open Interest Change (1h): {oi_str}

{sentiment_context}

=== SYNTHESIS INSTRUCTIONS ===
1. Start with 1H/4H timeframe alignment (primary weight: 70%)
2. Check OBI — does order book confirm or contradict technical?
3. Cross-reference macro sentiment (Fear&Greed, News, Reddit)
4. High confidence REQUIRES: 2/3 TF aligned + OBI confirmation

Output ONLY a valid JSON object — no markdown, no text outside JSON:

{{
  "regime": "BULL" | "BEAR" | "SIDEWAYS",
  "confidence": <float 0.0-1.0>,
  "reasoning": "<2-3 sentences: technical alignment + OBI + sentiment synthesis>"
}}

Confidence guide:
- 0.90-1.00: All 3 TF + OBI + sentiment all agree
- 0.75-0.89: 2/3 TF aligned + OBI confirms
- 0.60-0.74: Mixed TF but OBI strong — lean toward OBI direction
- 0.00-0.59: Conflicting signals — SIDEWAYS"""

    # ------------------------------------------------------------------ #
    # Main analysis                                                        #
    # ------------------------------------------------------------------ #

    async def analyze_regime(
        self,
        data_15m: pd.DataFrame,
        data_1h: pd.DataFrame,
        data_4h: pd.DataFrame,
        funding_rate: Optional[float] = None,
        open_interest_change: Optional[float] = None,
        sentiment_context: str = "",
    ) -> RegimeAnalysis:
        """
        Analisis regime — multi-TF + derivatives + 5-source sentiment + OBI.
        """
        tf_15m = _compute_indicators(data_15m)
        tf_1h  = _compute_indicators(data_1h)
        tf_4h  = _compute_indicators(data_4h)

        prompt = self._build_prompt(
            tf_15m, tf_1h, tf_4h,
            funding_rate, open_interest_change,
            sentiment_context
        )

        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=400,
            )

            raw = response.choices[0].message.content
            if raw is None:
                logger.warning("R1 returned None — fallback TA")
                return self._fallback_regime(tf_1h)

            # Strip <think>...</think> dari R1
            clean = re.sub(r'<think>.*?</think>', '', raw, flags=re.DOTALL).strip()

            json_match = re.search(r'\{.*\}', clean, re.DOTALL)
            if not json_match:
                logger.warning("No JSON in R1 response — fallback")
                return self._fallback_regime(tf_1h)

            parsed     = json.loads(json_match.group())
            regime     = str(parsed.get('regime', 'SIDEWAYS')).upper()
            confidence = float(parsed.get('confidence', 0.5))
            reasoning  = str(parsed.get('reasoning', ''))

            if regime not in ['BULL', 'BEAR', 'SIDEWAYS']:
                return self._fallback_regime(tf_1h)

            confidence = max(0.0, min(1.0, confidence))

            if confidence < self.min_confidence and regime != 'SIDEWAYS':
                logger.info(
                    f"Confidence {confidence:.0%} < {self.min_confidence:.0%} "
                    f"— downgrading {regime} → SIDEWAYS"
                )
                regime = 'SIDEWAYS'

            logger.info(
                f"R1 Analysis: {regime} | Conf: {confidence:.0%} | "
                f"15m: {tf_15m['ema_signal']} | "
                f"1h: {tf_1h['ema_signal']} | "
                f"4h: {tf_4h['ema_signal']} | "
                f"OBI: {'✅' if 'OBI' in sentiment_context else '⚪'}"
            )
            logger.debug(f"R1 Reasoning: {reasoning}")

            return RegimeAnalysis(
                regime=regime, confidence=confidence,
                reasoning=reasoning, source='r1'
            )

        except json.JSONDecodeError as e:
            logger.error(f"JSON parse failed: {e} — fallback TA")
            return self._fallback_regime(tf_1h)
        except Exception as e:
            logger.error(f"R1 failed: {e} — fallback TA")
            return self._fallback_regime(tf_1h)
