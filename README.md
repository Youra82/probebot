# 🔬 Probebot — Market Forensics Engine

Ein forensischer Marktanalyse-Bot, der historische Preisbewegungen kausal untersucht.  
Er lernt, **warum** Bewegungen entstanden sind — und findet wiederkehrende Vorbedingungen.

---

## Was Probebot kann

### 1. Bewegungserkennung (12 Typen)
| Typ | Beschreibung |
|-----|-------------|
| `BREAKDOWN` | Schlusskurs bricht unter N-Bar-Konsolidierung |
| `BREAKOUT_UP` | Schlusskurs bricht über N-Bar-Konsolidierung |
| `IMPULSE_DOWN/UP` | Einzelne Kerze > 2× ATR |
| `REVERSAL_DOWN/UP` | Trendwende nach N aufeinanderfolgenden Kerzen |
| `SQUEEZE_RELEASE_DOWN/UP` | Volatilitätskompression → plötzliche Ausdehnung |
| `ACCELERATION_DOWN/UP` | Momentum-Surge im laufenden Trend |
| `GAP_DOWN/UP` | Lücke zwischen Close und nächstem Open |

### 2. Feature-Engine (177 Indikatoren pro Kerze)

**Technische Analyse:**
RSI (7/14/21), MACD+Histogramm, Bollinger Bands, Keltner Channels, ATR (7/14/21), ADX+DI,
Supertrend, CCI, Williams %R, Stochastic RSI, MFI, Donchian, Ichimoku, EMA/SMA (5 Perioden),
WMA, HMA, Log-Returns, Candle-Body-Features, Engulfing, Squeeze-Indikator

**Physik & Komplexitäts-Indikatoren:**
- **Shannon Entropy** (10/20/40-Bar) — Marktunordnung messen
- **Hurst-Exponent** (30/60/100-Bar) — Trending vs. Mean-Reverting Regime
- **Higuchi Fractal Dimension** — Komplexität der Preisreihe
- **DFA Alpha** (Detrended Fluctuation Analysis) — Langzeitkorrelation
- **Kalman-Velocity** — Geglättete Geschwindigkeitsschätzung
- **Varianz-Ratio-Test** (Lo & MacKinlay) — Mean-Reversion-Detektor
- **WPI** (Wick Pressure Imbalance) — Kauf- vs. Verkaufsdruck aus Dochten
- **Memory Pressure** — Akkumulierter WPI mit exponentiellem Zerfall
- **CCT** (Candle Compression Tension) — Aufgestaute Energie
- **FFT Dominanzperiode** — Dominante Marktzyklen
- **Hilbert-Phase** — Instantane Phasenlage (cos/sin)
- **Lyapunov-Exponent** — Chaosmaß
- **Autocorrelation** (Lag 1/5/10)

**Market Structure:**
Swing Highs/Lows, Fair Value Gaps (FVG), Order Blocks, HH/HL/LH/LL-Struktur,
N-Bar-Breakouts, Gap-Detektion, VWAP, Inside/Outside/Pin Bars, Engulfing-Kerzen, Range-Position

**Volume-Analyse:**
OBV, CVD (Cumulative Volume Delta), Volume Profile POC (20/50-Bar),
Buy/Sell Pressure Ratio, Institutional Candle Detektor, Volume Entropy, MFI-Divergenz

**Composite Scores:**
DNA-Code (dnabot-Style), Regime Consensus (TREND/RANGE/CHAOS),
Trend Score (-10/+10), Momentum Score (-10/+10), Move Readiness (0-10)

### 3. Statistische Analyse
- **Welch's t-Test** — Welche Features sind vor Bewegungen signifikant erhöht/erniedrigt?
- **Cohen's d** — Effektstärke der Prädiktoren
- **Lift-Faktor** — Wie viel prädiktiver ist ein Feature vs. Zufall?
- **Hit-Rate** — In wie viel % der Events war die Bedingung erfüllt?

### 4. Pattern Clustering
K-Means Clustering gruppiert ähnliche Bewegungen nach ihrem Vorbedingungs-Fingerabdruck.  
→ Findet Ereignisklassen mit identischer Ursache an verschiedenen Zeitpunkten

### 5. Multi-Timeframe Drill-Down
Beginnt bei 1D → zoomt automatisch in 4H → 1H → 15m → 5m → 1m  
Berechnet pro Timeframe:
- Entry-Confidence-Score (0–10)
- Erkannte Vorbedingungen (Entropy, Hurst, RSI, Struktur, Volume)
- Optimales Entry-Timing
- Stoppt bei Confidence ≥ 8 (kein sinnloser weiterer Zoom)

### 6. Live-Scanner — Aktuelle Bewegung erklären

`bash run_live.sh` prüft ob der Markt **gerade jetzt** eine starke Bewegung macht und liefert:

