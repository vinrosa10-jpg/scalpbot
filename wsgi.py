"""
WSGI entry point per Render.
Avvia il server web e il bot in background.
"""
import threading
import asyncio
import os
from flask import Flask, jsonify, request
from loguru import logger

app = Flask(__name__)

# Stato globale del bot
bot_state = {
    "status": "starting",
    "capital": 100.0,
    "daily_pnl": 0.0,
    "wins": 0,
    "losses": 0,
    "trades": [],
    "active_pairs": [],
    "target_pct": 20,
    "log": []
}

bot_instance = None
config_instance = None


def run_bot():
    """Avvia il bot in un thread separato."""
    global bot_instance, config_instance
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        from config import Config
        from bot import ScalpingBot
        config_instance = Config.load()
        bot_instance = ScalpingBot(config_instance)
        bot_state["status"] = "running"
        bot_state["target_pct"] = config_instance.daily_profit_target_pct * 100
        loop.run_until_complete(bot_instance.start())
    except Exception as e:
        logger.error(f"Bot error: {e}")
        bot_state["status"] = "error"


@app.route('/')
def home():
    return jsonify({"ok": True, "service": "ScalpBot", "status": bot_state["status"]})


@app.route('/status')
def status():
    # Aggiorna stato dal bot se disponibile
    if bot_instance:
        rm = bot_instance.risk_manager
        om = bot_instance.order_manager
        bot_state["capital"] = round(rm.daily_start_capital, 2)
        bot_state["daily_pnl"] = round(rm.daily_pnl, 4)
        bot_state["wins"] = getattr(rm, 'winning_trades', 0)
        bot_state["losses"] = getattr(rm, 'losing_trades', 0)
        bot_state["active_pairs"] = list(bot_instance.strategies.keys())
        if rm._target_hit:
            bot_state["status"] = "target_hit"
        elif bot_instance.running:
            bot_state["status"] = "running"
    response = jsonify(bot_state)
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/command', methods=['POST', 'OPTIONS'])
def command():
    if request.method == 'OPTIONS':
        response = jsonify({})
        response.headers['Access-Control-Allow-Origin'] = '*'
        response.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return response

    data = request.json or {}
    cmd = data.get('command', '')

    if bot_instance:
        if cmd == 'pause':
            bot_instance.risk_manager._target_hit = True
            bot_state["status"] = "paused"
        elif cmd == 'set_risk':
            level = data.get('value', 'high')
            target = data.get('target', 20) / 100
            if config_instance:
                config_instance.daily_profit_target_pct = target
            bot_instance.risk_manager._target_hit = False
            bot_instance.risk_manager.daily_pnl = 0
            bot_state["target_pct"] = target * 100

    response = jsonify({"ok": True, "cmd": cmd})
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


@app.route('/health')
def health():
    return jsonify({"ok": True})


# Avvia bot in background quando il server parte
bot_thread = threading.Thread(target=run_bot, daemon=True)
bot_thread.start()
logger.info("🤖 Bot avviato in background")


if __name__ == '__main__':
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)
