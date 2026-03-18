"""
core/telegram_bot.py
Telegram C2 daemon — full remote control dengan inline buttons.
Commands: /status /pause /resume /stop /close /closeall /help
"""
import asyncio
import os
import sys
from datetime import datetime
from typing import Optional, Callable, Awaitable
from loguru import logger

try:
    from telegram import Update, Bot, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.ext import (
        Application, CommandHandler, CallbackQueryHandler, ContextTypes
    )
    TELEGRAM_AVAILABLE = True
except ImportError:
    TELEGRAM_AVAILABLE = False
    logger.warning("python-telegram-bot not installed. Run: pip install python-telegram-bot")


class TelegramC2:
    """
    Full Telegram C2 daemon untuk Atreides-1.
    - Notifikasi real-time: trade open, regime shift, PnL, circuit breaker
    - Remote commands: pause, resume, stop, close posisi
    - Inline buttons: konfirmasi close order langsung dari notifikasi
    """

    def __init__(
        self,
        token: str,
        chat_id: str,
        on_pause: Optional[Callable[[], Awaitable[None]]] = None,
        on_resume: Optional[Callable[[], Awaitable[None]]] = None,
        on_close_position: Optional[Callable[[str], Awaitable[dict]]] = None,
        on_close_all: Optional[Callable[[], Awaitable[list]]] = None,
        get_status: Optional[Callable[[], dict]] = None,
    ):
        self.token = token
        self.chat_id = str(chat_id)
        self.on_pause = on_pause
        self.on_resume = on_resume
        self.on_close_position = on_close_position
        self.on_close_all = on_close_all
        self.get_status = get_status
        self.app = None
        self._paused = False

        if not TELEGRAM_AVAILABLE:
            logger.error("Telegram bot disabled — install python-telegram-bot")

    # ------------------------------------------------------------------ #
    # Auth check                                                           #
    # ------------------------------------------------------------------ #

    def _is_authorized(self, update: Update) -> bool:
        """Tolak semua pesan dari chat ID yang tidak dikenal."""
        return str(update.effective_chat.id) == self.chat_id

    # ------------------------------------------------------------------ #
    # Senders                                                              #
    # ------------------------------------------------------------------ #

    async def send(self, message: str, reply_markup=None):
        """Kirim pesan ke Telegram. Silent fail jika tidak available."""
        if not TELEGRAM_AVAILABLE or not self.token:
            return
        try:
            bot = Bot(token=self.token)
            await bot.send_message(
                chat_id=self.chat_id,
                text=message,
                parse_mode='HTML',
                reply_markup=reply_markup
            )
        except Exception as e:
            logger.warning(f"Telegram send failed: {e}")

    async def notify_trade(self, symbol: str, side: str, amount: float,
                           entry: float, sl: float, tp: float, rr: float):
        """
        Notifikasi trade baru dengan inline button untuk close manual.
        Tombol 'Close Now' kirim command /close {symbol} langsung.
        """
        icon = "🟢" if side == "buy" else "🔴"
        direction = "LONG" if side == "buy" else "SHORT"
        msg = (
            f"{icon} <b>NEW TRADE — {symbol}</b>\n"
            f"Direction: <b>{direction}</b>\n"
            f"Amount: <code>{amount}</code>\n"
            f"Entry:  <code>${entry:,.2f}</code>\n"
            f"SL:     <code>${sl:,.2f}</code>\n"
            f"TP:     <code>${tp:,.2f}</code>\n"
            f"RR:     1:{rr}\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        # Inline button — tekan untuk close posisi ini
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    f"❌ Close {symbol}",
                    callback_data=f"close_{symbol}"
                ),
                InlineKeyboardButton(
                    "⏸️ Pause Bot",
                    callback_data="pause"
                ),
            ]
        ])
        await self.send(msg, reply_markup=keyboard)

    async def notify_circuit_breaker(self, drawdown_pct: float, equity: float):
        """Alert kritis saat circuit breaker trip."""
        msg = (
            f"🚨 <b>CIRCUIT BREAKER TRIPPED!</b>\n"
            f"Drawdown: <b>{drawdown_pct:.2f}%</b>\n"
            f"Equity: <code>${equity:.2f}</code>\n"
            f"Trading halted for 24 hours.\n"
            f"Send /resume to override manually."
        )
        keyboard = InlineKeyboardMarkup([
            [InlineKeyboardButton("▶️ Resume Trading", callback_data="resume")],
            [InlineKeyboardButton("❌ Close All Positions", callback_data="closeall")],
        ])
        await self.send(msg, reply_markup=keyboard)

    async def notify_regime_shift(self, symbol: str, old: str, new: str,
                                  confidence: float = 0.0):
        """Notifikasi perubahan market regime."""
        icons = {'BULL': '🟢', 'BEAR': '🔴', 'SIDEWAYS': '⬜'}
        msg = (
            f"{icons.get(new, '🔄')} <b>REGIME SHIFT — {symbol}</b>\n"
            f"{old} → <b>{new}</b>\n"
            f"Confidence: {confidence:.0%}\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        await self.send(msg)

    async def notify_pnl_update(self, pnl: float, equity: float,
                                trades: int, win_rate: float):
        """Periodic PnL update setiap jam."""
        icon = "📈" if pnl >= 0 else "📉"
        msg = (
            f"{icon} <b>Hourly PnL Update</b>\n"
            f"Net PnL:  <code>${pnl:+.4f}</code>\n"
            f"Equity:   <code>${equity:.2f}</code>\n"
            f"Trades:   {trades}\n"
            f"Win Rate: {win_rate:.0f}%\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("📊 Full Status", callback_data="status"),
                InlineKeyboardButton("❌ Close All", callback_data="closeall"),
            ]
        ])
        await self.send(msg, reply_markup=keyboard)

    async def notify_position_closed(self, symbol: str, pnl: float, reason: str):
        """Notifikasi saat posisi ditutup."""
        icon = "✅" if pnl >= 0 else "🔴"
        msg = (
            f"{icon} <b>POSITION CLOSED — {symbol}</b>\n"
            f"PnL: <code>${pnl:+.4f}</code>\n"
            f"Reason: {reason}\n"
            f"<i>{datetime.now().strftime('%H:%M:%S')}</i>"
        )
        await self.send(msg)

    async def notify_startup(self, symbols: list, leverage: int):
        """Notifikasi saat sistem start."""
        msg = (
            f"⚔️ <b>ATREIDES-1 ONLINE</b>\n\n"
            f"Symbols:  {', '.join(symbols)}\n"
            f"Leverage: {leverage}x\n"
            f"TF:       15m / 1h / 4h\n\n"
            f"Commands:\n"
            f"/status • /pause • /resume\n"
            f"/close BTC/USDT • /closeall • /stop\n\n"
            f"<i>{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>"
        )
        await self.send(msg)

    # ------------------------------------------------------------------ #
    # Command handlers                                                     #
    # ------------------------------------------------------------------ #

    async def _cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/status — tampilkan status lengkap sistem + posisi aktif."""
        if not self._is_authorized(update):
            return

        status_data = self.get_status() if self.get_status else {}
        paused_str = "⏸️ PAUSED" if self._paused else "▶️ RUNNING"
        cb = status_data.get("circuit_breaker", {})
        tripped = "🚨 TRIPPED" if cb.get("tripped") else "✅ OK"

        msg = (
            f"📊 <b>ATREIDES-1 STATUS</b>\n"
            f"{'─'*28}\n"
            f"System:   <b>{paused_str}</b>\n"
            f"CB:       <b>{tripped}</b>\n"
            f"{'─'*28}\n"
            f"<b>Today</b>\n"
            f"PnL:      <code>${cb.get('pnl_today', 0):+.4f}</code>\n"
            f"Net PnL:  <code>${cb.get('net_pnl_today', 0):+.4f}</code>\n"
            f"Fees:     <code>${cb.get('fees_today', 0):.4f}</code>\n"
            f"Trades:   {cb.get('trades_today', 0)} "
            f"(W:{cb.get('wins_today',0)} L:{cb.get('losses_today',0)})\n"
            f"Win Rate: {cb.get('win_rate', 0):.0f}%\n"
            f"Drawdown: {cb.get('drawdown_pct', 0):.2f}% "
            f"/ {cb.get('max_drawdown_pct', 2):.0f}% max\n"
            f"{'─'*28}\n"
            f"<b>Positions</b>\n"
        )

        positions = status_data.get("positions", [])
        if positions:
            for pos in positions:
                pnl = float(pos.get('unrealizedPnl') or 0)
                icon = "🟢" if pnl >= 0 else "🔴"
                msg += (
                    f"{icon} <b>{pos.get('symbol','')}</b> "
                    f"{pos.get('side','').upper()} | "
                    f"PnL: <code>${pnl:+.4f}</code>\n"
                )
        else:
            msg += "No active positions\n"

        keyboard = InlineKeyboardMarkup([
            [
                InlineKeyboardButton("⏸️ Pause", callback_data="pause"),
                InlineKeyboardButton("▶️ Resume", callback_data="resume"),
            ],
            [
                InlineKeyboardButton("❌ Close All", callback_data="closeall"),
                InlineKeyboardButton("🛑 Stop Bot", callback_data="stop"),
            ],
        ])
        await update.message.reply_text(msg, parse_mode='HTML', reply_markup=keyboard)

    async def _cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/pause — hentikan trading sementara."""
        if not self._is_authorized(update):
            return
        self._paused = True
        if self.on_pause:
            await self.on_pause()
        await update.message.reply_text(
            "⏸️ <b>Trading PAUSED</b>\nSend /resume to continue.",
            parse_mode='HTML'
        )
        logger.warning("Trading paused via Telegram")

    async def _cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/resume — lanjutkan trading."""
        if not self._is_authorized(update):
            return
        self._paused = False
        if self.on_resume:
            await self.on_resume()
        await update.message.reply_text(
            "▶️ <b>Trading RESUMED</b>",
            parse_mode='HTML'
        )
        logger.info("Trading resumed via Telegram")

    async def _cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/stop — matikan seluruh sistem (equivalent bash stop.sh)."""
        if not self._is_authorized(update):
            return
        await update.message.reply_text(
            "🛑 <b>STOPPING Atreides-1...</b>\n"
            "Semua posisi dibiarkan terbuka dengan SL/TP aktif.\n"
            "Gunakan /closeall sebelum /stop jika ingin close semua.",
            parse_mode='HTML'
        )
        logger.critical("System STOP commanded via Telegram")
        await asyncio.sleep(1)
        sys.exit(0)  # run.sh akan auto-restart — kirim /stop lagi jika tidak mau restart

    async def _cmd_close(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/close BTC/USDT — close posisi specific."""
        if not self._is_authorized(update):
            return

        args = ctx.args
        if not args:
            await update.message.reply_text(
                "Usage: /close BTC/USDT\nContoh: /close SOL/USDT",
                parse_mode='HTML'
            )
            return

        symbol = args[0].upper()
        await update.message.reply_text(f"⏳ Closing {symbol}...")

        if self.on_close_position:
            try:
                result = await self.on_close_position(symbol)
                pnl = result.get('pnl', 0)
                icon = "✅" if pnl >= 0 else "🔴"
                await update.message.reply_text(
                    f"{icon} <b>{symbol} CLOSED</b>\n"
                    f"PnL: <code>${pnl:+.4f}</code>",
                    parse_mode='HTML'
                )
            except Exception as e:
                await update.message.reply_text(f"❌ Failed to close {symbol}: {e}")
        else:
            await update.message.reply_text("❌ Close function not available")

    async def _cmd_closeall(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/closeall — close semua posisi aktif."""
        if not self._is_authorized(update):
            return

        await update.message.reply_text("⏳ Closing all positions...")

        if self.on_close_all:
            try:
                results = await self.on_close_all()
                if results:
                    msg = "✅ <b>All positions closed:</b>\n"
                    for r in results:
                        pnl = r.get('pnl', 0)
                        icon = "🟢" if pnl >= 0 else "🔴"
                        msg += f"{icon} {r.get('symbol')} PnL: <code>${pnl:+.4f}</code>\n"
                else:
                    msg = "ℹ️ No open positions to close."
                await update.message.reply_text(msg, parse_mode='HTML')
            except Exception as e:
                await update.message.reply_text(f"❌ Close all failed: {e}")
        else:
            await update.message.reply_text("❌ Close all function not available")

    async def _cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """/help — daftar semua commands."""
        if not self._is_authorized(update):
            return
        msg = (
            "⚔️ <b>ATREIDES-1 COMMANDS</b>\n"
            f"{'─'*28}\n"
            "/status        — status sistem & PnL\n"
            "/pause         — pause trading\n"
            "/resume        — resume trading\n"
            "/close SYMBOL  — close 1 posisi\n"
            "  contoh: /close BTC/USDT\n"
            "/closeall      — close semua posisi\n"
            "/stop          — matikan bot\n"
            "/help          — pesan ini\n"
            f"{'─'*28}\n"
            "Atau gunakan tombol inline di setiap notifikasi."
        )
        await update.message.reply_text(msg, parse_mode='HTML')

    # ------------------------------------------------------------------ #
    # Inline button handler                                                #
    # ------------------------------------------------------------------ #

    async def _handle_callback(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE):
        """Handle semua inline button presses."""
        query = update.callback_query
        if str(query.message.chat_id) != self.chat_id:
            await query.answer("Unauthorized")
            return

        await query.answer()  # Hapus loading spinner
        data = query.data

        if data == "pause":
            self._paused = True
            if self.on_pause:
                await self.on_pause()
            await query.edit_message_reply_markup(reply_markup=None)
            await self.send("⏸️ <b>Trading PAUSED via button</b>")
            logger.warning("Trading paused via inline button")

        elif data == "resume":
            self._paused = False
            if self.on_resume:
                await self.on_resume()
            await query.edit_message_reply_markup(reply_markup=None)
            await self.send("▶️ <b>Trading RESUMED via button</b>")
            logger.info("Trading resumed via inline button")

        elif data == "stop":
            await self.send("🛑 <b>Stopping Atreides-1...</b>")
            logger.critical("System STOP via inline button")
            await asyncio.sleep(1)
            sys.exit(0)

        elif data == "closeall":
            await self.send("⏳ Closing all positions...")
            if self.on_close_all:
                try:
                    results = await self.on_close_all()
                    msg = "✅ <b>All positions closed</b>\n" if results else "ℹ️ No positions to close."
                    for r in (results or []):
                        pnl = r.get('pnl', 0)
                        icon = "🟢" if pnl >= 0 else "🔴"
                        msg += f"{icon} {r.get('symbol')} PnL: <code>${pnl:+.4f}</code>\n"
                    await self.send(msg)
                except Exception as e:
                    await self.send(f"❌ Close all failed: {e}")

        elif data == "status":
            # Re-trigger status command
            status_data = self.get_status() if self.get_status else {}
            cb = status_data.get("circuit_breaker", {})
            msg = (
                f"📊 Net PnL: <code>${cb.get('net_pnl_today', 0):+.4f}</code>\n"
                f"Trades: {cb.get('trades_today', 0)} | "
                f"Win: {cb.get('win_rate', 0):.0f}%\n"
                f"Drawdown: {cb.get('drawdown_pct', 0):.2f}%"
            )
            await self.send(msg)

        elif data.startswith("close_"):
            symbol = data.replace("close_", "")
            await self.send(f"⏳ Closing {symbol}...")
            if self.on_close_position:
                try:
                    result = await self.on_close_position(symbol)
                    pnl = result.get('pnl', 0)
                    icon = "✅" if pnl >= 0 else "🔴"
                    await self.send(
                        f"{icon} <b>{symbol} CLOSED</b>\n"
                        f"PnL: <code>${pnl:+.4f}</code>"
                    )
                    await query.edit_message_reply_markup(reply_markup=None)
                except Exception as e:
                    await self.send(f"❌ Close {symbol} failed: {e}")

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    async def start_polling(self):
        """Jalankan bot polling sebagai background task."""
        if not TELEGRAM_AVAILABLE or not self.token:
            logger.warning("Telegram bot not started — missing token or library")
            return

        self.app = Application.builder().token(self.token).build()

        # Command handlers
        self.app.add_handler(CommandHandler("status", self._cmd_status))
        self.app.add_handler(CommandHandler("pause", self._cmd_pause))
        self.app.add_handler(CommandHandler("resume", self._cmd_resume))
        self.app.add_handler(CommandHandler("stop", self._cmd_stop))
        self.app.add_handler(CommandHandler("close", self._cmd_close))
        self.app.add_handler(CommandHandler("closeall", self._cmd_closeall))
        self.app.add_handler(CommandHandler("help", self._cmd_help))

        # Inline button handler
        self.app.add_handler(CallbackQueryHandler(self._handle_callback))

        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling(drop_pending_updates=True)
        logger.info("🤖 Telegram C2 started — /status /pause /resume /stop /close /closeall")

    async def stop(self):
        """Stop bot polling."""
        if self.app:
            await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()

    @property
    def is_paused(self) -> bool:
        return self._paused
