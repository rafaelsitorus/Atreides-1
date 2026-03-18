"""
core/circuit_breaker.py
Hard daily circuit breaker — hentikan trading jika equity turun 2% dalam sehari.
"""
import json
import os
from datetime import datetime, date
from pathlib import Path
from loguru import logger


class CircuitBreaker:
    """
    Melindungi modal dari death-by-1000-cuts.
    Jika daily drawdown >= threshold, semua trading dihentikan 24 jam.
    """

    def __init__(
        self,
        max_daily_loss_pct: float = 2.0,
        state_file: str = "logs/circuit_breaker_state.json"
    ):
        self.max_daily_loss_pct = max_daily_loss_pct
        self.state_file = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()

    # ------------------------------------------------------------------ #
    # State persistence                                                    #
    # ------------------------------------------------------------------ #

    def _load_state(self) -> dict:
        """Load state dari file — survive restart."""
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "date": str(date.today()),
            "equity_start": None,   # Equity awal hari ini
            "equity_low": None,     # Equity terendah hari ini
            "tripped": False,       # Apakah circuit breaker aktif
            "tripped_at": None,     # Waktu trip
            "total_fees_today": 0.0,
            "trades_today": 0,
            "wins_today": 0,
            "losses_today": 0,
            "pnl_today": 0.0,
        }

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    def _reset_if_new_day(self):
        """Reset state jika hari baru."""
        today = str(date.today())
        if self._state["date"] != today:
            logger.info(f"📅 New day detected — resetting circuit breaker state")
            old_state = self._state.copy()
            self._state = self._default_state()
            self._state["date"] = today
            # Log summary hari kemarin
            logger.info(
                f"📊 Yesterday summary: "
                f"Trades={old_state['trades_today']} | "
                f"W/L={old_state['wins_today']}/{old_state['losses_today']} | "
                f"PnL=${old_state['pnl_today']:.4f} | "
                f"Fees=${old_state['total_fees_today']:.4f}"
            )
            self._save_state()

    # ------------------------------------------------------------------ #
    # Core logic                                                           #
    # ------------------------------------------------------------------ #

    def update_equity(self, current_equity: float):
        """
        Dipanggil setiap cycle dengan equity terkini.
        Set equity_start di awal hari, cek drawdown.
        """
        self._reset_if_new_day()

        # Set equity awal hari
        if self._state["equity_start"] is None:
            self._state["equity_start"] = current_equity
            self._state["equity_low"] = current_equity
            logger.info(f"📌 Day start equity: ${current_equity:.2f}")
            self._save_state()
            return

        # Update equity terendah
        if current_equity < self._state["equity_low"]:
            self._state["equity_low"] = current_equity
            self._save_state()

        # Cek drawdown
        if not self._state["tripped"]:
            drawdown_pct = (
                (self._state["equity_start"] - current_equity)
                / self._state["equity_start"]
                * 100
            )
            if drawdown_pct >= self.max_daily_loss_pct:
                self._trip(current_equity, drawdown_pct)

    def _trip(self, equity: float, drawdown_pct: float):
        """Aktifkan circuit breaker."""
        self._state["tripped"] = True
        self._state["tripped_at"] = datetime.now().isoformat()
        self._save_state()
        logger.critical(
            f"🚨 CIRCUIT BREAKER TRIPPED! "
            f"Drawdown: {drawdown_pct:.2f}% | "
            f"Equity: ${equity:.2f} | "
            f"Start: ${self._state['equity_start']:.2f} | "
            f"Trading halted for 24 hours"
        )

    def record_trade(self, pnl: float, fee: float):
        """Catat hasil trade untuk statistik harian."""
        self._reset_if_new_day()
        self._state["trades_today"] += 1
        self._state["pnl_today"] += pnl
        self._state["total_fees_today"] += fee
        if pnl > 0:
            self._state["wins_today"] += 1
        else:
            self._state["losses_today"] += 1
        self._save_state()

    # ------------------------------------------------------------------ #
    # Status checks                                                        #
    # ------------------------------------------------------------------ #

    def is_tripped(self) -> bool:
        """Return True jika trading harus dihentikan."""
        self._reset_if_new_day()
        return self._state["tripped"]

    def get_status(self) -> dict:
        """Return status lengkap untuk Telegram/dashboard."""
        self._reset_if_new_day()
        equity_start = self._state["equity_start"] or 0
        equity_low = self._state["equity_low"] or 0
        drawdown = 0.0
        if equity_start > 0:
            drawdown = (equity_start - equity_low) / equity_start * 100

        win_rate = 0.0
        if self._state["trades_today"] > 0:
            win_rate = self._state["wins_today"] / self._state["trades_today"] * 100

        return {
            "date": self._state["date"],
            "tripped": self._state["tripped"],
            "tripped_at": self._state["tripped_at"],
            "equity_start": equity_start,
            "equity_low": equity_low,
            "drawdown_pct": drawdown,
            "max_drawdown_pct": self.max_daily_loss_pct,
            "trades_today": self._state["trades_today"],
            "wins_today": self._state["wins_today"],
            "losses_today": self._state["losses_today"],
            "win_rate": win_rate,
            "pnl_today": self._state["pnl_today"],
            "fees_today": self._state["total_fees_today"],
            "net_pnl_today": self._state["pnl_today"] - self._state["total_fees_today"],
        }
