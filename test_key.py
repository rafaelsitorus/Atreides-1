# test_key.py
import ccxt

exchange = ccxt.binanceusdm({
    'apiKey': 'tsNpYmws68CZ9bZ6ttHy95V4OnHLjm7eRe2zoqIsTUFPmQMYQqb5opd54LX1pcT4',
    'secret': 'MEPVYvvGcKrlwqkQXBUInzAJrUlyrWH5bfZ4nbgSBlWiVKEvNSOm9MgRkn0dVdFe',
    'options': {'fetchCurrencies': False},
    'urls': {
        'api': {
            'fapiPublic':  'https://testnet.binancefuture.com/fapi/v1',
            'fapiPrivate': 'https://testnet.binancefuture.com/fapi/v1',
        }
    }
})

markets = exchange.load_markets()
print("OK — jumlah market:", len(markets))