# autolearn.py -- ScalpBot AutoLearn v1
# Analizza i trade passati e ottimizza TP/SL automaticamente.
# Viene chiamato ogni notte a mezzanotte dal bot principale.

import sqlite3
import os
from loguru import logger
from config import save_state

DB_PATH = os.environ.get("DB_PATH", "/data/scalpbot.db")

# Limiti di sicurezza -- il sistema non puo' andare oltre questi valori
MIN_TP = 0.001   # 0.1% minimo
MAX_TP = 0.02    # 2.0% massimo
MIN_SL = 0.0005  # 0.05% minimo
MAX_SL = 0.01    # 1.0% massimo

# Quanti trade minimi servono per fare autolearn
MIN_TRADES_REQUIRED = 20


def get_recent_trades(limit=50):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT * FROM trades ORDER BY timestamp DESC LIMIT ?', (limit,)
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"AutoLearn get_trades error: {e}")
        return []


def analyze(trades):
    if not trades:
        return None

    total   = len(trades)
    wins    = [t for t in trades if t['pnl'] > 0]
    losses  = [t for t in trades if t['pnl'] <= 0]
    win_rate = len(wins) / total

    # Motivo di uscita
    tp_hits = [t for t in wins  if t['reason'] == 'TP']
    sl_hits = [t for t in losses if t['reason'] == 'SL']

    # PnL medio win/loss
    avg_win  = sum(t['pnl'] for t in wins)  / len(wins)  if wins  else 0
    avg_loss = sum(t['pnl'] for t in losses) / len(losses) if losses else 0

    # Durata media trade in secondi
    avg_duration = sum(t.get('duration_sec', 0) for t in trades) / total

    return {
        'total':       total,
        'win_rate':    round(win_rate * 100, 1),
        'wins':        len(wins),
        'losses':      len(losses),
        'tp_hits':     len(tp_hits),
        'sl_hits':     len(sl_hits),
        'avg_win':     round(avg_win, 4),
        'avg_loss':    round(avg_loss, 4),
        'avg_duration':round(avg_duration, 1),
        'profit_factor': round(abs(avg_win / avg_loss), 2) if avg_loss != 0 else 99,
    }


def compute_adjustments(stats, current_tp, current_sl):
    new_tp = current_tp
    new_sl = current_sl
    reasons = []

    wr = stats['win_rate']

    # -- WIN RATE TROPPO BASSO (< 45%) --
    # Stai perdendo piu' della meta' dei trade
    # Azione: avvicina il TP (prendi profitto prima) e allarga SL (meno stop prematuri)
    if wr < 45:
        new_tp = round(current_tp * 0.90, 5)   # -10% TP
        new_sl = round(current_sl * 1.10, 5)   # +10% SL
        reasons.append(f"WinRate basso ({wr}%) -- TP abbassato, SL allargato")

    # -- WIN RATE OTTIMO (> 65%) --
    # Stai vincendo spesso -- prova a prendere piu' profitto per trade
    elif wr > 65:
        new_tp = round(current_tp * 1.10, 5)   # +10% TP
        reasons.append(f"WinRate alto ({wr}%) -- TP aumentato")

    # -- TROPPI SL HIT (> 60% dei loss sono SL) --
    # Lo SL e' troppo stretto -- il prezzo tocca SL poi torna su
    if stats['total'] > 0:
        sl_rate = stats['sl_hits'] / stats['total'] * 100
        if sl_rate > 60 and wr < 55:
            new_sl = round(current_sl * 1.15, 5)   # +15% SL
            reasons.append(f"Troppi SL hit ({sl_rate:.0f}%) -- SL allargato")

    # -- PROFIT FACTOR BASSO (< 1.2) --
    # I win non coprono i loss -- TP troppo piccolo rispetto a SL
    if stats['profit_factor'] < 1.2 and stats['losses'] > 5:
        new_tp = round(current_tp * 1.10, 5)
        reasons.append(f"Profit factor basso ({stats['profit_factor']}) -- TP aumentato")

    # -- PROFIT FACTOR MOLTO ALTO (> 3) --
    # I win sono enormi rispetto ai loss -- possiamo stringere SL
    if stats['profit_factor'] > 3 and stats['wins'] > 10:
        new_sl = round(current_sl * 0.90, 5)   # -10% SL
        reasons.append(f"Profit factor alto ({stats['profit_factor']}) -- SL stretto")

    # Applica limiti di sicurezza
    new_tp = max(MIN_TP, min(MAX_TP, new_tp))
    new_sl = max(MIN_SL, min(MAX_SL, new_sl))

    # Mantieni sempre TP > SL * 1.5 (rapporto rischio/rendimento minimo)
    if new_tp < new_sl * 1.5:
        new_tp = round(new_sl * 1.5, 5)
        reasons.append("TP corretto per mantenere R/R >= 1.5")

    changed = (abs(new_tp - current_tp) > 0.00001 or abs(new_sl - current_sl) > 0.00001)

    return {
        'new_tp':   new_tp,
        'new_sl':   new_sl,
        'changed':  changed,
        'reasons':  reasons,
    }


