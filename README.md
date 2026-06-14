# Probebot — Market Forensics Engine + Trading Bot

Probebot ist ein zweiteiliges System:

1. **Forensics Engine** — analysiert historische Kursbewegungen kausal, findet wiederkehrende Vorbedingungen und wählt automatisch die passende Trading-Strategie
2. **Trading Bot** — optimiert Signal-Parameter und Risiko auf den 70%-Trainingsdaten; validiert strikt auf den unsichtbaren 30%-OOS-Daten

---

## Workflow-Überblick

```
run_pipeline.sh
│
├── Phase 1 — Forensik
│   ├── Daten laden (OHLCV, Bitget/CCXT)
│   ├── 177 Features berechnen
│   ├── 12 Bewegungstypen erkennen
│   ├── Welch t-Test → stärkste Prädiktoren
│   ├── K-Means Clustering → Fingerabdrücke
│   ├── MTF Drill-Down → Entry-Timing
│   ├── OOS-Validierung (STRICT 70/30)
│   ├── Strategie-Selektion (BREAKOUT / MOMENTUM / ORDERFLOW / …)
│   └── bot_spec.json + data_SYM_TF.parquet speichern
│
└── Phase 2 — Optimizer (optional, direkt im Anschluss)
    ├── Optuna — optimiert {t_threshold, min_score, min_hit_rate,
    │             sl_pct, tp_rr, leverage, risk_per_trade_pct, max_hold_bars}
    ├── NUR auf den 70%-Trainingsdaten — OOS-Periode NIE gesehen
    └── config_SYM_TF.json schreiben

show_results.sh
├── Mode 1 — OOS-Backtest Tabelle (30%-Testperiode, nie gesehen)
├── Mode 2 — Portfolio-Simulation (mehrere Configs kombinieren)
├── Mode 3 — Auto-Portfolio-Optimizer (greedy, DD-Constraint)
├── Mode 4 — Equity-Charts + Telegram
└── Mode 5–9 — Forensik-Rohdaten (Prädiktoren, Bewegungen, Cluster …)
```

---

## 70/30 Regel (strikt)

| Phase | Datenmenge | Zweck |
|-------|-----------|-------|
| Training (70%) | `df[:split_idx]` | Forensik, Korrelation, Optuna-Optimizer |
| OOS-Test (30%) | `df[split_idx:]` | Nur `show_results.sh` Mode 1–4 |

Der `split_idx` wird in `bot_spec.json` und `config_*.json` gespeichert.  
Der Optimizer erhält ausschließlich den Trainings-Slice — enforcement liegt im Caller (`run_pipeline.sh` + `optimizer.py`).

---

## Schnellstart

```bash
# 1. Repo klonen + installieren
git clone https://github.com/Youra82/probebot.git
cd probebot
bash install.sh

# 2. secret.json anlegen
cp secret.json.example secret.json
nano secret.json          # Telegram + Bitget API eintragen

# 3. Forensik + Optimizer starten (interaktiv)
bash run_pipeline.sh      # fragt Symbol, Zeitraum, Timeframe, Trials …

# 4. OOS-Ergebnis prüfen
bash show_results.sh      # Mode 1 → OOS-Backtest-Tabelle

# 5. Live-Scanner (erklärt was JETZT am Markt passiert)
bash run_live.sh

# 6. Update nach Code-Änderungen
bash update.sh
```

---

## Forensics Engine

### Bewegungserkennung (12 Typen)

| Typ | Beschreibung |
|-----|-------------|
| `BREAKDOWN` | Schlusskurs bricht unter N-Bar-Konsolidierung — Short |
| `BREAKOUT_UP` | Schlusskurs bricht über N-Bar-Konsolidierung — Long |
| `IMPULSE_DOWN/UP` | Einzelne Kerze > 2× ATR |
| `REVERSAL_DOWN/UP` | Trendwende nach N aufeinanderfolgenden Kerzen |
| `SQUEEZE_RELEASE_DOWN/UP` | Volatilitätskompression → plötzliche Ausdehnung |
| `ACCELERATION_DOWN/UP` | Momentum-Surge im laufenden Trend |
| `GAP_DOWN/UP` | Lücke zwischen Close und nächstem Open |

