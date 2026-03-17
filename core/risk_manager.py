from typing import Optional
from core.binance_client import BinanceClient
from loguru import logger


class RiskManager:
    """
    Risk Manager with Atomic Order Execution.
    Ensures Stop Loss is placed immediately after entry to server.
    """
    
    def __init__(self, client: BinanceClient):
        self.client = client
    
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
        Execute entry order and immediately place Stop Loss on server.
        
        Args:
            symbol: Trading pair (e.g., 'BTC/USDT')
            side: 'buy' (Long) or 'sell' (Short) for entry
            amount: Position size in base asset
            stop_loss_price: Stop loss trigger price (mandatory)
            entry_price: Required for LIMIT orders, None for MARKET
            order_type: 'MARKET' or 'LIMIT'
            
        Returns:
            Dict with entry_order and stop_loss_order details
            
        Raises:
            Exception: If entry succeeds but SL fails, attempts emergency close
        """
        # Round amounts and prices to exchange precision
        amount = self.client.round_amount(symbol, amount)
        stop_loss_price = self.client.round_price(symbol, stop_loss_price)
        
        if entry_price:
            entry_price = self.client.round_price(symbol, entry_price)
        
        # Determine SL side (opposite of entry)
        sl_side = 'sell' if side == 'buy' else 'buy'
        
        try:
            # 1. Place Entry Order
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
            
            logger.info(f"Entry order placed: {entry_order['id']} | Side: {side.upper()} | Amount: {amount}")
            
            # 2. Immediately place Stop Loss (Atomic - server side)
            # Binance Futures: STOP_MARKET order with stopPrice parameter
            sl_order = await self.client.exchange.create_order(
                symbol=symbol,
                type='STOP_MARKET',
                side=sl_side,
                amount=amount,
                price=None,  # Market price when triggered
                params={'stopPrice': stop_loss_price}
            )
            
            logger.info(f"Stop Loss placed at {stop_loss_price}: {sl_order['id']}")
            
            return {
                'entry': entry_order,
                'stop_loss': sl_order,
                'status': 'success'
            }
            
        except Exception as e:
            logger.error(f"Atomic order failed: {e}")
            # Attempt emergency close if entry succeeded but SL failed
            await self._emergency_close(symbol)
            raise
    
    async def _emergency_close(self, symbol: str):
        """Emergency position close if SL placement fails."""
        try:
            positions = await self.client.exchange.fetch_positions([symbol])
            for pos in positions:
                if pos['symbol'] == symbol and abs(pos['contracts']) > 0:
                    side = 'sell' if pos['side'] == 'long' else 'buy'
                    await self.client.exchange.create_market_order(
                        symbol=symbol,
                        side=side,
                        amount=abs(pos['contracts']),
                        params={'reduceOnly': True}
                    )
                    logger.warning(f"Emergency close executed for {symbol}")
        except Exception as e:
            logger.error(f"Emergency close failed: {e}")
