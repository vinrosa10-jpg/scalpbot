# 🤖 Binance Scalping Bot

**Strategy:** Order Book Imbalance + EMA Momentum  
**Markets:** Spot + Futures (USDT-M)  
**Pairs:** BTC, ETH, BNB, SOL, XRP (configurabile)

---

## 📋 Strategia

Il bot combina due segnali per aprire posizioni:

1. **EMA Momentum** – EMA 9 vs EMA 21 sulla candela da 1 minuto. Se EMA veloce > lenta = trend up.
2. **Order Book Imbalance** – Se >65% del volume nel book è lato buy = pressione acquisto.
3. **Trade Flow Confirmation** – Conferma che i trade recenti siano prevalentemente buy/sell.

**Entry LONG:** EMA up + OB imbalance buy + trade flow buy  
**Entry SHORT:** EMA down + OB imbalance sell + trade flow sell (solo Futures)  
**Exit:** Take Profit 0.3% | Stop Loss 0.15%

---

## 🖥️ Requisiti Server

- VPS Linux (Ubuntu 22.04+)
- Python 3.11+
- **Posizione geografica consigliata:** Tokyo o Singapore (vicinanza server Binance)
- RAM: 512MB minimo, 1GB consigliato

**Provider consigliati:**
- DigitalOcean (Tokyo) ~$6/mese
- Vultr (Tokyo) ~$6/mese
- AWS EC2 ap-northeast-1

---

## ⚙️ Installazione

### 1. Clona / carica i file sul server

```bash
mkdir ~/scalping_bot && cd ~/scalping_bot
# Carica tutti i file .py in questa cartella
```

### 2. Installa Python e dipendenze

```bash
sudo apt update && sudo apt install python3 python3-pip -y
pip3 install -r requirements.txt
```

### 3. Configura le credenziali

```bash
cp .env.example .env
nano .env
```

Inserisci le tue API key. **Lascia `TESTNET=true` per iniziare!**

#### Come ottenere API key testnet:
- **Spot testnet:** https://testnet.binance.vision → "Generate HMAC_SHA256 Key"
- **Futures testnet:** https://testnet.binancefuture.com → API Management

### 4. Crea la cartella log

```bash
mkdir logs
```

### 5. Avvia il bot

```bash
python3 main.py
```

---

## 🔄 Avvio automatico con systemd (consigliato)

```bash
sudo nano /etc/systemd/system/scalping-bot.service
```

```ini
[Unit]
Description=Binance Scalping Bot
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/home/ubuntu/scalping_bot
ExecStart=/usr/bin/python3 main.py
Restart=on-failure
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable scalping-bot
sudo systemctl start scalping-bot
sudo systemctl status scalping-bot
```

---

## 📊 Monitoraggio

```bash
# Log in tempo reale
tail -f logs/bot_$(date +%Y-%m-%d).log

# Stato del servizio
sudo systemctl status scalping-bot
```

---

## 🔧 Configurazione Avanzata

Modifica `.env` per personalizzare:

| Parametro | Default | Descrizione |
|-----------|---------|-------------|
| `POSITION_SIZE_USDT` | 20 | USDT per trade |
| `MAX_OPEN_TRADES` | 4 | Trade simultanei max |
| `TAKE_PROFIT_PCT` | 0.003 | Take profit 0.3% |
| `STOP_LOSS_PCT` | 0.0015 | Stop loss 0.15% |
| `MAX_DAILY_LOSS_USDT` | 50 | Limite perdita giornaliera |
| `FUTURES_LEVERAGE` | 5 | Leva futures |
| `OB_IMBALANCE_THRESHOLD` | 0.65 | Soglia imbalance (in config.py) |

---

## ⚠️ Avvertenze Importanti

1. **Inizia SEMPRE in testnet** — verifica il comportamento prima di usare soldi veri
2. **Non investire più di quanto puoi permetterti di perdere**
3. **Le commissioni su Spot sono 0.1% per trade** — il bot deve sovraperformarle
4. **In Italia** i guadagni da crypto trading sono soggetti a tassazione
5. **Il bot non garantisce profitti** — il mercato può andare in qualsiasi direzione

---

## 🗂️ Struttura File

```
scalping_bot/
├── main.py          # Entry point
├── config.py        # Configurazione
├── bot.py           # Orchestratore principale
├── strategy.py      # Logica EMA + Order Book
├── risk_manager.py  # Gestione rischio
├── order_manager.py # Ciclo vita ordini (TP/SL)
├── exchange.py      # Client Binance REST + WebSocket
├── data_feed.py     # Import redirect
├── requirements.txt
├── .env.example
└── logs/
```