`min_move_pct` wird **automatisch kalibriert** (Ziel: ~300 Events/Jahr) — kein manueller Wert nötig.

### Feature-Engine (177 Indikatoren)

**Technische Analyse:**
RSI (7/14/21), MACD + Histogramm, Bollinger Bands, Keltner Channels, ATR (7/14/21), ADX + DI,
Supertrend, CCI, Williams %R, Stochastic RSI, MFI, Donchian, Ichimoku, EMA/SMA (5 Perioden),
WMA, HMA, Log-Returns, Candle-Body-Ratios, Engulfing, Squeeze-Indikator

**Physik- & Komplexitäts-Indikatoren:**
Shannon Entropy (10/20/40-Bar), Hurst-Exponent (30/60/100-Bar), Higuchi Fractal Dimension,
DFA Alpha, Kalman-Velocity, Varianz-Ratio-Test, WPI (Wick Pressure Imbalance),
Memory Pressure, CCT (Candle Compression Tension), FFT Dominanzperiode,
Hilbert-Phase (cos/sin), Lyapunov-Exponent, Autocorrelation (Lag 1/5/10)

**Market Structure:**
Swing Highs/Lows, Fair Value Gaps (FVG), Order Blocks, HH/HL/LH/LL-Struktur,
N-Bar-Breakouts, Gap-Detektion, VWAP, Inside/Outside/Pin Bars, Engulfing, Range-Position

**Volume-Analyse:**
OBV, CVD (Cumulative Volume Delta), Volume Profile POC (20/50-Bar),
Buy/Sell Pressure Ratio, Institutional Candle Detektor, Volume Entropy, MFI-Divergenz

**Composite Scores:**
DNA-Code, Regime Consensus (TREND/RANGE/CHAOS), Trend Score, Momentum Score, Move Readiness

### Statistische Analyse

- **Welch's t-Test** — Welche der 177 Features sind vor Bewegungen signifikant erhöht/erniedrigt?
- **Cohen's d** — Effektstärke
- **Lift-Faktor** — Wie viel prädiktiver vs. Zufall?
- **Hit-Rate** — In wie viel % der Events war die Bedingung erfüllt?
- **OOS-Validierung** — Recall + Precision + Degradation auf den 30% Testdaten

### Strategie-Selektion (automatisch)

Nach der OOS-Validierung berechnet `strategy_selector.py` einen Score pro Strategie-Kategorie:

| Kategorie | Features |
|-----------|---------|
| `BREAKOUT` | Structure-Features (FVG, OB, Swings, Breakout-Signale) |
| `MOMENTUM` | Momentum-Features (RSI, MACD, ADX, EMA-Distanz …) |
| `ORDERFLOW` | Volume-Features (CVD, OBV, Pressure, MFI …) |
| `COMPLEXITY` | Physik-Features (Entropy, Hurst, Lyapunov …) |

Score = Σ|t-Statistik| der signifikanten Features der Kategorie.  
Der Optimizer verwendet die Strategie mit dem höchsten Score. Bei zwei Kategorien mit ähnlichem Score (≥ 75%) → `HYBRID`.

---

## Optimizer

`optimizer.py` läuft ausschließlich auf `df[:split_idx]` (70%-Trainingsperiode).

**Optimierte Parameter:**

| Parameter | Bereich | Beschreibung |
|-----------|---------|-------------|
| `t_threshold` | 2.0 – 8.0 | Mindest-t-Statistik um Feature als Signal zu werten |
| `min_score` | 5 – 100 | Mindest-Gesamtscore für Entry |
| `min_hit_rate` | 0.20 – 0.85 | Mindestanteil erfüllter Signal-Bedingungen |
| `sl_pct` | 0.5 – 5.0 | Stop-Loss in % vom Entry |
| `tp_rr` | 1.0 – 5.0 | Take-Profit Ratio (TP = SL × tp_rr) |
| `leverage` | 3 – 20 | Hebelwirkung |
| `risk_per_trade_pct` | 0.5 – 3.0 | Risiko pro Trade in % des Kapitals |
| `max_hold_bars` | 3 – 96 | Max. Haltedauer in Kerzen |

**Modi:**
- `best_profit` — maximiert PnL (nur DD-Grenze)
- `strict` — zusätzlich Win-Rate-Minimum