def save_autolearn_log(stats, adjustments, old_tp, old_sl):
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute(
            'INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)',
            ('autolearn_last_run', __import__('datetime').datetime.now().isoformat())
        )
        summary = (
            f"WR={stats['win_rate']}% | "
            f"PF={stats['profit_factor']} | "
            f"TP {old_tp:.4%}->{adjustments['new_tp']:.4%} | "
            f"SL {old_sl:.4%}->{adjustments['new_sl']:.4%} | "
            f"{'; '.join(adjustments['reasons']) if adjustments['reasons'] else 'No changes'}"
        )
        conn.execute(
            'INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)',
            ('autolearn_last_summary', summary)
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"AutoLearn save log error: {e}")


class AutoLearn:
    def __init__(self, config, api_server=None):
        self.config     = config
        self.api_server = api_server

    def _notify(self, msg):
        logger.info(f"AutoLearn: {msg}")
        if self.api_server:
            self.api_server.add_log("info", f"AI: {msg}")

    def run(self):
        self._notify("Avvio analisi trade...")

        trades = get_recent_trades(limit=50)

        if len(trades) < MIN_TRADES_REQUIRED:
            self._notify(
                f"Trade insufficienti ({len(trades)}/{MIN_TRADES_REQUIRED}) -- skip"
            )
            return False

        stats = analyze(trades)
        if not stats:
            return False

        self._notify(
            f"Analisi: {stats['total']} trade | "
            f"WR={stats['win_rate']}% | "
            f"PF={stats['profit_factor']} | "
            f"AvgWin={stats['avg_win']:.4f} AvgLoss={stats['avg_loss']:.4f}"
        )

        old_tp = self.config.spot_take_profit_pct
        old_sl = self.config.spot_stop_loss_pct

        adj = compute_adjustments(stats, old_tp, old_sl)

        if not adj['changed']:
            self._notify("Parametri gia' ottimali -- nessuna modifica")
            save_autolearn_log(stats, adj, old_tp, old_sl)
            return False

        # Applica nuovi parametri
        self.config.spot_take_profit_pct = adj['new_tp']
        self.config.spot_stop_loss_pct   = adj['new_sl']
        self.config.take_profit_pct      = adj['new_tp']
        self.config.stop_loss_pct        = adj['new_sl']

        # Salva nel DB
        save_state(self.config)
        save_autolearn_log(stats, adj, old_tp, old_sl)

        # Notifica ogni reason
        for r in adj['reasons']:
            self._notify(r)

        self._notify(
            f"Nuovi parametri: TP={adj['new_tp']:.3%} SL={adj['new_sl']:.3%} "
            f"(erano TP={old_tp:.3%} SL={old_sl:.3%})"
        )

        return True
