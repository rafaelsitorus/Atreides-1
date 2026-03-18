"""
utils/news_fetcher.py
Fase 3 — 5 sumber data gratis tanpa API key berbayar.

1. Fear & Greed Index      — alternative.me (no key)
2. CoinGecko News          — coingecko.com (no key)
3. Reddit RSS              — reddit.com (no key)
4. Binance Open Interest   — via CCXT (key sudah ada)
5. Order Book Imbalance    — via CCXT fetch_order_book (key sudah ada)
"""
import asyncio
import aiohttp
from datetime import datetime, timezone
from loguru import logger


class NewsFetcher:
    """
    Async multi-source sentiment + microstructure fetcher.
    Semua sumber di-cache untuk hemat bandwidth dan rate limit.
    """

    FEAR_GREED_URL = "https://api.alternative.me/fng/?limit=1"
    COINGECKO_URL  = "https://api.coingecko.com/api/v3/news"
    REDDIT_URL     = "https://www.reddit.com/r/CryptoCurrency/hot.json?limit=20"

    # Cache TTL berbeda per sumber
    TTL_SENTIMENT  = 900   # 15 menit — berita & sentiment
    TTL_OBI        = 60    # 1 menit — order book berubah cepat
    TTL_OI         = 300   # 5 menit — OI berubah sedang

    def __init__(self, exchange=None):
        self.exchange = exchange
        self._cache: dict = {}
        self._cache_time: dict = {}
        self._cache_ttl: dict = {}

    # ------------------------------------------------------------------ #
    # Cache helpers                                                        #
    # ------------------------------------------------------------------ #

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        ttl = self._cache_ttl.get(key, self.TTL_SENTIMENT)
        age = datetime.now(timezone.utc).timestamp() - self._cache_time[key]
        return age < ttl

    def _set_cache(self, key: str, value, ttl: int = None):
        self._cache[key] = value
        self._cache_time[key] = datetime.now(timezone.utc).timestamp()
        self._cache_ttl[key] = ttl or self.TTL_SENTIMENT

    # ------------------------------------------------------------------ #
    # 1. Fear & Greed Index                                                #
    # ------------------------------------------------------------------ #

    async def get_fear_greed(self) -> dict:
        """Contrarian signal: Extreme Fear → BULL bias, Extreme Greed → BEAR bias."""
        key = "fear_greed"
        if self._is_cache_valid(key):
            return self._cache[key]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.FEAR_GREED_URL,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()

            value = int(data['data'][0]['value'])
            label = data['data'][0]['value_classification']

            if value <= 25:
                trading_bias = 'BULLISH'
            elif value <= 45:
                trading_bias = 'MILD_BULLISH'
            elif value <= 55:
                trading_bias = 'NEUTRAL'
            elif value <= 75:
                trading_bias = 'MILD_BEARISH'
            else:
                trading_bias = 'BEARISH'

            result = {'value': value, 'label': label, 'trading_bias': trading_bias}
            self._set_cache(key, result)
            logger.debug(f"Fear & Greed: {value}/100 ({label}) → {trading_bias}")
            return result

        except Exception as e:
            logger.debug(f"Fear & Greed failed: {e}")
            return {'value': 50, 'label': 'Neutral', 'trading_bias': 'NEUTRAL'}

    # ------------------------------------------------------------------ #
    # 2. CoinGecko News                                                    #
    # ------------------------------------------------------------------ #

    async def get_coingecko_news(self, currency: str = "BTC") -> dict:
        """Keyword-based sentiment dari headline CoinGecko."""
        key = f"cg_news_{currency}"
        if self._is_cache_valid(key):
            return self._cache[key]

        BEARISH_KW = [
            'hack', 'exploit', 'ban', 'crackdown', 'lawsuit', 'sec',
            'regulation', 'crash', 'liquidat', 'fear', 'dump', 'sell',
            'warning', 'risk', 'scam', 'fraud', 'collapse', 'probe'
        ]
        BULLISH_KW = [
            'etf', 'approval', 'adoption', 'launch', 'partnership',
            'upgrade', 'bullish', 'rally', 'ath', 'record',
            'institutional', 'buy', 'accumul', 'breakout', 'surge'
        ]

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.COINGECKO_URL,
                    headers={'accept': 'application/json'},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()

            articles   = data.get('data', [])[:15]
            headlines  = []
            bull_score = 0
            bear_score = 0

            for article in articles:
                title = article.get('title', '').lower()
                headlines.append(article.get('title', ''))
                for kw in BULLISH_KW:
                    if kw in title:
                        bull_score += 1
                for kw in BEARISH_KW:
                    if kw in title:
                        bear_score += 1

            total           = bull_score + bear_score
            sentiment_score = bull_score / total if total > 0 else 0.5
            sentiment       = ('BULLISH' if sentiment_score >= 0.6
                               else 'BEARISH' if sentiment_score <= 0.4
                               else 'NEUTRAL')

            result = {
                'headlines': headlines[:5], 'bull_score': bull_score,
                'bear_score': bear_score, 'sentiment_score': sentiment_score,
                'sentiment': sentiment,
            }
            self._set_cache(key, result)
            logger.debug(f"CoinGecko [{currency}]: {sentiment} B:{bull_score} Br:{bear_score}")
            return result

        except Exception as e:
            logger.debug(f"CoinGecko news failed: {e}")
            return {
                'headlines': [], 'bull_score': 0, 'bear_score': 0,
                'sentiment_score': 0.5, 'sentiment': 'NEUTRAL',
            }

    # ------------------------------------------------------------------ #
    # 3. Reddit Sentiment                                                  #
    # ------------------------------------------------------------------ #

    async def get_reddit_sentiment(self, currency: str = "BTC") -> dict:
        """Upvote ratio dari r/CryptoCurrency sebagai crowd sentiment."""
        key = f"reddit_{currency}"
        if self._is_cache_valid(key):
            return self._cache[key]

        coin_keywords = {
            'BTC': ['btc', 'bitcoin'],
            'ETH': ['eth', 'ethereum'],
            'SOL': ['sol', 'solana'],
        }
        kws = coin_keywords.get(currency.upper(), ['crypto'])

        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.REDDIT_URL,
                    headers={'User-Agent': 'Atreides-1/1.0'},
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()

            posts              = data.get('data', {}).get('children', [])
            total_upvote_ratio = 0.0
            relevant_posts     = 0
            relevant_titles    = []

            for post in posts:
                pd_   = post.get('data', {})
                title = pd_.get('title', '').lower()
                ratio = float(pd_.get('upvote_ratio', 0.5))
                score = int(pd_.get('score', 0))

                if any(kw in title for kw in kws) or score > 500:
                    total_upvote_ratio += ratio
                    relevant_posts     += 1
                    relevant_titles.append(pd_.get('title', ''))

            avg_ratio = (total_upvote_ratio / relevant_posts
                         if relevant_posts > 0 else 0.5)
            sentiment = ('BULLISH' if avg_ratio >= 0.75
                         else 'BEARISH' if avg_ratio <= 0.55
                         else 'NEUTRAL')

            result = {
                'avg_upvote_ratio': avg_ratio, 'relevant_posts': relevant_posts,
                'sentiment': sentiment, 'top_titles': relevant_titles[:3],
            }
            self._set_cache(key, result)
            logger.debug(
                f"Reddit [{currency}]: {sentiment} | "
                f"Ratio: {avg_ratio:.2f} | Posts: {relevant_posts}"
            )
            return result

        except Exception as e:
            logger.debug(f"Reddit failed: {e}")
            return {
                'avg_upvote_ratio': 0.5, 'relevant_posts': 0,
                'sentiment': 'NEUTRAL', 'top_titles': [],
            }

    # ------------------------------------------------------------------ #
    # 4. Open Interest Sentiment                                           #
    # ------------------------------------------------------------------ #

    async def get_open_interest_sentiment(self, symbol: str) -> dict:
        """OI change sebagai proxy market positioning strength."""
        key = f"oi_{symbol}"
        if self._is_cache_valid(key):
            return self._cache[key]

        if not self.exchange:
            return {'oi_change_pct': 0.0, 'signal': 'NEUTRAL',
                    'sentiment': 'OI unavailable'}

        try:
            oi_hist = await self.exchange.fetch_open_interest_history(
                symbol, timeframe='1h', limit=3
            )
            if oi_hist and len(oi_hist) >= 2:
                oi_prev = float(oi_hist[-2].get('openInterestAmount', 0) or 0)
                oi_curr = float(oi_hist[-1].get('openInterestAmount', 0) or 0)

                if oi_prev > 0:
                    oi_pct = ((oi_curr - oi_prev) / oi_prev) * 100

                    if oi_pct > 3.0:
                        signal    = 'STRONG_TREND'
                        sentiment = f"OI +{oi_pct:.2f}% (strong positioning)"
                    elif oi_pct > 1.0:
                        signal    = 'MILD_TREND'
                        sentiment = f"OI +{oi_pct:.2f}% (building)"
                    elif oi_pct < -3.0:
                        signal    = 'UNWINDING'
                        sentiment = f"OI {oi_pct:.2f}% (mass exit)"
                    elif oi_pct < -1.0:
                        signal    = 'MILD_UNWINDING'
                        sentiment = f"OI {oi_pct:.2f}% (unwinding)"
                    else:
                        signal    = 'NEUTRAL'
                        sentiment = f"OI {oi_pct:.2f}% (stable)"

                    result = {
                        'oi_change_pct': oi_pct,
                        'signal': signal,
                        'sentiment': sentiment,
                    }
                    self._set_cache(key, result, ttl=self.TTL_OI)
                    logger.debug(f"[{symbol}] OI: {signal} | {oi_pct:.2f}%")
                    return result

        except Exception as e:
            logger.debug(f"[{symbol}] OI failed (non-critical): {e}")

        return {'oi_change_pct': 0.0, 'signal': 'NEUTRAL',
                'sentiment': 'OI unavailable'}

    # ------------------------------------------------------------------ #
    # 5. Order Book Imbalance (OBI)                                        #
    # ------------------------------------------------------------------ #

    async def get_order_book_imbalance(self, symbol: str, depth: int = 20) -> dict:
        """
        Order Book Imbalance — microstructure signal terkuat.

        Formula: OBI = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        Range: -1.0 (full sell pressure) to +1.0 (full buy pressure)

        OBI > +0.20  = buy pressure dominan → BULLISH signal
        OBI < -0.20  = sell pressure dominan → BEARISH signal
        -0.20 to +0.20 = balanced → NEUTRAL

        Keunggulan OBI:
        - Real-time (tidak perlu historical data)
        - Langsung mencerminkan intention trader sekarang
        - Tersedia di testnet maupun mainnet
        """
        key = f"obi_{symbol}"
        if self._is_cache_valid(key):
            return self._cache[key]

        if not self.exchange:
            return {
                'obi': 0.0, 'signal': 'NEUTRAL',
                'bid_volume': 0.0, 'ask_volume': 0.0,
                'sentiment': 'OBI unavailable'
            }

        try:
            # Fetch order book — depth 20 level sudah cukup
            ob = await self.exchange.fetch_order_book(symbol, limit=depth)

            bids = ob.get('bids', [])  # [[price, volume], ...]
            asks = ob.get('asks', [])  # [[price, volume], ...]

            if not bids or not asks:
                raise ValueError("Empty order book")

            # Hitung total volume di bid dan ask
            bid_vol = sum(float(b[1]) for b in bids[:depth])
            ask_vol = sum(float(a[1]) for a in asks[:depth])
            total   = bid_vol + ask_vol

            if total == 0:
                raise ValueError("Zero total volume")

            obi = (bid_vol - ask_vol) / total

            # Interpretasi OBI
            if obi > 0.30:
                signal    = 'STRONG_BULL'
                sentiment = f"OBI +{obi:.3f} — strong buy pressure"
            elif obi > 0.15:
                signal    = 'MILD_BULL'
                sentiment = f"OBI +{obi:.3f} — mild buy pressure"
            elif obi < -0.30:
                signal    = 'STRONG_BEAR'
                sentiment = f"OBI {obi:.3f} — strong sell pressure"
            elif obi < -0.15:
                signal    = 'MILD_BEAR'
                sentiment = f"OBI {obi:.3f} — mild sell pressure"
            else:
                signal    = 'NEUTRAL'
                sentiment = f"OBI {obi:.3f} — balanced book"

            result = {
                'obi': obi,
                'bid_volume': bid_vol,
                'ask_volume': ask_vol,
                'bid_ask_ratio': bid_vol / ask_vol if ask_vol > 0 else 1.0,
                'signal': signal,
                'sentiment': sentiment,
            }
            self._set_cache(key, result, ttl=self.TTL_OBI)
            logger.debug(
                f"[{symbol}] OBI: {obi:+.3f} ({signal}) | "
                f"Bid: {bid_vol:.2f} Ask: {ask_vol:.2f}"
            )
            return result

        except Exception as e:
            logger.debug(f"[{symbol}] OBI failed (non-critical): {e}")
            return {
                'obi': 0.0, 'signal': 'NEUTRAL',
                'bid_volume': 0.0, 'ask_volume': 0.0,
                'sentiment': 'OBI unavailable'
            }

    # ------------------------------------------------------------------ #
    # Combined context — semua 5 sumber                                   #
    # ------------------------------------------------------------------ #

    async def get_sentiment_context(self, symbol: str) -> dict:
        """
        Fetch semua 5 sumber secara concurrent.
        Gagal satu tidak menghentikan yang lain.
        """
        currency = symbol.split('/')[0]

        results = await asyncio.gather(
            self.get_fear_greed(),
            self.get_coingecko_news(currency),
            self.get_reddit_sentiment(currency),
            self.get_open_interest_sentiment(symbol),
            self.get_order_book_imbalance(symbol),
            return_exceptions=True
        )

        def safe(r, fallback):
            return fallback if isinstance(r, Exception) else r

        fg, news, reddit, oi, obi = results

        return {
            'fear_greed': safe(fg,
                {'value': 50, 'label': 'Neutral', 'trading_bias': 'NEUTRAL'}),
            'news': safe(news,
                {'headlines': [], 'sentiment': 'NEUTRAL', 'sentiment_score': 0.5,
                 'bull_score': 0, 'bear_score': 0}),
            'reddit': safe(reddit,
                {'avg_upvote_ratio': 0.5, 'sentiment': 'NEUTRAL',
                 'relevant_posts': 0, 'top_titles': []}),
            'oi_sentiment': safe(oi,
                {'oi_change_pct': 0.0, 'signal': 'NEUTRAL',
                 'sentiment': 'OI unavailable'}),
            'obi': safe(obi,
                {'obi': 0.0, 'signal': 'NEUTRAL',
                 'bid_volume': 0.0, 'ask_volume': 0.0,
                 'sentiment': 'OBI unavailable'}),
        }

    def format_for_prompt(self, context: dict, symbol: str) -> str:
        """
        Format semua 5 sumber menjadi satu blok teks untuk R1 prompt.
        OBI ditempatkan pertama karena paling real-time dan actionable.
        """
        fg     = context.get('fear_greed', {})
        news   = context.get('news', {})
        reddit = context.get('reddit', {})
        oi     = context.get('oi_sentiment', {})
        obi    = context.get('obi', {})

        headlines_text = ""
        for i, h in enumerate(news.get('headlines', [])[:3], 1):
            headlines_text += f"  {i}. {h}\n"
        if not headlines_text:
            headlines_text = "  No headlines available\n"

        reddit_titles = ""
        for t in reddit.get('top_titles', [])[:2]:
            reddit_titles += f"  - {t}\n"
        if not reddit_titles:
            reddit_titles = "  No relevant posts\n"

        # OBI interpretation helper
        obi_val = obi.get('obi', 0.0)
        obi_interpretation = (
            "STRONGLY confirms BULL direction" if obi_val > 0.30 else
            "mildly confirms BULL direction"   if obi_val > 0.15 else
            "STRONGLY confirms BEAR direction" if obi_val < -0.30 else
            "mildly confirms BEAR direction"   if obi_val < -0.15 else
            "neutral — no directional pressure"
        )

        return f"""=== MARKET MICROSTRUCTURE & SENTIMENT (5 SOURCES) ===

[1] ORDER BOOK IMBALANCE (most real-time signal):
    OBI Score: {obi_val:+.3f} (range -1.0 to +1.0)
    Signal: {obi.get('signal', 'NEUTRAL')} — {obi_interpretation}
    Bid Volume: {obi.get('bid_volume', 0):.2f} | Ask Volume: {obi.get('ask_volume', 0):.2f}
    B/A Ratio: {obi.get('bid_ask_ratio', 1.0):.3f}
    Note: OBI > +0.15 = buy pressure, OBI < -0.15 = sell pressure

[2] Fear & Greed Index: {fg.get('value', 50)}/100 ({fg.get('label', 'Neutral')})
    Trading Bias: {fg.get('trading_bias', 'NEUTRAL')}
    Note: <25=Extreme Fear(contrarian BULL), >75=Extreme Greed(contrarian BEAR)

[3] CoinGecko News: {news.get('sentiment', 'NEUTRAL')}
    Bullish keywords: {news.get('bull_score', 0)} | Bearish keywords: {news.get('bear_score', 0)}
    Top Headlines:
{headlines_text}
[4] Reddit r/CryptoCurrency: {reddit.get('sentiment', 'NEUTRAL')}
    Avg upvote ratio: {reddit.get('avg_upvote_ratio', 0.5):.2f} | Posts: {reddit.get('relevant_posts', 0)}
    Top discussions:
{reddit_titles}
[5] Open Interest: {oi.get('sentiment', 'OI unavailable')}
    Signal: {oi.get('signal', 'NEUTRAL')}
    Note: OI rising=trend strengthening, OI falling=unwinding

DECISION WEIGHTS: Technical=70% | OBI=15% | Macro Sentiment=15%
OBI + Technical agree = strong confirmation (+0.10 confidence)
OBI contradicts Technical = reduce confidence (-0.05)
All 5 sources agree = maximum confidence boost (+0.15)"""