Optuna-Study wird in `artifacts/db/optuna_probebot.db` (SQLite) persistiert.  
Config wird nur überschrieben wenn das neue Ergebnis strikt besser ist.

---

## Signal-Logik (Backtester)

Für jede Kerze:

```
score = 0
for feature in entry_conditions[move_type]:
    if |t| >= t_threshold:
        if (feature > baseline_avg und direction == 'above')
        oder (feature < baseline_avg und direction == 'below'):
            score += |t|

n_met   = Anzahl erfüllter Bedingungen
n_total = Gesamtzahl relevanter Bedingungen

Entry wenn: score >= min_score AND n_met / n_total >= min_hit_rate
```

SL/TP werden vom Schlusskurs berechnet:
- Long: `SL = close × (1 - sl_pct/100)` / `TP = close × (1 + sl_pct × tp_rr / 100)`
- Short: umgekehrt

Trade wird nach `max_hold_bars` Kerzen force-closed (falls weder SL noch TP erreicht).

---

## show_results.sh — Modi

```
Mode 1 — OOS-Backtest Tabelle
  Alle config_*.json werden auf den 30%-Testdaten gebacktestet.
  Ausgabe: Ranking-Tabelle nach PnL (Trades, Win-Rate, MaxDD, Sharpe).
  Optional: Trade-Liste einer Config anzeigen.

Mode 2 — Portfolio-Simulation
  Wähle beliebige Configs aus und berechne kombinierte Portfolio-Stats.
  (Kapital, PnL, Win-Rate, Einzelergebnisse)

Mode 3 — Auto-Portfolio-Optimizer
  Greedy-Selektion: fügt Strategien hinzu solange kombinierter DD ≤ Grenze.
  Kann Ergebnis direkt in settings.json als active_strategies speichern.

Mode 4 — Equity-Charts
  Matplotlib Equity-Kurven aller Configs (OOS).
  Speichert PNG in artifacts/charts/ + optional Telegram-Versand.

Mode 5 — Stärkste Prädiktoren (t-Statistik)
Mode 6 — Letzte 50 Bewegungen aus forensics.db
Mode 7 — Pattern-Cluster Zusammenfassung
Mode 8 — Letzten JSON-Report anzeigen
Mode 9 — Bewegung nach Datum suchen
```

---

## Live-Scanner

`bash run_live.sh` prüft ob der Markt **gerade jetzt** eine starke Bewegung macht:

1. **Alarm-Header** — Bewegungstyp, Magnitude, aktueller Regime/RSI/Entropy/Hurst
2. **Warum ist das passiert?** — Priorisierte Ursachen-Liste (Entropy-Anstieg, Hurst-Regime, RSI-Divergenz, EMA-Bruch, Volumen-Spike, CVD-Divergenz, Squeeze-Release, Wick-Druck, DB-Prädiktoren)
3. **Historischer Vergleich** — Ähnlichste Events aus `forensics.db` (Cosine-Similarity) + Prognose
4. **MTF Drill-Down** — Entry-Confidence 0–10 pro Timeframe

Als Cron-Job:
```bash
*/15 * * * * cd /pfad/zu/probebot && bash run_live.sh >> logs/live_cron.log 2>&1
```

---

## Konfiguration

### settings.json

```json
{
  "exchange": "bitget",
  "symbol": "BTC/USDT:USDT",
  "primary_timeframe": "1d",
  "drill_down_timeframes": ["4h", "1h", "15m", "5m", "1m"],
  "start_date": "2022-01-01",
  "report_top_n": 5,
  "drill_down": true,
  "scan_candles": 5,
  "optimizer_trials": 100,
  "start_capital": 100,
  "max_drawdown": 30
}
```

### secret.json (nicht im Repo)

```json
{
  "telegram": {
    "bot_token": "DEIN_BOT_TOKEN",
    "chat_id": "DEINE_CHAT_ID"
  },
  "probebot": {
    "api_key": "...",
    "api_secret": "...",
    "passphrase": "..."
  }
}
```

---

## Zeitrahmen & Daten (Bitget)

