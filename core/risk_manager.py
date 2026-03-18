from typing import Optional
from core.binance_client import BinanceClient
from loguru import logger


class RiskManager:
    """
    Risk Manager with Atomic Order Execution.
    Entry + Stop Loss + Take Profit dikirim ke server secara atomic.
    """

    def __init__(self, client: BinanceClient, risk_reward_ratio: float = 2.0):
        """
        Args:
            client: BinanceClient instance
            risk_reward_ratio: TP = SL distance × ratio (default 2.0 = 1:2 RR)
        """
        self.client = client
        self.rr_ratio = risk_reward_ratio

    async def execute_atomic_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_loss_price: float,
        entry_price: Optional[float] = None,
        order_type: str = 'MARKET'
    ) -> dict:
        """
        Execute entry + Stop Loss + Take Profit secara atomic ke server Binance.

        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            side: 'buy' (Long) atau 'sell' (Short)
            amount: Position size in base asset
            stop_loss_price: Harga trigger SL (mandatory)
            entry_price: Harga entry untuk LIMIT order, None untuk MARKET
            order_type: 'MARKET' atau 'LIMIT'

        Returns:
            Dict berisi entry_order, stop_loss_order, take_profit_order
        """
        amount = self.client.round_amount(symbol, amount)
        stop_loss_price = self.client.round_price(symbol, stop_loss_price)

        if entry_price:
            entry_price = self.client.round_price(symbol, entry_price)

        # Sisi berlawanan untuk SL dan TP
        exit_side = 'sell' if side == 'buy' else 'buy'

        try:
            # 1. Entry Order
            if order_type == 'MARKET':
                if side == 'buy':
                    entry_order = await self.client.exchange.create_market_buy_order(symbol, amount)
                else:
                    entry_order = await self.client.exchange.create_market_sell_order(symbol, amount)
            else:
                if side == 'buy':
                    entry_order = await self.client.exchange.create_limit_buy_order(symbol, amount, entry_price)
                else:
                    entry_order = await self.client.exchange.create_limit_sell_order(symbol, amount, entry_price)

            fill_price = float(entry_order.get('average') or entry_order.get('price') or entry_price or stop_loss_price)
            logger.info(f"Entry order placed: {entry_order['id']} | Side: {side.upper()} | Amount: {amount} | Fill: ~{fill_price:.2f}")

            # 2. Hitung Take Profit berdasarkan RR ratio
            sl_distance = abs(fill_price - stop_loss_price)
            if side == 'buy':
                take_profit_price = fill_price + (sl_distance * self.rr_ratio)
            else:
                take_profit_price = fill_price - (sl_distance * self.rr_ratio)
            take_profit_price = self.client.round_price(symbol, take_profit_price)

            # 3. Stop Loss — STOP_MARKET server-side
            sl_order = await self.client.exchange.create_order(
                symbol=symbol,
                type='STOP_MARKET',
                side=exit_side,
                amount=amount,
                price=None,
                params={
                    'stopPrice': stop_loss_price,
                    'closePosition': True
                }
            )
            logger.info(f"Stop Loss placed at {stop_loss_price}: {sl_order['id']}")

            # 4. Take Profit — TAKE_PROFIT_MARKET server-side
            tp_order = await self.client.exchange.create_order(
                symbol=symbol,
                type='TAKE_PROFIT_MARKET',
                side=exit_side,
                amount=amount,
                price=None,
                params={
                    'stopPrice': take_profit_price,
                    'closePosition': True
                }
            )
            logger.info(f"Take Profit placed at {take_profit_price}: {tp_order['id']} | RR: 1:{self.rr_ratio}")

            return {
                'entry': entry_order,
                'stop_loss': sl_order,
                'take_profit': tp_order,
                'sl_price': stop_loss_price,
                'tp_price': take_profit_price,
                'status': 'success'
            }

        except Exception as e:
            logger.error(f"Atomic order failed: {e}")
            await self._emergency_close(symbol)
            raise

    async def _emergency_close(self, symbol: str):
        """Emergency close jika SL/TP placement gagal setelah entry berhasil."""
        try:
            positions = await self.client.exchange.fetch_positions([symbol])
            for pos in positions:
                if pos['symbol'] == symbol and abs(float(pos['contracts'] or 0)) > 0:
                    side = 'sell' if pos['side'] == 'long' else 'buy'
                    await self.client.exchange.create_market_order(
                        symbol=symbol,
                        side=side,
                        amount=abs(float(pos['contracts'])),
                        params={'reduceOnly': True}
                    )
                    logger.warning(f"Emergency close executed for {symbol}")
        except Exception as e:
            logger.error(f"Emergency close failed: {e}")