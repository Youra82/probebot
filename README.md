# probebot — Market Forensics Engine + Adaptive Trading Bot

Probebot analysiert historische Krypto-Märkte auf statistisch signifikante Bewegungsmuster,
wählt automatisch die passende Handelsstrategie und führt live Trades auf Bitget Futures aus.

**Kernprinzip:** Erst verstehen, dann handeln — keine blinde Optimierung, sondern statistische Forensik mit strikter 70/30-Datentrennung.

---

## Inhaltsverzeichnis

1. [Architekturüberblick](#architekturüberblick)
2. [Phase 1 — Forensik](#phase-1--forensik)
3. [Phase 2 — Optimizer](#phase-2--optimizer)
4. [Phase 3 — OOS Evaluation](#phase-3--oos-evaluation)
5. [Phase 4 — Live Trading](#phase-4--live-trading)
6. [Die 70/30-Regel](#die-7030-regel)
7. [Feature-Katalog (177 Features)](#feature-katalog)
8. [Bewegungstypen (12 Typen)](#bewegungstypen)
9. [Handelsstrategien (6 Typen)](#handelsstrategien)
10. [Projektstruktur](#projektstruktur)
11. [Installation](#installation)
12. [Konfiguration](#konfiguration)
13. [Empfohlene Symbole](#empfohlene-symbole)
14. [Live-Betrieb & Cron](#live-betrieb--cron)
15. [Dateien & Artefakte](#dateien--artefakte)

---

## Architekturüberblick

```
PHASE 1 — FORENSIK                    PHASE 2 — OPTIMIZER
  run_pipeline.sh                        run_pipeline.sh (nach Forensik)
    |                                      |
  probebot.run                           probebot.analysis.optimizer
    |-- DataLoader (ccxt/Bitget)            |-- bot_spec.json  (Eingabe)
    |-- compute_all_features (177)          |-- data.parquet   (NUR 70%!)
    |-- MovementDetector (12 Typen)         |-- Optuna (100+ Trials)
    |-- STRICT 70/30 SPLIT                  `-- config_SYM_TF.json (Ausgabe)
    |-- PatternMiner + Correlator
    |-- OutOfSampleValidator            PHASE 3 — OOS EVALUATION
    |-- select_strategy (BREAKOUT, …)     show_results.sh
    |-- DrillDownEngine (MTF)               |-- Mode 1: OOS Backtest Ranking
    |-- HTML-Report                         |-- Mode 2: Portfolio Simulation
    `-- bot_spec.json ──────────────────►   |-- Mode 3: Auto-Portfolio-Optimizer
                                            `-- Mode 4: Equity Chart

PHASE 4 — LIVE TRADING
  run_live_bot.sh -> master_runner.py
    | (für jede aktive Strategie)
  strategy/run.py
    |-- @guardian_decorator (Crash-Schutz)
    |-- _load_config (config_SYM_TF.json)
    |-- _load_bot_spec (bot_spec_SYM_TF.json)
    |-- _compute_signal (177 Features -> Score)
    |     `-- compute_trade_params (BREAKOUT/MOMENTUM/…)
    `-- full_trade_cycle
          |-- housekeeper (cancel all + close orphans)
          |-- ensure_tp_sl (Safety-Net)
          `-- _execute_trade
                |-- Entry Market Order
                |-- SL Trigger Order
                `-- TP (Fixed) oder Trailing Stop
```

---

## Phase 1 — Forensik

**Befehl:** `bash run_pipeline.sh`

Analysiert historische OHLCV-Daten, findet statistisch belegbare Bewegungsmuster
und erstellt die Wissensgrundlage für den Optimizer.

### 1.1 Datenladen + Qualitätsprüfung

```python
loader.fetch(symbol, timeframe, start_date, end_date)
```

- Bitget Futures via ccxt
- Automatischer Lücken-Check: erkennt fehlende Kerzen (>10x erwarteter Abstand = Warnung)
- Daten-Cache als Parquet: `artifacts/data/data_SYM_TF.parquet`

**Bitget historische Datenverfügbarkeit:**

| Timeframe | Verfügbar ab | Kerzen/Jahr | Anmerkung                |
|-----------|-------------|-------------|--------------------------|
| 1d        | 2021        | ~365        | 1 bekannte 2-Tage-Lücke |
| 4h        | 2021        | ~2.200      | lückenlos                |
| 1h        | 2021        | ~8.800      | lückenlos                |
| 15m       | 2023        | ~35.000     | lückenlos                |
| 5m        | 2024        | ~105.000    | —                        |
| 1m        | 2025        | ~525.000    | —                        |

### 1.2 Feature-Berechnung (177 Features)

Vier Kategorien:

```
technical.py   -> RSI, MACD, Bollinger, ATR, EMA, Stochastic, ADX, …
physics.py     -> Hurst-Exponent, Shannon-Entropy, Lyapunov, DFA, WPI, …
structure.py   -> Swing High/Low, Donchian, FVG, Order Blocks, Ichimoku, …
volume.py      -> CVD, OBV, VWAP, Volume POC, MFI, Buy/Sell Pressure, …
```

Zusätzliche Composite-Scores (`engine.py`):
- `regime` — Markt-Regime (TREND/RANGE/SQUEEZE/REVERSAL)
- `trend_score` — -10 bis +10 (Multi-EMA-Konsens)
- `momentum_score` — -10 bis +10 (RSI/MACD/ROC)
- `move_readiness` — 0 bis 10 (Wie bereit ist der Markt für eine große Bewegung?)
- `dna_code` — Kerzen-Encoding (inspiriert von dnabot)

### 1.3 Bewegungserkennung (12 Typen)

`MovementDetector` erkennt pro Kerze genau eine Bewegung (höchste Priorität gewinnt):

| Priorität   | Bewegungstyp               | Erkennungslogik                                    |
|-------------|---------------------------|----------------------------------------------------|
| 0 (höchste) | SQUEEZE_RELEASE_UP/DOWN   | ATR-Z zuvor < -1.0, jetzt > 0.5 (Volatilitätsstoß) |
| 1           | BREAKOUT_UP / BREAKDOWN   | Close bricht N-Bar-Hoch/-Tief (timeframe-adaptiv)  |
| 2           | REVERSAL_UP / REVERSAL_DOWN | N aufeinanderfolgende Kerzen gleicher Richtung   |
| 3           | IMPULSE_UP / IMPULSE_DOWN | Kerzenkörper > atr_impulse × ATR (Standard: 1.5x) |
| 4           | ACCELERATION_UP/DOWN      | energy_z > 3.0 + starker Kerzenmove               |
| 5 (niedrigste) | GAP_UP / GAP_DOWN      | Open vs. Previous Close > 0.5%                    |

**Adaptive Parameter** (je nach Timeframe):
- `breakout_bars`: 1d=20, 1h=48, 15m=80, 1m=120
- `reversal_min_run`: 1d=5, 1h=12, 15m=20, 1m=30

**Auto-Kalibrierung `min_move_pct`:** Der Bot sucht den höchsten Schwellwert,
der noch mindestens 300 Events/Jahr liefert — automatisch, kein manueller Input nötig.

### 1.4 Strikte 70/30-Datentrennung

```
split_idx = int(len(df) * 0.70)

df_train     = df.iloc[:split_idx]   <- ALLES LERNEN PASSIERT NUR HIER
df_test      = df.iloc[split_idx:]   <- VOLLSTÄNDIG UNSICHTBAR für Lernen
```

`split_date` und `split_idx` werden in `bot_spec.json` gespeichert.

### 1.5 Pattern Mining + Korrelation (NUR Trainingsdaten)

```python
miner.mine_movements(df_train, movements_train, …)
correlator.analyze(df_train, movements_train, …)
```

- **PatternMiner**: Speichert Feature-Vektoren (10 Kerzen vor Bewegung) in SQLite
- **Correlator**: Berechnet t-Statistiken pro Feature pro Bewegungstyp
  - `mean_before`: Feature-Wert in den 10 Kerzen vor dem Event
  - `mean_all`: Feature-Wert im gesamten Trainingszeitraum
  - `predictive_pct`: % der Ereignisse mit Feature in richtiger Zone
  - Signifikanzfilter: |t| >= 4.0 für Einstiegsbedingungen

### 1.6 Out-of-Sample Validierung

```python
validator.validate(df, split_idx, movements_test, correlations)
```

Prüft jeden Bewegungstyp auf Generalisierbarkeit (auf den 30% Test-Daten):

| Label       | Bedeutung                                           | Handelsrelevanz      |
|-------------|-----------------------------------------------------|----------------------|
| ROBUST      | OOS-Recall >= 60% UND OOS-Precision >= 50%          | Handelbar            |
| STABIL      | OOS-Recall >= 40% UND OOS-Precision >= 35%          | Handelbar            |
| UNSICHER    | Teilweise Generalisierung                           | Nur mit Vorsicht     |
| OVERFITTING | In-Sample gut, OOS schlecht                         | Nicht handeln        |

Nur ROBUST + STABIL kommen in `tradeable_types` → Optimizer → Live Trading.

### 1.7 Strategie-Auswahl (automatisch)

`strategy_selector.py` berechnet Scores basierend darauf, welche Feature-Kategorien
vor Bewegungen dominant sind:

```
score['BREAKOUT']   = structure_features dominant  (donchian, FVG, order blocks, breakout_*)
score['MOMENTUM']   = momentum_features dominant   (RSI, MACD, EMA-Distanz, ROC)
score['ORDERFLOW']  = volume_features dominant     (CVD, OBV, buy/sell pressure)
score['COMPLEXITY'] = physics_features dominant    (Entropy, Hurst, DFA, Lyapunov)
score['MEAN_REV']   = Hurst-Features vorhanden    (hurst_30/60, variance_ratio)
score['SQUEEZE']    = squeeze_release_count > 10% (Anteil an allen Events)
```

Wenn Top-1 und Top-2 Score nur 25% auseinanderliegen → `HYBRID` (kombiniert).

Das Ergebnis wird in `bot_spec.json` als `selected_strategy` gespeichert
und bestimmt die SL/TP-Logik im Live Trading.

### 1.8 MTF Drill-Down

Für die Top-N stärksten Ereignisse untersucht der DrillDownEngine
jede Bewegung auf mehreren Timeframes (1d → 4h → 1h → 15m → 5m → 1m):

- Entry-Konfidenz pro Timeframe (0-10)
- Precursor-Features (was passierte vorher auf jedem TF)
- Ähnliche vergangene Events (fingerprint similarity)

### 1.9 Ausgabe-Artefakte

| Datei | Inhalt |
|-------|--------|
| `artifacts/db/bot_spec_SYM_TF.json` | Einstiegsbedingungen, Korrelationen, OOS-Validation, Strategie, split_idx |
| `artifacts/db/report_SYM_TF.html` | Interaktives Dashboard (Dark-Theme, im Browser öffnen) |
| `artifacts/data/data_SYM_TF.parquet` | Feature-DataFrame Cache (für Optimizer) |
| `artifacts/charts/*.png` | Overview, Korrelation, Fingerprint, Cluster, Drill-Down Charts |
| `artifacts/db/forensics.db` | SQLite: movements, commonalities, scan_log |

Per Telegram werden automatisch HTML-Report + bot_spec.json gesendet.

---

## Phase 2 — Optimizer

**Befehl:** `bash run_pipeline.sh` (Phase 2 direkt nach Forensik), **`bash run_optimizer.sh`**
(Phase 2 einzeln nachholen, z.B. wenn beim Pipeline-Lauf mit "n" übersprungen wurde — fragt nur
Symbol/Timeframe/Trials/Kapital/Modus/Max-Drawdown ab, findet `bot_spec_*.json` + `data_*.parquet`
automatisch), oder manuell:

```bash
python -m probebot.analysis.optimizer \
    --symbol BTC/USDT:USDT --timeframe 1h \
    --bot_spec artifacts/db/bot_spec_BTC_USDT_USDT_1h.json \
    --data     artifacts/data/data_BTC_USDT_USDT_1h.parquet \
    --split_idx 24545 --trials 100
```

### Optimierte Parameter (Optuna, 8 Dimensionen)

| Parameter            | Bereich      | Bedeutung                                          |
|----------------------|-------------|----------------------------------------------------|
| `t_threshold`        | 2.0 – 8.0   | Min. t-Statistik für Einstiegsbedingungen          |
| `min_score`          | 5 – 100     | Min. Gesamtscore um Signal auszulösen              |
| `min_hit_rate`       | 0.20 – 0.85 | Min. Anteil erfüllter Bedingungen                  |
| `sl_pct`             | 0.5 – 5.0 % | Fallback SL-Abstand                                |
| `tp_rr`              | 1.0 – 5.0   | Fallback TP Risk:Reward Verhältnis                 |
| `leverage`           | 3 – 20x     | Hebelgrad                                          |
| `risk_per_trade_pct` | 0.5 – 3.0 % | Risikoanteil pro Trade                             |
| `max_hold_bars`      | 3 – 96      | Maximale Haltedauer in Kerzen                      |

### Pruning-Bedingungen

Trials werden abgebrochen wenn:
- Weniger als 20 Trades (statistisch irrelevant)
- Max. Drawdown > `max_drawdown` % (aus settings.json)
- Modus `strict`: Win-Rate < `min_win_rate` %

### Modus-Optionen

| Modus         | Zielfunktion             | Einsatz                     |
|---------------|--------------------------|-----------------------------|
| `best_profit` | Max. PnL%                | Standard — hohe Rendite     |
| `strict`      | Max. PnL% + Win-Rate-Filter | Konservativ — weniger Trades |

### Overfitting-Sperre — `--force`

**Existiert bereits eine `config_SYM_TF.json`, überspringt der Optimizer automatisch und meldet
das** (Optimierungsdatum, bisherige Trials, bisherige PnL) — verhindert, dass wiederholte Läufe
(z.B. per Cron oder aus Versehen) weitere Trials auf denselben 70% Trainingsdaten anhäufen und
sich so an Rauschen überanpassen ("Trial-Akkumulation").

Um trotzdem neu zu optimieren (z.B. nach einem Code-Fix, neuen Daten oder bewusst geänderten
Parametern), explizit erzwingen:

```bash
python -m probebot.analysis.optimizer \
    --symbol BTC/USDT:USDT --timeframe 1h \
    --bot_spec artifacts/db/bot_spec_BTC_USDT_USDT_1h.json \
    --data     artifacts/data/data_BTC_USDT_USDT_1h.parquet \
    --split_idx 24545 --trials 100 --force
```

`--force` löscht die alte Optuna-Study (Trial-Historie startet bei 0) und überschreibt die
bestehende Config garantiert — unabhängig davon, ob das neue Ergebnis numerisch besser aussieht
als das alte. Über `run_pipeline.sh` entspricht das der Frage **"Bestehende DB-Eintraege
loeschen? (j/n)"** → `j` (löscht Config-Datei + Optuna-Study für die gewählten Symbol/TF-Kombinationen,
bevor der Optimizer erneut läuft).

> **Was die Sperre NICHT verhindert:** Mensch-im-Loop-Overfitting — OOS-Ergebnis (Phase 3)
> anschauen, unzufrieden sein, Parameter ändern, mit `--force` neu optimieren, wieder OOS
> anschauen, wiederholen. Jeder einzelne Optimizer-Lauf bleibt technisch sauber (sieht nie die
> 30% OOS-Daten), aber die *Wahl*, wann und wie neu zu optimieren, wird dann indirekt vom
> OOS-Ergebnis gesteuert. Das lässt sich nicht rein technisch verhindern — Faustregel: OOS-Ergebnis
> erst anschauen, wenn man mit den Parametern wirklich fertig ist.

### Ausgabe: `config_SYM_TF.json`

```json
{
  "market":   { "symbol": "BTC/USDT:USDT", "timeframe": "1h" },
  "strategy": {
    "type":            "BREAKOUT",
    "tradeable_types": [{"move_type": "BREAKOUT_UP", "direction": "LONG"}],
    "bot_spec_path":   "artifacts/db/bot_spec_…json"
  },
  "signal": {
    "t_threshold":   5.2,
    "min_score":     38.4,
    "min_hit_rate":  0.61,
    "max_hold_bars": 24
  },
  "risk": {
    "sl_pct":             1.2,
    "tp_rr":              2.8,
    "leverage":           10,
    "risk_per_trade_pct": 1.0,
    "start_capital":      100
  },
  "period": {
    "train_start": "2021-01-01",
    "split_date":  "2023-03-15",
    "oos_end":     "2025-01-01"
  },
  "_meta": {
    "insample_pnl_pct":  142.3,
    "insample_trades":   87,
    "insample_win_rate": 54.0,
    "insample_max_dd":   18.2,
    "note":              "OOS (30%) never used during optimization"
  }
}
```

> **Wichtig:** `_meta.insample_pnl_pct` sind In-Sample-Zahlen (70% Trainingsdaten).
> Das echte Ergebnis zeigt **Phase 3 (show_results.sh Mode 1)** auf den 30% OOS-Daten.
> Sehr hohe In-Sample-Zahlen (> 500%) sind ein Overfitting-Signal.

---

## Phase 3 — OOS Evaluation

**Befehl:** `bash show_results.sh`

Evaluiert die Configs auf den **niemals gesehenen 30% OOS-Daten**.

### Mode 1 — OOS-Backtest Ranking

Lädt alle `config_SYM_TF.json` und backtestet auf `df.iloc[split_idx:]`.
Sortierte Tabelle: Symbol | PnL% | Trades | Win% | MaxDD | Sharpe | Profit-Faktor

### Mode 2 — Portfolio-Simulation

Kombiniert mehrere Configs zu einem Portfolio.
Kumulativer Equity-Verlauf + Portfolio-Metriken.

### Mode 3 — Auto-Portfolio-Optimizer

Greedy-Algorithmus wählt die beste Kombination unter DD-Constraint.
Schreibt die Strategien direkt in `settings.json → live_trading_settings.active_strategies`.

### Mode 4 — Equity Chart

Equity-Kurven-Charts erstellen und per Telegram senden.

### Forensik-Modi (Rohdaten)

| Mode | Inhalt |
|------|--------|
| 5 | Stärkste Prädiktoren (t >= 2.0) pro Bewegungstyp |
| 6 | Letzte 50 erkannte Bewegungen aus SQLite |
| 7 | Pattern-Cluster Zusammenfassung |
| 8 | Neuesten Report anzeigen |
| 9 | Bewegung nach Datum suchen |

---

## Phase 4 — Live Trading

**Befehl:** `bash run_live_bot.sh`

### master_runner.py

Liest `settings.json → live_trading_settings.active_strategies` und steuert alle Strategien:

```
Fall A: Offene Position  →  mode=check  (Positions-Monitor)
Fall B: Freier Slot      →  mode=signal (neues Signal suchen)
        Stoppe wenn max_open_positions erreicht
```

### Signal-Logik (`strategy/run.py`)

```
1. fetch_recent_ohlcv (250 Kerzen, letzte offene Kerze verwerfen)
2. compute_all_features (177 Features)
3. Für jeden tradeable_type in Config:
     score, n_met, n_total = compute_signal_score(last_row, conditions, t_threshold)
     hit_rate = n_met / n_total
     if score >= min_score AND hit_rate >= min_hit_rate:
         kandidat (merke strategy, move_type, score)
4. Bester Kandidat gewinnt
5. compute_trade_params (SL/TP je nach Strategie aus Live-Features)
6. full_trade_cycle(signal)
```

### full_trade_cycle Pattern (gelernt von ltbbot/dnabot)

```
FALL A — Position offen (Tracker status='open'):
  Exchange-Position noch aktiv?
    JA  -> ensure_tp_sl (fehlende SL/TP-Orders neu platzieren)
    NEIN -> Grund erkennen (SL oder TP getroffen via closed trigger orders)
            -> Telegram Abschluss-Nachricht
            -> Tracker idle + Candle-Cooldown

FALL B — Kein Trade (Tracker status='idle'):
  housekeeper (cancel all + verwaiste Positionen schliessen)
  Candle-Cooldown aktiv? -> abbrechen
  Signal vorhanden? -> _execute_trade
```

### Strategie-spezifische SL/TP-Logik (`strategy/signal_logic.py`)

Für jede der 6 Strategien wird SL/TP aus Live-Features berechnet — nicht aus festen Prozentsätzen:

| Strategie   | SL-Quelle                        | TP-Methode                               |
|-------------|----------------------------------|------------------------------------------|
| BREAKOUT    | swing_low x 0.998 (LONG)         | Trailing Stop 0.8% nach 1.5xSL           |
| MOMENTUM    | 1.5 x ATR14                      | Fixed 2.5 x ATR14                        |
| ORDERFLOW   | 1.5 x ATR14                      | VWAP oder Volume POC in 1.5–4xSL Range   |
| MEAN_REV    | swing_low x 0.997 (LONG)         | EMA20 als Mittelwert-Ziel                |
| COMPLEXITY  | 2.0 x ATR14 (breiter)            | Fixed 1.5 x SL                           |
| SQUEEZE     | 0.5 x bb_width                   | Trailing Stop 1.0% nach 1.5xbb_width     |
| HYBRID      | config sl_pct (Fallback)         | config tp_rr (Fallback)                  |

Wenn Feature-Werte nicht verfügbar oder ungültig → automatischer Fallback auf Config-Werte.

### TradeParams Dataclass

```python
@dataclass
class TradeParams:
    sl_price:                  float
    tp_price:                  Optional[float]  # None = Trailing Stop
    use_trailing:              bool
    trailing_activation_price: Optional[float]  # Preis wo Trailing aktiviert
    trailing_pct:              float            # Callback % (z.B. 0.8)
    sl_source:                 str             # z.B. "swing_low=42500.00"
    tp_source:                 str             # z.B. "trailing_stop 0.8%"
```

### Positionsgröße (risiko-basiert)

```python
risk_usdt   = balance * risk_per_trade_pct / 100
sl_distance = abs(entry_price - sl_price)
contracts   = risk_usdt / sl_distance
# Margin-Cap: contracts <= balance * leverage / price * 0.99
```

### Sicherheits-Features (gelernt von ltbbot/dnabot/stbot)

**guardian_decorator** — Fängt alle Ausnahmen, loggt kritisch, sendet Telegram-Alert:
```python
@guardian_decorator
def run_strategy(account, telegram_cfg, symbol, timeframe, mode, logger): ...
```

**ensure_tp_sl** — Prüft jede Runde ob SL + TP noch aktiv, legt fehlende Orders neu an.

**Zombie-Killer** — Nach `cancel_all_orders()` werden verbleibende Trigger-Orders einzeln storniert.

**Per-Symbol Tracker** — `artifacts/tracker/tracker_BTCUSDTUSDT_1h.json`:
```json
{
  "status":               "open",
  "symbol":               "BTC/USDT:USDT",
  "timeframe":            "1h",
  "side":                 "long",
  "strategy":             "BREAKOUT",
  "move_type":            "BREAKOUT_UP",
  "entry_price":          67500.0,
  "sl_price":             66800.0,
  "tp_price":             null,
  "use_trailing":         true,
  "trailing_activation":  68700.0,
  "trailing_pct":         0.8,
  "sl_source":            "swing_low=66850.00",
  "tp_source":            "trailing_stop 0.8% after 1.5xSL",
  "contracts":            0.015,
  "sl_order_id":          "123456",
  "tp_order_id":          "789012",
  "active_since":         "2026-06-14T10:30:00+00:00",
  "candle_blocked_until": ""
}
```

**Candle-Cooldown** — Nach einem Trade kein Re-Entry bis die aktuelle Kerze geschlossen hat.

---

## Die 70/30-Regel

Dies ist die wichtigste Invariante des gesamten Systems:

```
+-------------------------------------------------------------------+
| TRAINING (70%)                   TEST (30%)                       |
|                                                                   |
| * Pattern Mining                 * VOLLSTÄNDIG UNSICHTBAR         |
| * Feature Korrelation            * während Phase 1 + 2            |
| * OOS-Validator (Recall)         * Erst in Phase 3 verwendet      |
| * Optuna Optimizer               * Einmalige ehrliche Auswertung  |
| * Bot-Spec Generierung                                            |
|                                                                   |
| split_idx in bot_spec.json + config.json gespeichert             |
+-------------------------------------------------------------------+
```

**Was ist verboten:**
- OOS-Zahlen (show_results Mode 1) als Optimierungsziel verwenden
- split_idx manuell nachträglich verschieben
- Optimizer mit vollem Datensatz laufen lassen

---

## Feature-Katalog

### Technische Indikatoren (`technical.py`)

| Feature | Beschreibung |
|---------|-------------|
| `rsi_7/14/21` | RSI verschiedene Perioden |
| `stoch_k/d` | Stochastic Oscillator |
| `macd / macd_signal / macd_hist` | MACD |
| `bb_upper / lower / width / position` | Bollinger Bänder |
| `kc_upper / lower / squeeze` | Keltner Channel + Squeeze |
| `ema_9 / 21 / 50 / 200` | Exponentielle Gleitende Durchschnitte |
| `dist_ema_9 / 21 / 50 / 200` | EMA-Distanz (normalisiert) |
| `atr_14 / atr_pct / atr_z` | ATR (absolut, relativ, z-score) |
| `adx / di_plus / di_minus` | Trend-Stärke (DMI) |
| `cci_20` | Commodity Channel Index |
| `willr_14` | Williams %R |
| `roc_5 / 10 / 20` | Rate of Change |
| `supertrend / supertrend_dir` | Supertrend Indikator |
| `ichi_*` | Ichimoku (cloud, base, conversion, above_cloud) |
| `consec_bull / consec_bear` | Aufeinanderfolgende gleichgerichtete Kerzen |

### Physik / Komplexität (`physics.py`)

| Feature | Beschreibung |
|---------|-------------|
| `entropy_10 / 20 / 40` | Shannon-Entropie der Renditen (Unvorhersehbarkeit) |
| `ear_entropy` | Entropie der ATR-Renditen |
| `hurst_30 / 60 / 100` | Hurst-Exponent (< 0.5 = mean-reversion, > 0.5 = trending) |
| `higuchi_fd` | Higuchi Fraktale Dimension |
| `dfa_alpha` | Detrended Fluctuation Analysis |
| `lyapunov` | Lyapunov-Exponent (Chaos-Maß) |
| `autocorr_1` | Lag-1 Autokorrelation |
| `variance_ratio` | Varianz-Verhältnis (zufällig vs. trending) |
| `wpi` | Wave Power Index (Energie der Kurswellen) |
| `memory_pressure` | Hurst-basierter Gedächtnis-Druck |
| `cct` | Complexity-Cascade Time |
| `energy_z` | Z-Score der Kursenergie |
| `kalman_vel` | Kalman-Filter Geschwindigkeit |
| `velocity` | Kursgeschwindigkeit (normalisiert) |

### Marktstruktur (`structure.py`)

| Feature | Beschreibung |
|---------|-------------|
| `swing_high / swing_low` | Aktuelle Swing High/Low Preise |
| `breakout_up / down_10 / 20` | Ausbruch aus N-Bar Range |
| `donchian_high / low / pos` | Donchian Channel |
| `at_donchian_high / low` | An Donchian-Grenze |
| `struct_hh / hl / lh / ll` | Higher High, Higher Low, etc. |
| `range_position_20 / 50` | Position in N-Bar Range |
| `ema_alignment` | EMA-Stack Ausrichtung (bullish/bearish) |
| `fvg_bull / fvg_bear` | Fair Value Gap |
| `bull_ob / bear_ob` | Order Blocks |
| `realized_vol_20` | Realisierte Volatilität |
| `entropy_squeeze` | Entropie-Squeeze (kombiniert BB + KC) |

### Volumen-Analyse (`volume.py`)

| Feature | Beschreibung |
|---------|-------------|
| `cvd` | Cumulative Volume Delta (Kauf vs. Verkauf) |
| `cvd_slope` | CVD Steigung (Momentum des Orderflusses) |
| `obv / obv_z / obv_slope` | On Balance Volume |
| `vwap_20` | Volume-Weighted Average Price |
| `vol_poc_20` | Volume Point of Control (Preis mit meistem Volumen) |
| `volume_z / volume_ratio` | Volumen Z-Score / Verhältnis zum Durchschnitt |
| `mfi_14` | Money Flow Index |
| `buy_pressure / sell_pressure` | Absoluter Kauf-/Verkaufsdruck |
| `cum_pressure_slope` | Steigung des kumulativen Drucks |
| `mfi_divergence` | MFI Divergenz (Preis vs. Geldfluss) |
| `vol_confirm` | Volumen-Bestätigung der Richtung |

---

## Bewegungstypen

12 erkennbare Bewegungstypen (Detector-Prioritäten berücksichtigt):

| Typ | Richtung | Beschreibung |
|-----|----------|-------------|
| `SQUEEZE_RELEASE_UP` | UP | Volatilität expandiert nach Kompression — bullish |
| `SQUEEZE_RELEASE_DOWN` | DOWN | Volatilität expandiert nach Kompression — bearish |
| `BREAKOUT_UP` | UP | Close bricht über N-Bar-Hochpunkt |
| `BREAKDOWN` | DOWN | Close bricht unter N-Bar-Tiefpunkt |
| `REVERSAL_UP` | UP | Trendwende nach N bearishen Kerzen |
| `REVERSAL_DOWN` | DOWN | Trendwende nach N bullishen Kerzen |
| `IMPULSE_UP` | UP | Einzelne Kerze > atr_impulse x ATR |
| `IMPULSE_DOWN` | DOWN | Einzelne Kerze > atr_impulse x ATR |
| `ACCELERATION_UP` | UP | energy_z > 3.0 + starker Move (Trend beschleunigt) |
| `ACCELERATION_DOWN` | DOWN | energy_z > 3.0 + starker Move (Trend beschleunigt) |
| `GAP_UP` | UP | Open > Previous Close + 0.5% |
| `GAP_DOWN` | DOWN | Open < Previous Close − 0.5% |

---

## Handelsstrategien

Der Bot entscheidet automatisch (basierend auf Forensik-Ergebnis) welche Strategie er nutzt.
Jede Strategie hat eigene SL/TP-Logik die aus Live-Features berechnet wird:

### BREAKOUT
Ausbruch aus einer Konsolidierungszone.
- **Signal-Features**: breakout_up/down, donchian_position, struct_hh/ll, ichimoku_above_cloud
- **SL**: Knapp unterhalb swing_low x 0.998 (LONG) / swing_high x 1.002 (SHORT)
- **TP**: Trailing Stop 0.8% — aktiviert bei 1.5x SL-Abstand (Breakouts können weit laufen)
- **Fallback-SL**: config sl_pct x 1.2 (extra Puffer für Konsolidierungszone)

### MOMENTUM
Bereits laufende Bewegung mithandeln.
- **Signal-Features**: rsi_14, macd_hist, dist_ema_21/50, momentum_score, roc_10
- **SL**: 1.5 x ATR14 hinter Entry
- **TP**: 2.5 x ATR14 (festes ATR-basiertes R:R ≈ 1:1.67)

### ORDERFLOW
Institutionellen Kauf-/Verkaufsdruck folgen.
- **Signal-Features**: cvd, cvd_slope, obv_z, buy_pressure, mfi_14, vol_confirm
- **SL**: 1.5 x ATR14
- **TP**: Nächster VWAP oder Volume POC im Bereich 1.5–4x SL (Liquiditätsziel)
- **Fallback**: 2.0 x SL wenn kein VWAP/POC in Range

### MEAN_REV
Extreme Bewegungen fade — Rückkehr zum Mittelwert.
- **Signal-Features**: hurst_30/60 (< 0.5), rsi_14 (extrem), variance_ratio, autocorr_1
- **SL**: swing_low x 0.997 (LONG) — wo der Bounce herkam
- **TP**: EMA20 als Mittelwert-Ziel (mindestens 1.2:1 R:R)
- **Fallback**: 1.5 x SL

### COMPLEXITY
Entropy und Hurst-Regime-Shifts handeln.
- **Signal-Features**: entropy_20/40, hurst_60, dfa_alpha, memory_pressure, wpi
- **SL**: 2.0 x ATR14 (breiter — Signal ist verrauschter)
- **TP**: 1.5 x SL (bescheiden — kein starker Richtungs-Bias)

### SQUEEZE
Auf den Ausbruch aus Volatilitätskompression warten.
- **Signal-Features**: kc_squeeze, bb_width (minimal), entropy_squeeze, atr_z (negativ)
- **SL**: 0.5 x bb_width (innerhalb der Squeeze-Zone)
- **TP**: Trailing Stop 1.0% — aktiviert bei 1.5 x bb_width (explosive Expansion)

### HYBRID
Wird gewählt wenn kein einzelner Ansatz dominiert (Top-2 Scores < 25% auseinander).
- **SL/TP**: Fallback auf config-Werte (sl_pct + tp_rr)

---

## Projektstruktur

```
probebot/
|-- run_pipeline.sh              # Interaktiver Pipeline-Starter (Forensik + Optimizer)
|-- run_optimizer.sh             # Phase 2 (Optimizer) einzeln nachholen, ohne Phase 1 neu zu starten
|-- show_results.sh              # OOS Evaluation + Forensik-Viewer (9 Modi)
|-- run_live_bot.sh              # Live Trading starten
|-- master_runner.py             # Orchestriert alle aktiven Strategien
|-- install.sh                   # Erstinstallation
|-- update.sh                    # VPS Update (git reset + secret.json sichern)
|-- push_configs.sh              # Config + Bot-Spec manuell zurueck ins Repo pushen
|-- settings.json                # Konfiguration
|-- secret.json                  # API-Keys (NICHT im Git)
|-- secret.json.example          # Vorlage für secret.json
|-- requirements.txt
|
|-- src/probebot/
|   |-- run.py                   # Phase 1: Forensik-Hauptprogramm
|   |-- data/
|   |   `-- loader.py            # OHLCV-Download via ccxt
|   |-- features/
|   |   |-- engine.py            # Orchestrierung + Composite Scores
|   |   |-- technical.py         # RSI, MACD, BB, ATR, EMA, ADX, …
|   |   |-- physics.py           # Hurst, Entropy, DFA, Lyapunov, WPI, …
|   |   |-- structure.py         # Swing H/L, Donchian, FVG, OB, Ichimoku, …
|   |   `-- volume.py            # CVD, OBV, VWAP, POC, MFI, …
|   |-- detection/
|   |   `-- detector.py          # MovementDetector (12 Typen + Dedup)
|   |-- forensics/
|   |   |-- database.py          # SQLite Interface
|   |   |-- miner.py             # Pattern Mining (10-Kerzen Lookback)
|   |   |-- correlator.py        # t-Statistik + Cluster-Analyse
|   |   |-- validator.py         # Out-of-Sample Validator (ROBUST/STABIL)
|   |   `-- drill_down.py        # MTF Drill-Down Engine
|   |-- analysis/
|   |   |-- strategy_selector.py # Strategie-Auswahl aus Forensik-Ergebnis
|   |   |-- backtester.py        # Signal-Backtesting Engine
|   |   |-- optimizer.py         # Optuna Optimizer (NUR 70% Trainingsdaten)
|   |   `-- show_results.py      # OOS Evaluation (Modi 1-4)
|   |-- report/
|   |   |-- bot_spec.py          # bot_spec.json Generator
|   |   |-- generator.py         # Terminal-Report Formatter
|   |   |-- charts.py            # Matplotlib Charts
|   |   `-- html_report.py       # Interaktiver HTML Dashboard
|   |-- live/
|   |   |-- scanner.py           # Live-Scanner (Modus 'live')
|   |   `-- alerter.py           # Telegram Live-Alerts
|   |-- strategy/
|   |   |-- run.py               # Live Strategy Runner (@guardian_decorator)
|   |   |-- signal_logic.py      # TradeParams + per-Strategie SL/TP Logik
|   |   `-- configs/             # config_SYM_TF.json (vom Optimizer geschrieben)
|   `-- utils/
|       |-- exchange.py          # Bitget Futures Wrapper (ccxt)
|       |-- trade_manager.py     # full_trade_cycle, ensure_tp_sl, Tracker I/O
|       |-- guardian.py          # guardian_decorator (Crash-Schutz + Telegram)
|       `-- telegram.py          # send_message / send_photo / send_document
|
`-- artifacts/
    |-- db/
    |   |-- forensics.db         # SQLite (movements, commonalities, scan_log)
    |   |-- optuna_probebot.db   # Optuna Trial-Datenbank
    |   |-- bot_spec_*.json      # Pro Symbol+TF (von Forensik erstellt)
    |   |-- config_*.json        # Pro Symbol+TF (vom Optimizer erstellt)
    |   `-- report_*.html        # Interaktive HTML-Reports
    |-- data/
    |   `-- data_*.parquet       # Feature-DataFrame Cache
    |-- charts/
    |   `-- *.png                # Charts
    `-- tracker/
        `-- tracker_*.json       # Pro Symbol+TF: offene Position + Order-IDs
```

---

## Installation

### Erstinstallation (lokal / VPS)

```bash
git clone <repo-url> probebot
cd probebot
bash install.sh
cp secret.json.example secret.json
nano secret.json   # API-Keys eintragen
```

`install.sh` erstellt automatisch:
- Python venv unter `.venv/`
- Alle Dependencies aus `requirements.txt`
- Verzeichnisse: `logs/`, `artifacts/db/`, `artifacts/charts/`, `artifacts/tracker/`

### VPS Update

```bash
bash update.sh
```

`update.sh` sichert `secret.json` vor dem `git reset --hard` und stellt es danach wieder her.

> **Achtung:** `git reset --hard` verwirft lokale Änderungen an bereits von Git getrackten Dateien
> — auch `config_*.json`, falls die auf diesem Rechner neu optimiert, aber nie gepusht wurden.
> Vor `update.sh` also erst `bash push_configs.sh` ausführen, wenn lokale Config/Bot-Spec-Änderungen
> erhalten bleiben sollen (siehe unten).

### Configs + Bot-Specs manuell pushen

```bash
bash push_configs.sh
```

Pusht `config_*.json` (Optimizer-Output) **und** `bot_spec_*.json` (Entry-Bedingungen, normalerweise
nicht von Git getrackt) gemeinsam zurück ins Repo. Sinnvoll nach jedem Optimizer-Lauf, den du behalten
willst — sonst geht das Ergebnis beim nächsten `update.sh` auf diesem oder einem anderen Rechner
verloren. Committet nur Configs, Bot-Specs und `settings.json`, nichts anderes. Bei Push-Konflikt
(Remote hat neuere Commits) wird automatisch gestasht, rebased und erneut versucht.

---

## Konfiguration

### `settings.json`

```json
{
  "exchange":              "bitget",
  "symbol":                "BTC/USDT:USDT",
  "primary_timeframe":     "1d",
  "drill_down_timeframes": ["4h", "1h", "15m", "5m", "1m"],
  "start_date":            "2021-01-01",
  "end_date":              "2025-01-01",
  "min_move_pct":          2.5,
  "lookback_candles":      10,
  "atr_multiplier":        1.5,
  "min_occurrences":       3,
  "correlation_threshold": 0.7,
  "movement_types":        null,
  "report_top_n":          5,
  "drill_down":            true,
  "scan_candles":          5,
  "optimizer_trials":      100,
  "start_capital":         100,
  "max_drawdown":          30,
  "live_trading_settings": {
    "max_open_positions": 5,
    "active_strategies":  []
  }
}
```

| Parameter | Beschreibung |
|-----------|-------------|
| `symbol` | Bitget Futures Symbol |
| `primary_timeframe` | Basis-Timeframe für Forensik |
| `drill_down_timeframes` | Kürzere TFs für MTF Drill-Down |
| `min_move_pct` | Auto-kalibriert (300 Events/Jahr Ziel) |
| `atr_multiplier` | Start-Schwellwert für Impulse-Erkennung |
| `optimizer_trials` | Anzahl Optuna-Trials |
| `start_capital` | Startkapital USDT für Backtest |
| `max_drawdown` | Max. erlaubter Drawdown % |
| `live_trading_settings.max_open_positions` | Max. gleichzeitige Trades |
| `live_trading_settings.active_strategies` | Von show_results Mode 3 befüllt |

### `secret.json`

```json
{
  "telegram": {
    "bot_token": "1234567890:AABBcc…",
    "chat_id":   "-1001234567890"
  },
  "probebot": {
    "api_key":    "bg_…",
    "api_secret": "…",
    "passphrase": "…"
  }
}
```

> `secret.json` wird NICHT von Git getrackt. Bitget API braucht READ + TRADE Permissions.

---

## Empfohlene Symbole

**Timeframe: 1h.** Getestet wurden BTC auf 1d/1h/4h und ETH auf 1h/4h — auf 1d fehlen bei den
meisten Coins genug Kerzen für eine statistisch belastbare 70/30-Validierung (siehe 1.4), auf 4h
war bislang bei keinem Symbol ein Edge validierbar. 1h liefert genug Kerzen/Jahr (~8.800) für
robuste OOS-Stats und war in beiden bisherigen Erfolgsfällen (ETH, SOL) der validierende Timeframe.

**Auswahlkriterium:** ≥4 Jahre 1h-Historie auf Bitget Futures (direkt gegen die API verifiziert,
Stand 2026-07-02) — echte Coins, keine der neuerdings gelisteten tokenisierten Aktien/Rohstoffe
(SPCX, MSTR, XAU, INTC, NATGAS, …), die ebenfalls unter `.../USDT:USDT` laufen und sich sonst
unbemerkt einschleichen.

| # | Symbol | Kategorie | 1h-Daten ab |
|---|--------|-----------|-------------|
| 1 | BTC | Major | 2021 |
| 2 | ETH | Major | 2021 |
| 3 | XRP | Major | 2021 |
| 4 | BNB | Exchange-Coin | 2021 |
| 5 | ADA | L1 | 2021 |
| 6 | DOT | L1 | 2021 |
| 7 | LTC | Payment/Legacy | 2021 |
| 8 | BCH | Payment/Legacy | 2021 |
| 9 | ATOM | L1 (Cosmos) | 2021 |
| 10 | ETC | Legacy | 2021 |
| 11 | TRX | L1 | 2021 |
| 12 | FIL | Storage | 2021 |
| 13 | LINK | Oracle/DeFi | 2021 |
| 14 | UNI | DeFi | 2021 |
| 15 | SOL | L1 | 2022 |
| 16 | DOGE | Meme | 2022 |
| 17 | AVAX | L1 | 2022 |
| 18 | NEAR | L1 | 2022 |
| 19 | XLM | Payment | 2022 |
| 20 | ICP | L1 | 2022 |

**Bereits getestet (Stand 2026-07-02):**

| Symbol/TF | Ergebnis |
|-----------|----------|
| BTC 1d/1h/4h | Kein validierter Edge |
| ETH 4h | Kein validierter Edge |
| **ETH 1h** | ✅ `IMPULSE_DOWN` validiert — OOS: 79% WR, PF 14.1, DD 6.2% |
| **SOL 1h** | ✅ `IMPULSE_DOWN` validiert — OOS: 76% WR, PF 11.0, DD 3.5% |

`IMPULSE_DOWN` validiert bislang nur bei ETH/SOL, nicht bei BTC — plausibel (BTC ist der
"ruhigste" Coin, Alts neigen zu schärferen Abwärts-Impulsen/Liquidationskaskaden). Reproduziert
sich über zwei unabhängige Symbole, spricht also eher für ein echtes Muster als für Zufall.

**Zweite Welle (später, sobald mehr Historie vorliegt):** jüngere, hochvolatile Alts mit aktuell
nur 1–2,5 Jahren 1h-Historie — zu wenig für eine erste robuste OOS-Validierung, aber vielversprechend:
SUI (2023), ARB (2023), INJ (2023), OP (2023), RUNE (2023), APT (2023), TIA (2024), SEI (2024), WLD (2024).

---

## Live-Betrieb & Cron

### Cron-Einrichtung (VPS)

```bash
crontab -e
```

Für 1h-Strategien (2 Minuten nach der vollen Stunde — Kerze sicher geschlossen):
```
2 * * * * cd /home/user/probebot && .venv/bin/python3 master_runner.py >> logs/master_runner.log 2>&1
```

Für 4h-Strategien:
```
5 */4 * * * cd /home/user/probebot && .venv/bin/python3 master_runner.py >> logs/master_runner.log 2>&1
```

Für 1d-Strategien (00:05 UTC):
```
5 0 * * * cd /home/user/probebot && .venv/bin/python3 master_runner.py >> logs/master_runner.log 2>&1
```

### Manuell ausführen

```bash
# Master Runner (alle aktiven Strategien)
bash run_live_bot.sh

# Einzelne Strategie
PYTHONPATH=src python -m probebot.strategy.run \
    --symbol BTC/USDT:USDT --timeframe 1h --mode signal

# Nur Position prüfen
PYTHONPATH=src python -m probebot.strategy.run \
    --symbol BTC/USDT:USDT --timeframe 1h --mode check
```

### Telegram-Nachrichten (Live Trading)

**Signal:**
```
🚀 probebot SIGNAL: BTC/USDT:USDT (1h)
────────────────────────────────
🟢 LONG | BREAKOUT_UP
🎯 Strategie: BREAKOUT
📊 Score: 89.4 (5/7 Bed., 71%)
💰 Entry:   $67500.00
🛑 SL:      $66800.00 (-1.04%)  [swing_low=66850.00]
🎯 TP:      Trailing 0.8% ab $68700.00  [trailing_stop 0.8% after 1.5xSL]
📐 R:R:     1:1.8
⚙️ Hebel:   10x
🛡️ Risiko:  1.0% (1.00 USDT)
📦 Kontr.:  0.0150
```

**Abschluss:**
```
✅ probebot GESCHLOSSEN (TP)
────────────────────────────────
🟢 LONG | BTC/USDT:USDT (1h)
🎯 Strategie: BREAKOUT | BREAKOUT_UP
💰 Entry:  $67500.0
🛑 SL:     $66800.0  [swing_low=66850.00]
🎯 TP:     Trailing 0.8%  [trailing_stop 0.8% after 1.5xSL]
```

---

## Dateien & Artefakte

### Nicht von Git getrackt

| Datei | Inhalt |
|-------|--------|
| `secret.json` | Bitget API-Keys + Telegram |
| `artifacts/db/forensics.db` | SQLite Datenbank mit Bewegungen + Korrelationen |
| `artifacts/db/optuna_probebot.db` | Optuna Trials (persistent) |
| `artifacts/db/bot_spec_*.json` | Forensik-Ergebnisse pro Symbol+TF |
| `artifacts/db/config_*.json` | Optimizer-Configs für Live Trading |
| `artifacts/data/*.parquet` | Feature-DataFrame Cache |
| `artifacts/tracker/*.json` | Offene Positionen + Order-IDs |
| `logs/*.log` | Laufzeitlogs |

### Von Git getrackt

| Datei | Inhalt |
|-------|--------|
| `settings.json` | Konfiguration |
| `requirements.txt` | Python Dependencies |
| `src/probebot/**/*.py` | Gesamter Quellcode |
| `*.sh` | Shell-Scripts |

---

## Schnellstart-Checkliste

```
[ ] 1. git clone + bash install.sh
[ ] 2. secret.json mit Bitget API-Keys (READ + TRADE)
[ ] 3. Telegram Bot erstellen (BotFather) + Chat-ID ermitteln
[ ] 4. settings.json: symbol + timeframe anpassen
[ ] 5. bash run_pipeline.sh  ->  Forensik Phase 1
[ ] 6. bash run_pipeline.sh  ->  Optimizer Phase 2 (direkt angeboten)
[ ] 7. bash show_results.sh  ->  Mode 1  (OOS Ergebnisse prüfen)
[ ] 8. bash show_results.sh  ->  Mode 3  (Portfolio -> settings.json)
[ ] 9. Cron: 2 * * * * python3 master_runner.py
```

---

## Technische Abhängigkeiten

```
ccxt>=4.0.0          Bitget Futures API
pandas>=2.0.0        DataFrames
numpy>=1.24.0        Numerik
ta>=0.11.0           Technische Indikatoren
scipy>=1.10.0        t-Tests, Statistik
scikit-learn>=1.3.0  Clustering (KMeans)
optuna>=3.0.0        Hyperparameter-Optimierung
pyarrow>=12.0.0      Parquet I/O
matplotlib>=3.7.0    Charts
requests>=2.28.0     Telegram HTTP API
rich>=13.0.0         Terminal Formatierung
```
