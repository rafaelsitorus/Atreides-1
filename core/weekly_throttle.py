"""
core/weekly_throttle.py
Fase 3B — Weekly Feedback Loop.

Jika sistem profit > 1% per hari selama 7 hari berturut-turut,
masuk Throttled State untuk mengunci profit:
  - Min confidence dinaikkan: 75% → 85%
  - Risk percent dikurangi: 5% → 2%
  - Frekuensi trading dikurangi: setiap cycle → 1 dari 3 cycle
"""
import json
from datetime import date, timedelta
from pathlib import Path
from loguru import logger


class WeeklyThrottle:
    """
    Weekly performance feedback loop.
    Monitor daily PnL % dan aktifkan throttle jika target tercapai.
    """

    def __init__(
        self,
        target_daily_pct: float = 1.0,       # Target profit harian %
        consecutive_days: int = 7,            # Hari berturut yang diperlukan
        throttle_min_confidence: float = 0.85,
        throttle_risk_pct: float = 2.0,
        throttle_cycle_skip: int = 2,         # Skip 2 dari 3 cycle
        state_file: str = "logs/weekly_throttle_state.json"
    ):
        self.target_daily_pct    = target_daily_pct
        self.consecutive_days    = consecutive_days
        self.throttle_min_conf   = throttle_min_confidence
        self.throttle_risk_pct   = throttle_risk_pct
        self.throttle_cycle_skip = throttle_cycle_skip
        self.state_file          = Path(state_file)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state = self._load_state()
        self._cycle_counter = 0  # Counter untuk throttle skip

    # ------------------------------------------------------------------ #
    # State persistence                                                    #
    # ------------------------------------------------------------------ #

    def _load_state(self) -> dict:
        if self.state_file.exists():
            try:
                with open(self.state_file) as f:
                    return json.load(f)
            except Exception:
                pass
        return self._default_state()

    def _default_state(self) -> dict:
        return {
            "daily_results": {},    # {"2026-03-19": {"pnl_pct": 1.2, "passed": True}}
            "throttled": False,
            "throttled_since": None,
            "consecutive_wins": 0,
            "last_checked": None,
        }

    def _save_state(self):
        with open(self.state_file, "w") as f:
            json.dump(self._state, f, indent=2)

    # ------------------------------------------------------------------ #
    # Core logic                                                           #
    # ------------------------------------------------------------------ #

    def record_daily_result(self, equity_start: float, equity_end: float):
        """
        Catat hasil hari ini. Dipanggil saat hari berganti.

        Args:
            equity_start: Equity awal hari
            equity_end: Equity akhir hari
        """
        if equity_start <= 0:
            return

        today = str(date.today() - timedelta(days=1))  # Hari yang baru selesai
        pnl_pct = ((equity_end - equity_start) / equity_start) * 100
        passed  = pnl_pct >= self.target_daily_pct

        self._state["daily_results"][today] = {
            "pnl_pct": round(pnl_pct, 4),
            "passed": passed,
            "equity_start": equity_start,
            "equity_end": equity_end,
        }

        # Hitung consecutive wins
        consecutive = 0
        check_date  = date.today() - timedelta(days=1)
        for _ in range(self.consecutive_days):
            day_str = str(check_date)
            day_result = self._state["daily_results"].get(day_str, {})
            if day_result.get("passed", False):
                consecutive += 1
                check_date -= timedelta(days=1)
            else:
                break

        self._state["consecutive_wins"] = consecutive
        self._state["last_checked"]     = str(date.today())

        logger.info(
            f"📅 Daily result recorded: {today} | "
            f"PnL: {pnl_pct:+.2f}% | "
            f"Passed: {'✅' if passed else '❌'} | "
            f"Consecutive wins: {consecutive}/{self.consecutive_days}"
        )

        # Aktifkan throttle jika target tercapai
        if consecutive >= self.consecutive_days and not self._state["throttled"]:
            self._activate_throttle(consecutive)
        elif consecutive < self.consecutive_days and self._state["throttled"]:
            self._deactivate_throttle()

        # Buang data lama (simpan 30 hari terakhir)
        all_dates = sorted(self._state["daily_results"].keys())
        if len(all_dates) > 30:
            for old_date in all_dates[:-30]:
                del self._state["daily_results"][old_date]

        self._save_state()

    def _activate_throttle(self, consecutive: int):
        """Masuk Throttled State."""
        self._state["throttled"]       = True
        self._state["throttled_since"] = str(date.today())
        self._save_state()
        logger.info(
            f"🎯 THROTTLE ACTIVATED — "
            f"{consecutive} consecutive days ≥ {self.target_daily_pct}% profit | "
            f"Min confidence: 75% → {self.throttle_min_conf:.0%} | "
            f"Risk: 5% → {self.throttle_risk_pct}% | "
            f"Protecting profits!"
        )

    def _deactivate_throttle(self):
        """Keluar dari Throttled State."""
        self._state["throttled"]       = False
        self._state["throttled_since"] = None
        self._save_state()
        logger.info("📈 THROTTLE DEACTIVATED — back to normal trading parameters")

    # ------------------------------------------------------------------ #
    # Query methods — dipanggil oleh orchestrator                         #
    # ------------------------------------------------------------------ #

    def is_throttled(self) -> bool:
        """Return True jika sistem dalam Throttled State."""
        return self._state["throttled"]

    def should_skip_cycle(self) -> bool:
        """
        Dalam throttled state, skip 2 dari 3 cycle.
        Return True jika cycle ini harus di-skip.
        """
        if not self._state["throttled"]:
            return False
        self._cycle_counter += 1
        # Hanya eksekusi setiap throttle_cycle_skip + 1 cycle
        if self._cycle_counter % (self.throttle_cycle_skip + 1) != 0:
            logger.debug(
                f"[THROTTLE] Skipping cycle "
                f"{self._cycle_counter % (self.throttle_cycle_skip + 1)}"
                f"/{self.throttle_cycle_skip}"
            )
            return True
        return False

    def get_active_params(self, base_min_confidence: float, base_risk_pct: float) -> dict:
        """
        Return parameter yang aktif berdasarkan throttle state.

        Returns dict dengan min_confidence dan risk_pct yang harus dipakai.
        """
        if self._state["throttled"]:
            return {
                "min_confidence": self.throttle_min_conf,
                "risk_pct": self.throttle_risk_pct,
                "state": "THROTTLED",
            }
        return {
            "min_confidence": base_min_confidence,
            "risk_pct": base_risk_pct,
            "state": "NORMAL",
        }

    def get_status(self) -> dict:
        """Return status untuk Telegram /status dan dashboard."""
        recent_days = {}
        for i in range(7):
            day = str(date.today() - timedelta(days=i+1))
            result = self._state["daily_results"].get(day, {})
            recent_days[day] = {
                "pnl_pct": result.get("pnl_pct", None),
                "passed": result.get("passed", False),
            }

        return {
            "throttled": self._state["throttled"],
            "throttled_since": self._state["throttled_since"],
            "consecutive_wins": self._state["consecutive_wins"],
            "target_days": self.consecutive_days,
            "target_daily_pct": self.target_daily_pct,
            "throttle_min_confidence": self.throttle_min_conf,
            "throttle_risk_pct": self.throttle_risk_pct,
            "recent_7_days": recent_days,
        }
