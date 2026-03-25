"""
Database Manager - SQLite per documentazione automatica trading.
"""

import sqlite3
import os
from datetime import datetime, date
from loguru import logger

DB_PATH = os.environ.get("DB_PATH", "/data/scalpbot.db")


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    c = conn.cursor()

    c.execute('''CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        date TEXT NOT NULL,
        pair TEXT NOT NULL,
        side TEXT NOT NULL,
        market TEXT NOT NULL,
        entry_price REAL NOT NULL,
        exit_price REAL NOT NULL,
        qty REAL NOT NULL,
        pnl REAL NOT NULL,
        reason TEXT NOT NULL,
        duration_sec REAL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS daily_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        capital REAL NOT NULL,
        daily_pnl REAL NOT NULL,
        wins INTEGER NOT NULL,
        losses INTEGER NOT NULL,
        win_rate REAL NOT NULL,
        max_drawdown REAL DEFAULT 0
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS equity_curve (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        equity REAL NOT NULL,
        pnl_cumulative REAL NOT NULL
    )''')

    c.execute('''CREATE TABLE IF NOT EXISTS bot_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    )''')

    conn.commit()
    conn.close()
    logger.info("✅ Database inizializzato")


def save_trade(pair, side, market, entry_price, exit_price, qty, pnl, reason, duration_sec=0):
    try:
        conn = get_conn()
        now = datetime.now()
        conn.execute('''INSERT INTO trades
            (timestamp, date, pair, side, market, entry_price, exit_price, qty, pnl, reason, duration_sec)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (now.isoformat(), now.date().isoformat(), pair, side, market,
             entry_price, exit_price, qty, pnl, reason, duration_sec))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB save_trade error: {e}")


def save_snapshot(capital, daily_pnl, wins, losses, max_drawdown=0):
    try:
        conn = get_conn()
        now = datetime.now()
        wr = wins / (wins + losses) if (wins + losses) > 0 else 0
        conn.execute('''INSERT INTO daily_snapshots
            (date, timestamp, capital, daily_pnl, wins, losses, win_rate, max_drawdown)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)''',
            (now.date().isoformat(), now.isoformat(), capital, daily_pnl, wins, losses, wr, max_drawdown))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB save_snapshot error: {e}")


def save_equity(equity, pnl_cumulative):
    try:
        conn = get_conn()
        conn.execute('''INSERT INTO equity_curve (timestamp, equity, pnl_cumulative)
            VALUES (?, ?, ?)''', (datetime.now().isoformat(), equity, pnl_cumulative))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB save_equity error: {e}")


def save_bot_state(key: str, value: str):
    """Salva parametro dinamico nel DB."""
    try:
        conn = get_conn()
        conn.execute('INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)',
            (key, value))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_bot_state error: {e}")


def get_bot_state(key: str, default: str = None) -> str:
    """Legge parametro dinamico dal DB."""
    try:
        conn = get_conn()
        row = conn.execute('SELECT value FROM bot_state WHERE key = ?',
            (key,)).fetchone()
        conn.close()
        return row[0] if row else default
    except Exception:
        return default


def save_all_state(state: dict):
    """Salva dizionario di stato nel DB."""
    try:
        conn = get_conn()
        for key, value in state.items():
            conn.execute('INSERT OR REPLACE INTO bot_state (key, value) VALUES (?, ?)',
                (key, str(value)))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"save_all_state error: {e}")


def load_all_state() -> dict:
    """Carica tutto lo stato dal DB."""
    try:
        conn = get_conn()
        rows = conn.execute('SELECT key, value FROM bot_state').fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}


def get_daily_report(target_date=None):
    if target_date is None:
        target_date = date.today().isoformat()
    try:
        conn = get_conn()
        trades = conn.execute(
            'SELECT * FROM trades WHERE date = ? ORDER BY timestamp DESC', (target_date,)
        ).fetchall()
        snap = conn.execute(
            'SELECT * FROM daily_snapshots WHERE date = ? ORDER BY timestamp DESC LIMIT 1', (target_date,)
        ).fetchone()
        conn.close()
        trades_list = [dict(t) for t in trades]
        wins = sum(1 for t in trades_list if t['pnl'] > 0)
        losses = sum(1 for t in trades_list if t['pnl'] <= 0)
        total_pnl = sum(t['pnl'] for t in trades_list)
        return {
            'date': target_date,
            'total_trades': len(trades_list),
            'wins': wins,
            'losses': losses,
            'win_rate': round(wins / len(trades_list) * 100, 1) if trades_list else 0,
            'total_pnl': round(total_pnl, 4),
            'best_trade': round(max((t['pnl'] for t in trades_list), default=0), 4),
            'worst_trade': round(min((t['pnl'] for t in trades_list), default=0), 4),
            'capital': dict(snap)['capital'] if snap else 0,
            'trades': trades_list,
        }
    except Exception as e:
        logger.error(f"DB get_daily_report error: {e}")
        return {}


def get_overall_stats():
    try:
        conn = get_conn()
        trades = conn.execute('SELECT * FROM trades ORDER BY timestamp ASC').fetchall()
        equity = conn.execute('SELECT * FROM equity_curve ORDER BY timestamp DESC LIMIT 100').fetchall()
        conn.close()
        trades_list = [dict(t) for t in trades]
        if not trades_list:
            return {'total_trades': 0, 'total_pnl': 0, 'win_rate': 0, 'equity': []}
        wins = sum(1 for t in trades_list if t['pnl'] > 0)
        losses = sum(1 for t in trades_list if t['pnl'] <= 0)
        total_pnl = sum(t['pnl'] for t in trades_list)
        cumulative = 0
        peak = 0
        max_dd = 0
        for t in trades_list:
            cumulative += t['pnl']
            if cumulative > peak:
                peak = cumulative
            dd = peak - cumulative
            if dd > max_dd:
                max_dd = dd
        daily = {}
        for t in trades_list:
            d = t['date']
            if d not in daily:
                daily[d] = {'pnl': 0, 'trades': 0, 'wins': 0}
            daily[d]['pnl'] += t['pnl']
            daily[d]['trades'] += 1
            if t['pnl'] > 0:
                daily[d]['wins'] += 1
        return {
            'total_trades': len(trades_list),
            'wins': wins,
            'losses': losses,
            'win_rate': round(wins / len(trades_list) * 100, 1) if trades_list else 0,
            'total_pnl': round(total_pnl, 4),
            'max_drawdown': round(max_dd, 4),
            'best_day': round(max((d['pnl'] for d in daily.values()), default=0), 4),
            'worst_day': round(min((d['pnl'] for d in daily.values()), default=0), 4),
            'profitable_days': sum(1 for d in daily.values() if d['pnl'] > 0),
            'total_days': len(daily),
            'daily': [{'date': k, **v} for k, v in sorted(daily.items())],
            'equity': [dict(e) for e in equity],
        }
    except Exception as e:
        logger.error(f"DB get_overall_stats error: {e}")
        return {}


def export_csv():
    try:
        conn = get_conn()
        trades = conn.execute('SELECT * FROM trades ORDER BY timestamp ASC').fetchall()
        conn.close()
        lines = ['timestamp,date,pair,side,market,entry_price,exit_price,qty,pnl,reason,duration_sec']
        for t in trades:
            t = dict(t)
            lines.append(
                f"{t['timestamp']},{t['date']},{t['pair']},{t['side']},{t['market']},"
                f"{t['entry_price']},{t['exit_price']},{t['qty']},{t['pnl']},"
                f"{t['reason']},{t['duration_sec']}"
            )
        return '\n'.join(lines)
    except Exception as e:
        logger.error(f"DB export_csv error: {e}")
        return ''
