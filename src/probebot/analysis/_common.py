"""
probebot analysis/_common.py — Gemeinsame Hilfsfunktionen fuer alle run_analysis.sh-Module.

Analog zu dnabots analysis/utils.py, aber auf probebots Backtester/Config/
bot_spec-Struktur aufbauend statt auf einer Genome-Datenbank.
"""
import json
from pathlib import Path
from typing import Dict, List

ROOT = Path(__file__).parent.parent.parent.parent
SETTINGS_PATH = ROOT / 'settings.json'
DOCS_DIR = ROOT / 'docs'

G  = '\033[0;32m'
Y  = '\033[1;33m'
R  = '\033[0;31m'
C  = '\033[0;36m'
NC = '\033[0m'

COLORS = ['#2563eb', '#16a34a', '#dc2626', '#d97706', '#7c3aed',
          '#0891b2', '#db2777', '#059669', '#ea580c', '#8b5cf6']


# ─── Configs / OOS-Daten / Backtest (reuse show_results.py) ──────────────────

def load_configs() -> List[Dict]:
    from probebot.analysis.show_results import _load_configs
    return _load_configs()


def prompt_capital_override(configs: List[Dict]) -> List[Dict]:
    """Fragt ein Gesamt-Startkapital ab (Enter = Summe der Config-Werte,
    proportional aufgeteilt nach den bisherigen relativen Gewichten der
    Configs). Siehe show_results.py:_prompt_capital_override."""
    from probebot.analysis.show_results import _prompt_capital_override
    return _prompt_capital_override(configs)


def load_oos_data(config: Dict, start_date: str = None, end_date: str = None):
    """Returns df_oos, split_idx, intrusion_info."""
    from probebot.analysis.show_results import _load_oos_data
    return _load_oos_data(config, start_date, end_date)


def run_oos_backtest(config: Dict, start_date: str = None, end_date: str = None):
    """Returns dict mit n_trades/win_rate/pnl_pct/max_drawdown/sharpe/trades/... oder None."""
    from probebot.analysis.show_results import _run_oos_backtest
    return _run_oos_backtest(config, start_date, end_date)


def run_backtest_custom(config: Dict, param_overrides: Dict = None,
                         start_date: str = None, end_date: str = None,
                         start_capital_override: float = None):
    """Wie run_oos_backtest, aber mit ueberschreibbaren Backtest-Parametern
    (fee_pct_per_side, sl_pct, tp_rr, t_threshold, min_score, ...) fuer
    Sweeps/Sensitivity-Analysen. Gibt None zurueck wenn keine Daten."""
    from probebot.analysis.backtester import run_backtest
    bot_spec = load_bot_spec(config)
    entry_conditions = bot_spec.get('entry_conditions', {})
    tradeable = config['strategy']['tradeable_types']
    params = {**config.get('signal', {}), **config.get('risk', {})}
    if param_overrides:
        params.update(param_overrides)
    start_capital = (start_capital_override if start_capital_override is not None
                      else config.get('risk', {}).get('start_capital', 100.0))
    df_oos, split_idx, _ = load_oos_data(config, start_date, end_date)
    if df_oos is None or len(df_oos) == 0:
        return None
    return run_backtest(df_oos, entry_conditions, tradeable, params, start_capital)


def load_bot_spec(config: Dict) -> Dict:
    bot_spec_path = config.get('strategy', {}).get('bot_spec_path', '')
    if not Path(bot_spec_path).exists():
        sym = config['market']['symbol'].replace('/', '_').replace(':', '_')
        tf  = config['market']['timeframe']
        bot_spec_path = str(ROOT / 'artifacts' / 'db' / f'bot_spec_{sym}_{tf}.json')
    with open(bot_spec_path, encoding='utf-8') as f:
        return json.load(f)


def config_name(config: Dict) -> str:
    sym = config['market']['symbol']
    tf  = config['market']['timeframe']
    return f"{sym.split('/')[0]} {tf}"


# ─── Settings / Telegram ──────────────────────────────────────────────────────

def load_settings() -> Dict:
    try:
        with open(SETTINGS_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def get_telegram():
    from probebot.utils.telegram import load_telegram_config
    tg = load_telegram_config()
    token = tg.get('bot_token', '')
    chat_id = tg.get('chat_id', '')
    return (token, chat_id) if token and chat_id else (None, None)


def send_photo(token, chat_id, path, caption=''):
    from probebot.utils.telegram import send_photo as _send_photo, send_document as _send_document
    if _send_photo(token, chat_id, path, caption):
        return True
    # sendPhoto lehnt Bilder ab deren Breite+Hoehe > 10000px liegt (z.B. Analysen
    # mit vielen Configs, wo die Chart-Hoehe pro Config skaliert) — als Dokument
    # gibt es dieses Limit nicht, das Bild kommt so trotzdem an.
    return _send_document(token, chat_id, path, caption)


# ─── Charts ────────────────────────────────────────────────────────────────────

def style_axes(*axes):
    """Einheitliches Dark-Theme fuer alle Analyse-Charts."""
    for ax in axes:
        ax.set_facecolor('#1e293b')
        ax.tick_params(colors='#94a3b8')
        for spine in ax.spines.values():
            spine.set_color('#334155')
        ax.grid(True, alpha=0.15, color='#475569')
        ax.xaxis.label.set_color('#94a3b8')
        ax.yaxis.label.set_color('#94a3b8')
        ax.title.set_color('white')


def save_send(fig, name: str, caption: str = '', no_telegram: bool = False) -> str:
    """Speichert Chart lokal in docs/ + im System-Temp-Verzeichnis und sendet optional via Telegram."""
    import tempfile
    import matplotlib.pyplot as plt
    path = str(Path(tempfile.gettempdir()) / f'probebot_{name}.png')
    docs = DOCS_DIR / f'{name}_latest.png'
    DOCS_DIR.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=150, bbox_inches='tight', facecolor='#0f172a')
    fig.savefig(str(docs), dpi=150, bbox_inches='tight', facecolor='#0f172a')
    plt.close(fig)
    print(f"  {G}Chart: {path}{NC}")
    if not no_telegram:
        token, chat_id = get_telegram()
        if token:
            if send_photo(token, chat_id, path, caption):
                print(f"  {G}Via Telegram gesendet.{NC}")
            else:
                print(f"  {R}Telegram-Versand fehlgeschlagen.{NC}")
        else:
            print(f"  {Y}Telegram nicht konfiguriert.{NC}")
    return path


# ─── Gemeinsame Statistik-Helfer ───────────────────────────────────────────────

def equity_curve(trades: List[Dict], start_capital: float) -> List[float]:
    """Kumulative Equity-Kurve aus einer Trade-Liste (pnl-Feld, chronologisch)."""
    equity = [start_capital]
    cap = start_capital
    for t in trades:
        cap += t.get('pnl', 0.0)
        equity.append(cap)
    return equity


def max_drawdown_from_equity(equity: List[float]) -> float:
    peak = equity[0] if equity else 0.0
    max_dd = 0.0
    for e in equity:
        peak = max(peak, e)
        if peak > 0:
            max_dd = max(max_dd, (peak - e) / peak * 100.0)
    return max_dd
