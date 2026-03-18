import os
from dotenv import load_dotenv
import ccxt

load_dotenv()

# Inisialisasi dengan konfigurasi minimalis
exchange = ccxt.binance({
    'apiKey': os.getenv('BINANCE_API_KEY'),
    'secret': os.getenv('BINANCE_SECRET'),
})

# OVERRIDE TOTAL: Kita ganti semua base URL agar tidak ada celah ke api.binance.com
# fapiPrivate adalah pintu masuk untuk data akun Futures
exchange.urls['api']['fapiPublic'] = 'https://testnet.binancefuture.com/fapi/v1'
exchange.urls['api']['fapiPublicV2'] = 'https://testnet.binancefuture.com/fapi/v2'
exchange.urls['api']['fapiPrivate'] = 'https://testnet.binancefuture.com/fapi/v1'
exchange.urls['api']['fapiPrivateV2'] = 'https://testnet.binancefuture.com/fapi/v2'

try:
    print(f"--- ATREIDES-1 LOW-LEVEL DEBUG ---")
    
    # Memanggil method internal CCXT yang dipetakan langsung ke /fapi/v2/account
    # Ini melewati semua logika pengecekan saldo Spot/SAPI
    response = exchange.fapiPrivateV2GetAccount()
    
    # Mengambil total saldo dari response JSON Binance
    total_balance = response.get('totalWalletBalance', '0')
    
    print(f"Status: Berhasil Tembus ke Testnet!")
    print(f"Total Wallet Balance: {total_balance} USDT")

except Exception as e:
    print(f"Masih gagal, Raja. Detail:")
    print(f"Tipe: {type(e).__name__}")
    print(f"Pesan: {e}")