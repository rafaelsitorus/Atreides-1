"""
utils/news_fetcher.py
Fase 3C — 4 sumber sentiment gratis tanpa API key berbayar.

1. Fear & Greed Index  — alternative.me (no key)
2. CoinGecko News      — coingecko.com (no key)
3. Reddit RSS          — reddit.com (no key)
4. Binance L/S Ratio   — via CCXT (pakai key yang sudah ada)
"""
import asyncio
import aiohttp
from datetime import datetime, timezone
from typing import Optional
from loguru import logger


class NewsFetcher:
    """
    Async multi-source sentiment fetcher.
    Semua sumber di-cache 15 menit untuk hemat bandwidth.
    """

    FEAR_GREED_URL  = "https://api.alternative.me/fng/?limit=1"
    COINGECKO_URL   = "https://api.coingecko.com/api/v3/news"
    REDDIT_URL      = "https://www.reddit.com/r/CryptoCurrency/hot.json?limit=20"
    CACHE_TTL       = 900  # 15 menit

    def __init__(self, exchange=None):
        """
        Args:
            exchange: ccxt exchange instance untuk fetch Long/Short ratio
        """
        self.exchange = exchange
        self._cache: dict = {}
        self._cache_time: dict = {}

    # ------------------------------------------------------------------ #
    # Cache helpers                                                        #
    # ------------------------------------------------------------------ #

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache_time:
            return False
        age = datetime.now(timezone.utc).timestamp() - self._cache_time[key]
        return age < self.CACHE_TTL

    def _set_cache(self, key: str, value):
        self._cache[key] = value
        self._cache_time[key] = datetime.now(timezone.utc).timestamp()

    # ------------------------------------------------------------------ #
    # 1. Fear & Greed Index                                                #
    # ------------------------------------------------------------------ #

    async def get_fear_greed(self) -> dict:
        """
        Fear & Greed Index dari alternative.me.
        Contrarian signal: Extreme Fear → BULL bias, Extreme Greed → BEAR bias.
        """
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
                trading_bias = 'BULLISH'       # Extreme Fear = contrarian buy
            elif value <= 45:
                trading_bias = 'MILD_BULLISH'
            elif value <= 55:
                trading_bias = 'NEUTRAL'
            elif value <= 75:
                trading_bias = 'MILD_BEARISH'
            else:
                trading_bias = 'BEARISH'       # Extreme Greed = contrarian sell

            result = {
                'value': value,
                'label': label,
                'trading_bias': trading_bias,
            }
            self._set_cache(key, result)
            logger.debug(f"Fear & Greed: {value}/100 ({label}) → {trading_bias}")
            return result

        except Exception as e:
            logger.debug(f"Fear & Greed failed: {e}")
            return {'value': 50, 'label': 'Neutral', 'trading_bias': 'NEUTRAL'}

    # ------------------------------------------------------------------ #
    # 2. CoinGecko News                                                    #
    # ------------------------------------------------------------------ #

    async def get_coingecko_news(self, currency: str = "bitcoin") -> dict:
        """
        Top crypto headlines dari CoinGecko — gratis tanpa API key.
        Keyword analysis untuk detect sentiment shift.
        """
        key = f"cg_news_{currency}"
        if self._is_cache_valid(key):
            return self._cache[key]

        # Mapping symbol ke CoinGecko name
        coin_map = {
            'BTC': 'bitcoin', 'ETH': 'ethereum',
            'SOL': 'solana',  'BNB': 'binancecoin',
        }
        coin = coin_map.get(currency.upper(), 'bitcoin')

        # Bearish keywords
        BEARISH_KW = [
            'hack', 'exploit', 'ban', 'crackdown', 'lawsuit',
            'sec', 'regulation', 'crash', 'liquidat', 'fear',
            'dump', 'sell', 'warning', 'risk', 'scam', 'fraud'
        ]
        # Bullish keywords
        BULLISH_KW = [
            'etf', 'approval', 'adoption', 'launch', 'partnership',
            'upgrade', 'bullish', 'rally', 'ath', 'record',
            'institutional', 'buy', 'accumul', 'breakout'
        ]

        try:
            async with aiohttp.ClientSession() as session:
                headers = {'accept': 'application/json'}
                async with session.get(
                    self.COINGECKO_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()

            articles = data.get('data', [])[:15]
            headlines = []
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

            total = bull_score + bear_score
            sentiment_score = bull_score / total if total > 0 else 0.5

            if sentiment_score >= 0.6:
                sentiment = 'BULLISH'
            elif sentiment_score <= 0.4:
                sentiment = 'BEARISH'
            else:
                sentiment = 'NEUTRAL'

            result = {
                'headlines': headlines[:5],
                'bull_score': bull_score,
                'bear_score': bear_score,
                'sentiment_score': sentiment_score,
                'sentiment': sentiment,
            }
            self._set_cache(key, result)
            logger.debug(
                f"CoinGecko News [{currency}]: "
                f"{sentiment} | Bull:{bull_score} Bear:{bear_score}"
            )
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
        """
        Reddit r/CryptoCurrency hot posts sentiment.
        Upvote ratio sebagai proxy bullish/bearish crowd sentiment.
        """
        key = f"reddit_{currency}"
        if self._is_cache_valid(key):
            return self._cache[key]

        try:
            headers = {'User-Agent': 'Atreides-1/1.0 trading bot'}
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    self.REDDIT_URL,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    data = await resp.json()

            posts = data.get('data', {}).get('children', [])

            total_upvote_ratio = 0.0
            relevant_posts = 0
            relevant_titles = []
            coin_keywords = {
                'BTC': ['btc', 'bitcoin'],
                'ETH': ['eth', 'ethereum'],
                'SOL': ['sol', 'solana'],
            }
            kws = coin_keywords.get(currency.upper(), ['btc', 'bitcoin', 'crypto'])

            for post in posts:
                post_data = post.get('data', {})
                title = post_data.get('title', '').lower()
                upvote_ratio = float(post_data.get('upvote_ratio', 0.5))
                score = int(post_data.get('score', 0))

                # Hanya ambil post yang relevan dengan coin
                is_relevant = any(kw in title for kw in kws) or score > 500
                if is_relevant:
                    total_upvote_ratio += upvote_ratio
                    relevant_posts += 1
                    relevant_titles.append(post_data.get('title', ''))

            avg_ratio = total_upvote_ratio / relevant_posts if relevant_posts > 0 else 0.5

            if avg_ratio >= 0.75:
                sentiment = 'BULLISH'
            elif avg_ratio <= 0.55:
                sentiment = 'BEARISH'
            else:
                sentiment = 'NEUTRAL'

            result = {
                'avg_upvote_ratio': avg_ratio,
                'relevant_posts': relevant_posts,
                'sentiment': sentiment,
                'top_titles': relevant_titles[:3],
            }
            self._set_cache(key, result)
            logger.debug(
                f"Reddit [{currency}]: {sentiment} | "
                f"Ratio: {avg_ratio:.2f} | Posts: {relevant_posts}"
            )
            return result

        except Exception as e:
            logger.debug(f"Reddit sentiment failed: {e}")
            return {
                'avg_upvote_ratio': 0.5, 'relevant_posts': 0,
                'sentiment': 'NEUTRAL', 'top_titles': [],
            }

    # ------------------------------------------------------------------ #
    # 4. Binance Open Interest Change                                      #
    # ------------------------------------------------------------------ #

    async def get_open_interest_sentiment(self, symbol: str) -> dict:
        """
        Open Interest change sebagai proxy market positioning.
        OI naik + harga naik = trend BULL kuat
        OI naik + harga turun = trend BEAR kuat
        OI turun = posisi ditutup, trend melemah
        """
        key = f"oi_sentiment_{symbol}"
        if self._is_cache_valid(key):
            return self._cache[key]

        if not self.exchange:
            return {'oi_change_pct': 0.0, 'signal': 'NEUTRAL', 'sentiment': 'OI data unavailable'}

        try:
            # Ambil OI history 2 candle 1h terakhir
            oi_hist = await self.exchange.fetch_open_interest_history(
                symbol, timeframe='1h', limit=3
            )

            if oi_hist and len(oi_hist) >= 2:
                oi_prev = float(oi_hist[-2].get('openInterestAmount', 0) or 0)
                oi_curr = float(oi_hist[-1].get('openInterestAmount', 0) or 0)

                if oi_prev > 0:
                    oi_change_pct = ((oi_curr - oi_prev) / oi_prev) * 100

                    if oi_change_pct > 3.0:
                        signal = 'STRONG_TREND'
                        sentiment = f"OI +{oi_change_pct:.2f}% (strong positioning)"
                    elif oi_change_pct > 1.0:
                        signal = 'MILD_TREND'
                        sentiment = f"OI +{oi_change_pct:.2f}% (building positions)"
                    elif oi_change_pct < -3.0:
                        signal = 'UNWINDING'
                        sentiment = f"OI {oi_change_pct:.2f}% (mass exit — caution)"
                    elif oi_change_pct < -1.0:
                        signal = 'MILD_UNWINDING'
                        sentiment = f"OI {oi_change_pct:.2f}% (light unwinding)"
                    else:
                        signal = 'NEUTRAL'
                        sentiment = f"OI {oi_change_pct:.2f}% (stable)"

                    result = {
                        'oi_change_pct': oi_change_pct,
                        'signal': signal,
                        'sentiment': sentiment,
                    }
                    self._set_cache(key, result)
                    logger.debug(f"[{symbol}] OI Sentiment: {signal} | {oi_change_pct:.2f}%")
                    return result

        except Exception as e:
            logger.debug(f"[{symbol}] OI sentiment failed (non-critical): {e}")

        return {'oi_change_pct': 0.0, 'signal': 'NEUTRAL', 'sentiment': 'OI data unavailable'}

    # ------------------------------------------------------------------ #
    # Combined context                                                     #
    # ------------------------------------------------------------------ #

    async def get_sentiment_context(self, symbol: str) -> dict:
        """
        Fetch semua 4 sumber secara concurrent.
        Non-blocking — gagal satu tidak menghentikan yang lain.
        """
        currency = symbol.split('/')[0]

        fear_greed, cg_news, reddit, ls_ratio = await asyncio.gather(
            self.get_fear_greed(),
            self.get_coingecko_news(currency),
            self.get_reddit_sentiment(currency),
            self.get_open_interest_sentiment(symbol),
            return_exceptions=True
        )

        # Graceful fallback jika ada yang gagal
        def safe(result, fallback):
            return fallback if isinstance(result, Exception) else result

        return {
            'fear_greed': safe(fear_greed,
                {'value': 50, 'label': 'Neutral', 'trading_bias': 'NEUTRAL'}),
            'news': safe(cg_news,
                {'headlines': [], 'sentiment': 'NEUTRAL', 'sentiment_score': 0.5,
                 'bull_score': 0, 'bear_score': 0}),
            'reddit': safe(reddit,
                {'avg_upvote_ratio': 0.5, 'sentiment': 'NEUTRAL',
                 'relevant_posts': 0, 'top_titles': []}),
            'oi_sentiment': safe(ls_ratio,
                {'oi_change_pct': 0.0, 'signal': 'NEUTRAL', 'sentiment': 'OI data unavailable'}),
        }

    def format_for_prompt(self, context: dict, symbol: str) -> str:
        """
        Format semua 4 sumber menjadi satu blok teks untuk R1 prompt.
        """
        fg  = context.get('fear_greed', {})
        news = context.get('news', {})
        reddit = context.get('reddit', {})
        oi  = context.get('oi_sentiment', {})

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

        return f"""=== MACRO SENTIMENT (4 SOURCES) ===

[1] Fear & Greed Index: {fg.get('value', 50)}/100 ({fg.get('label', 'Neutral')})
    Trading Bias: {fg.get('trading_bias', 'NEUTRAL')}
    Note: <25=Extreme Fear(contrarian BULL), >75=Extreme Greed(contrarian BEAR)

[2] CoinGecko News Sentiment: {news.get('sentiment', 'NEUTRAL')}
    Bullish keywords: {news.get('bull_score', 0)} | Bearish keywords: {news.get('bear_score', 0)}
    Top Headlines:
{headlines_text}
[3] Reddit r/CryptoCurrency Sentiment: {reddit.get('sentiment', 'NEUTRAL')}
    Avg upvote ratio: {reddit.get('avg_upvote_ratio', 0.5):.2f} | Relevant posts: {reddit.get('relevant_posts', 0)}
    Top discussions:
{reddit_titles}
[4] Binance Open Interest: {oi.get('sentiment', 'OI data unavailable')}
    Signal: {oi.get('signal', 'NEUTRAL')}
    Note: OI rising=trend strengthening, OI falling=positions unwinding

SENTIMENT WEIGHT IN DECISION: 25% (technical = 75%)
All 4 sources agree = +0.10 confidence boost
3/4 agree = +0.05 confidence boost
Split/conflicting = no adjustment"""
