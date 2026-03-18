"""
utils/dashboard.py
Rich terminal dashboard untuk monitoring Atreides-1 secara real-time.
Jalankan terpisah: python utils/dashboard.py
"""
import asyncio
import os
from datetime import datetime
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.panel import Panel
from rich.columns import Columns
from rich.text import Text
from rich.live import Live
from rich.layout import Layout
from rich import box
import ccxt.async_support as ccxt

load_dotenv()

SYMBOLS = os.getenv('TRADING_SYMBOLS', 'BTC/USDT,ETH/USDT,SOL/USDT').split(',')
REFRESH_SECONDS = 5


def make_exchange():
    exchange = ccxt.binanceusdm({
        'apiKey': os.getenv('BINANCE_API_KEY'),
        'secret': os.getenv('BINANCE_SECRET'),
        'enableRateLimit': True,
        'options': {
            'defaultType': 'future',
            'fetchCurrencies': False,
        },
        'urls': {
            'api': {
                'fapiPublic':    'https://testnet.binancefuture.com/fapi/v1',
                'fapiPrivate':   'https://testnet.binancefuture.com/fapi/v1',
                'fapiPublicV2':  'https://testnet.binancefuture.com/fapi/v2',
                'fapiPrivateV2': 'https://testnet.binancefuture.com/fapi/v2',
            }
        }
    })
    return exchange


async def fetch_dashboard_data(exchange):
    """Ambil semua data yang dibutuhkan dashboard."""
    try:
        balance = await exchange.fetch_balance()
        usdt_free  = float(balance.get('USDT', {}).get('free', 0))
        usdt_total = float(balance.get('USDT', {}).get('total', 0))

        symbols = [s.strip() for s in SYMBOLS]
        positions = await exchange.fetch_positions(symbols)
        active_positions = [p for p in positions if abs(float(p.get('contracts') or 0)) > 0]

        # Harga terkini
        prices = {}
        for symbol in symbols:
            try:
                ticker = await exchange.fetch_ticker(symbol)
                prices[symbol] = float(ticker['last'])
            except Exception:
                prices[symbol] = 0.0

        return {
            'usdt_free': usdt_free,
            'usdt_total': usdt_total,
            'positions': active_positions,
            'prices': prices,
            'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        }
    except Exception as e:
        return {'error': str(e)}


def render_dashboard(data: dict) -> Layout:
    """Render layout dashboard dari data."""
    layout = Layout()
    layout.split_column(
        Layout(name="header", size=3),
        Layout(name="body"),
        Layout(name="footer", size=3),
    )
    layout["body"].split_row(
        Layout(name="balance", ratio=1),
        Layout(name="positions", ratio=2),
        Layout(name="prices", ratio=1),
    )

    # Header
    layout["header"].update(Panel(
        Text("⚔️  ATREIDES-1 TRADING DASHBOARD", justify="center", style="bold white"),
        style="bold blue"
    ))

    if 'error' in data:
        layout["body"].update(Panel(f"[red]Error: {data['error']}[/red]"))
        layout["footer"].update(Panel(f"Last update: {datetime.now().strftime('%H:%M:%S')}"))
        return layout

    # Balance panel
    balance_text = Text()
    balance_text.append("💰 BALANCE\n\n", style="bold yellow")
    balance_text.append(f"Free:  ", style="dim")
    balance_text.append(f"${data['usdt_free']:,.2f}\n", style="bold green")
    balance_text.append(f"Total: ", style="dim")
    balance_text.append(f"${data['usdt_total']:,.2f}\n", style="bold white")
    layout["balance"].update(Panel(balance_text, title="Wallet", border_style="yellow"))

    # Positions table
    pos_table = Table(box=box.SIMPLE, show_header=True, header_style="bold cyan")
    pos_table.add_column("Symbol", style="white")
    pos_table.add_column("Side", justify="center")
    pos_table.add_column("Size", justify="right")
    pos_table.add_column("Entry", justify="right")
    pos_table.add_column("PnL", justify="right")
    pos_table.add_column("PnL%", justify="right")

    if data['positions']:
        for pos in data['positions']:
            side = pos.get('side', '').upper()
            side_color = "green" if side == "LONG" else "red"
            pnl = float(pos.get('unrealizedPnl') or 0)
            pnl_pct = float(pos.get('percentage') or 0)
            pnl_color = "green" if pnl >= 0 else "red"
            entry = float(pos.get('entryPrice') or 0)
            contracts = float(pos.get('contracts') or 0)

            pos_table.add_row(
                pos.get('symbol', ''),
                f"[{side_color}]{side}[/{side_color}]",
                f"{contracts:.4f}",
                f"${entry:,.2f}",
                f"[{pnl_color}]${pnl:+.4f}[/{pnl_color}]",
                f"[{pnl_color}]{pnl_pct:+.2f}%[/{pnl_color}]",
            )
    else:
        pos_table.add_row("—", "—", "—", "—", "[dim]No active positions[/dim]", "—")

    layout["positions"].update(Panel(pos_table, title="Active Positions", border_style="cyan"))

    # Prices panel
    price_text = Text()
    price_text.append("📈 PRICES\n\n", style="bold magenta")
    for symbol, price in data['prices'].items():
        price_text.append(f"{symbol:<12}", style="dim")
        price_text.append(f"${price:>10,.2f}\n", style="bold white")
    layout["prices"].update(Panel(price_text, title="Market", border_style="magenta"))

    # Footer
    layout["footer"].update(Panel(
        Text(f"🕐 Last update: {data['timestamp']} | Refresh: {REFRESH_SECONDS}s | Press Ctrl+C to exit",
             justify="center", style="dim"),
        style="dim"
    ))

    return layout


async def main():
    exchange = make_exchange()
    console = Console()

    try:
        await exchange.load_markets()
        console.print("[green]✅ Connected to Binance Testnet[/green]")

        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                data = await fetch_dashboard_data(exchange)
                live.update(render_dashboard(data))
                await asyncio.sleep(REFRESH_SECONDS)

    except KeyboardInterrupt:
        console.print("\n[yellow]Dashboard stopped.[/yellow]")
    finally:
        await exchange.close()


if __name__ == '__main__':
    asyncio.run(main())