| Timeframe | Verfügbar ab | Kerzen/Jahr | Hinweis |
|-----------|-------------|-------------|---------|
| `1d` | 2021-01-01 | ~365 | 1 bekannte Lücke |
| `4h` | 2021-01-01 | ~2 200 | lückenlos |
| `1h` | 2021-01-01 | ~8 800 | lückenlos |
| `15m` | 2023-01-01 | ~35 000 | lückenlos |
| `5m` | 2024-01-01 | ~105 000 | |
| `1m` | 2025-01-01 | ~525 000 | |

---

## Projektstruktur

```
probebot/
├── src/probebot/
│   ├── data/
│   │   └── loader.py               # CCXT Datenloader (limit=200, gap-free)
│   ├── features/
│   │   ├── technical.py            # ~25 TA-Indikatoren
│   │   ├── physics.py              # ~15 Physik/Entropie-Features
│   │   ├── structure.py            # Market Structure (FVG, OB, Swings)
│   │   ├── volume.py               # Volume-Analyse (CVD, POC, Pressure)
│   │   └── engine.py               # Kombiniert alle → 177 Features
│   ├── detection/
│   │   └── detector.py             # 12 Bewegungstypen
│   ├── forensics/
│   │   ├── database.py             # SQLite (artifacts/db/forensics.db)
│   │   ├── miner.py                # Pattern-Mining + Cosine-Similarity
│   │   ├── correlator.py           # Welch t-Test + K-Means Clustering
│   │   ├── validator.py            # OOS-Validierung (70/30 Split)
│   │   └── drill_down.py           # MTF Zoom + Entry-Scoring
│   ├── analysis/
│   │   ├── strategy_selector.py    # Auto-Strategie-Selektion (BREAKOUT/MOMENTUM/…)
│   │   ├── backtester.py           # Signal-Scoring + Trade-Simulation
│   │   ├── optimizer.py            # Optuna (NUR 70% Trainingsdaten)
│   │   └── show_results.py         # Modes 1-4 (OOS Evaluation)
│   ├── live/
│   │   ├── scanner.py              # LiveScanner: erkennt + erklärt aktuelle Moves
│   │   └── alerter.py              # Telegram-Formatting für Live-Alerts
│   ├── report/
│   │   ├── generator.py            # Rich Terminal-Output
│   │   ├── charts.py               # Matplotlib Charts (5 Typen)
│   │   ├── html_report.py          # HTML-Report
│   │   └── bot_spec.py             # bot_spec.json Generator (split_date, split_idx …)
│   ├── utils/
│   │   └── telegram.py             # Telegram: Text, Photo, Document
│   └── run.py                      # Haupteinstiegspunkt + Parquet-Export
│
├── artifacts/
│   ├── db/                         # forensics.db, bot_spec_*.json, optuna_probebot.db
│   ├── data/                       # data_SYM_TF.parquet (Feature-Cache)
│   └── charts/                     # generierte PNGs
│
├── src/probebot/strategy/
│   └── configs/                    # config_SYM_TF.json (Optimizer-Output)
│
├── logs/                           # pipeline_*.log + live_*.log
├── settings.json
├── requirements.txt
├── install.sh                      # Installation (.venv + pip)
├── update.sh                       # Git pull + Abhängigkeiten aktualisieren
├── run_pipeline.sh                 # Phase 1 Forensik + Phase 2 Optimizer
├── run_live.sh                     # Live-Scanner (manuell oder cron)
├── show_status.sh                  # DB-Statistiken + Top-Prädiktoren
└── show_results.sh                 # Mode 1-4 (OOS Trading) + Mode 5-9 (Forensik)
```

---

## Abhängigkeiten

```
ccxt>=4.0.0          # Exchange-Anbindung (Bitget, Binance …)
pandas>=2.0.0
numpy>=1.24.0
ta>=0.11.0           # Technische Indikatoren
scipy>=1.10.0        # Welch t-Test, Statistik
scikit-learn>=1.3.0  # K-Means, RobustScaler, cosine_similarity
optuna>=3.0.0        # Hyperparameter-Optimizer
pyarrow>=12.0.0      # Parquet-Feature-Cache
matplotlib>=3.7.0    # Charts
requests>=2.28.0     # Telegram HTTP
rich>=13.0.0         # Terminal-Ausgabe
```

---

## Lizenz

Privat — alle Rechte vorbehalten.