1. **Alarm-Header** — Bewegungstyp, Magnitude, aktueller Regime/RSI/Entropy/Hurst
2. **Warum ist das passiert?** — Priorisierte Ursachen-Liste (Entropy-Anstieg, Hurst-Regime, RSI-Divergenz, EMA-Bruch, Volumen-Spike, CVD-Divergenz, Squeeze-Release, Wick-Druck, DB-bekannte Prädiktoren…)
3. **Historischer Vergleich** — Ähnlichste Events aus der forensics.db (Cosine-Similarity) + Prognose (Hit-Rate, medianer weiterer Move)
4. **MTF Drill-Down** — Wo ist das beste Entry-Signal gerade? (Entry-Confidence 0–10 pro Timeframe)

Kann auch als **Cron-Job** laufen (z.B. alle 15 Minuten):
```bash
*/15 * * * * cd /pfad/zu/probebot && bash run_live.sh >> logs/live_cron.log 2>&1
```

### 7. Grafische Auswertung (Telegram)

| Chart | Inhalt |
|-------|--------|
| **Overview** | Kerzen + Bewegungsmarker + Entropy + Hurst Subplot |
| **Correlation** | Horizontale Balken: t-Statistik pro Feature (grün=erhöht, rot=erniedrigt) |
| **Fingerprint** | Radar-Chart: Vorbedingungs-Fingerabdruck pro Bewegungstyp |
| **Cluster** | Cluster-Vergleich: dieser Cluster vs. alle anderen |
| **Drill-Down** | MTF Entry-Confidence + Vorbedingungen + Signale pro Timeframe |

Alle Charts + JSON-Report werden automatisch per Telegram geschickt.

---

## Schnellstart

```bash
# 1. Repo klonen
git clone https://github.com/Youra82/probebot.git
cd probebot

# 2. Installation (erstellt .venv, installiert alle Pakete)
bash install.sh

# 3. secret.json anlegen (Telegram + Exchange API)
cp secret.json.example secret.json
nano secret.json          # Tokens eintragen

# 4. Historische Forensik-Analyse starten (einmalig zum Lernen)
bash run_pipeline.sh      # fragt interaktiv nach Symbol, Zeitraum etc.

# 5. Live-Scanner — erklärt was JETZT gerade am Markt passiert
bash run_live.sh

# 6. Ergebnisse & Status anschauen
bash show_status.sh       # DB-Statistiken, Top-Prädiktoren, letzte Logs
bash show_results.sh      # Prädiktoren, Bewegungen, Cluster, Reports
```

> **Hinweis:** `run_live.sh` vergleicht aktuelle Bewegungen mit der historischen Datenbank.  
> Für beste Ergebnisse erst `run_pipeline.sh` ausführen, damit `forensics.db` gefüllt ist.

**Update (nach Code-Änderungen):**
```bash
bash update.sh
```
`update.sh` sichert `secret.json`, zieht den aktuellen Stand von GitHub (`git reset --hard origin/main`), stellt `secret.json` wieder her und aktualisiert die Pakete.

---

## Installation

```bash
git clone https://github.com/Youra82/probebot.git
cd probebot
bash install.sh
```

`install.sh` erstellt automatisch ein `.venv`, installiert alle Abhängigkeiten und legt die Verzeichnisse an.

**Abhängigkeiten:**
```
ccxt, pandas, numpy, ta, scipy, scikit-learn, rich, matplotlib
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
  "end_date": "2025-01-01",
  "min_move_pct": 2.5,
  "lookback_candles": 10,
  "atr_multiplier": 1.5,
  "report_top_n": 5,
  "drill_down": true,
  "scan_candles": 5
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

## Verwendung

### Forensik-Analyse (historisch)
```bash
# Interaktiv — fragt nach Symbol, Zeitraum etc.
bash run_pipeline.sh

# Oder direkt
bash run_pipeline.sh --symbol "BTC/USDT:USDT" --timeframe 1d \
    --start_date 2022-01-01 --end_date 2025-01-01
```

### Live-Scanner (aktuelle Bewegung erklären)
```bash
# Prüft ob der Markt GERADE eine starke Bewegung macht und erklärt WARUM
bash run_live.sh

# Mit anderen Parametern
bash run_live.sh --timeframe 1h --min_move 1.5 --candles 3

# Ohne Telegram (nur Terminal)
bash run_live.sh --no_telegram
```

### Weitere Scripts
```bash
bash show_status.sh      # DB-Statistiken, Top-Prädiktoren, letzte Logs
bash show_results.sh     # Interaktiv: Prädiktoren, Bewegungen, Cluster, Reports
bash update.sh           # Git pull + Abhängigkeiten updaten
```

### Direkt per Python (advanced)
```bash
export PYTHONPATH=./src

# Bestimmte Bewegungstypen
python -m probebot.run --movement_types BREAKDOWN,IMPULSE_DOWN

# Spezifisches Datum tief untersuchen
python -m probebot.run --investigate_date 2024-03-14 --drill_down

# Ohne Telegram
python -m probebot.run --no_telegram

# Live-Modus
python -m probebot.run --mode live --timeframe 1h --min_move_pct 1.5
```

---

## Projektstruktur

```
probebot/
├── src/probebot/
│   ├── data/
│   │   └── loader.py           # CCXT Datenloader, multi-TF fetch_window_around
│   ├── features/
│   │   ├── technical.py        # ~25 TA-Indikatoren (pure numpy/pandas)
│   │   ├── physics.py          # ~15 Physik/Entropie-Features
│   │   ├── structure.py        # Market Structure (FVG, OB, Swings)
│   │   ├── volume.py           # Volume-Analyse (CVD, POC, Pressure)
│   │   └── engine.py           # Kombiniert alle → 177 Features
│   ├── detection/
│   │   └── detector.py         # 12 Bewegungstypen
│   ├── forensics/
│   │   ├── database.py         # SQLite (artifacts/db/forensics.db)
│   │   ├── miner.py            # Pattern-Mining + Cosine-Similarity
│   │   ├── correlator.py       # Welch t-Test + K-Means Clustering
│   │   └── drill_down.py       # MTF Zoom + Entry-Scoring
│   ├── live/
│   │   ├── scanner.py          # LiveScanner: erkennt + erklärt aktuelle Moves
│   │   └── alerter.py          # Telegram-Formatting für Live-Alerts
│   ├── report/
│   │   ├── generator.py        # Rich Terminal-Output
│   │   └── charts.py           # Matplotlib Charts (5 Typen)
│   ├── utils/
│   │   └── telegram.py         # Telegram: Text, Photo, Document
│   └── run.py                  # Haupteinstiegspunkt (--mode full|live)
├── artifacts/
│   ├── db/                     # forensics.db + JSON-Reports
│   └── charts/                 # generierte PNGs
├── logs/                       # pipeline_*.log + live_*.log
├── settings.json
├── requirements.txt
├── install.sh                  # Installation (venv + pip)
├── update.sh                   # Git pull + Update
├── run_pipeline.sh             # Forensik-Analyse interaktiv
├── run_live.sh                 # Live-Scanner (manuell oder cron)
├── show_status.sh              # DB-Statistiken + Top-Prädiktoren
└── show_results.sh             # Interaktive Ergebnis-Anzeige
```

---

## Ablauf einer Analyse

```
[1] Daten laden        — CCXT Bitget/Binance, OHLCV historisch
[2] Features berechnen — 177 Spalten pro Kerze
[3] Bewegungen finden  — 12 Typen, gefiltert nach min_move_pct
[4] Pattern minen      — SQLite-Speicherung, Cosine-Similarity ähnlicher Events
    Korrelation        — Welch t-Test: welche Features gehen Moves voraus?
    Clustering         — K-Means: Gruppen mit gleichem Fingerabdruck
[5] MTF Drill-Down     — 1D → 4H → 1H → 15m → 5m → 1m
    Entry-Scoring      — 0–10 pro Timeframe, stoppt bei ≥ 8
[6] Charts generieren  — 5 PNGs (Overview, Correlation, Fingerprint, Cluster, Drill-Down)
    Telegram senden    — Charts + JSON-Report + Zusammenfassung
```

---

## Telegram Output (Beispiel)

```
🔬 PROBEBOT gestartet
Symbol: BTC/USDT:USDT  TF: 1d
Zeitraum: 2022-01-01 → 2025-01-01

🔴 BREAKDOWN  ▼ -8.3%
Zeitpunkt: 2024-03-14 00:00  |  3.2× ATR
Regime: CHAOS  RSI: 71.2  ADX: 18.4  Entropy: 0.89  Hurst: 0.38

⏱ Bestes Entry-Signal: 1h  Confidence: 8/10
Entry-Zeitpunkt: 2024-03-14 15:30

🔍 Vorbedingungen:
  • Entropy steigt (Chaos baut sich auf): 0.891
  • Hurst < 0.45 (Mean-Reverting Regime): H=0.382
  • Volume sinkt stetig (-34%)
  • Keltner Squeeze aktiv für 4 Kerzen

🔗 Ähnliche Ereignisse (3):
  2022-11-08  BREAKDOWN  -9.1%  Ähnlichkeit: 87%
  2023-08-17  BREAKDOWN  -7.6%  Ähnlichkeit: 82%

📊 Stärkste Prädiktoren:
  • entropy_20 ↑ erhöht vor BREAKDOWN  (t=+4.21)
  • hurst_60 ↓ erniedrigt vor BREAKDOWN  (t=-3.87)
  • volume_ratio ↓ erniedrigt vor BREAKDOWN  (t=-2.94)
```

---

## Architektur-Inspiration

Probebot destilliert die besten Konzepte aus:
- **dnabot** — SQLite Pattern-Datenbank, Self-Learning
- **mbot** — MDEF Entropy/Velocity/Acceleration Framework
- **dbot / oraclebot** — Hurst, Higuchi, Kalman, WPI, CCT Feature-Engineering
- **zerobot** — EAR Entropy, Adaptive Brick Logic
- **apexbot** — RADAR (Hurst+Entropy Scoring), Liquidity Zones

---

## Lizenz

Privat — alle Rechte vorbehalten.
