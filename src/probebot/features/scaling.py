"""
Timeframe-adaptive Skalierung fuer Feature-Perioden.

Alle Indikator-Perioden (RSI-14, EMA-20, Bollinger-20, Ichimoku 9/26/52, ...)
wurden urspruenglich fuer 1h-Kerzen gewaehlt. Ohne Skalierung misst z.B.
RSI-14 bei 30m nur 7 Echtstunden (zu kurz fuer die eigentliche Marktdynamik)
und bei 6h ploetzlich 3,5 Tage (zu lang, verwaschen in mehrtaegigem Rauschen)
statt der bei 1h gemeinten ~14 Stunden.

sp() skaliert jede Periode auf dieselbe reale Zeitspanne wie bei 1h (Baseline).
"""

TF_MINUTES = {
    '1m': 1, '3m': 3, '5m': 5, '15m': 15, '30m': 30,
    '1h': 60, '2h': 120, '4h': 240, '6h': 360, '12h': 720,
    '1d': 1440, '3d': 4320, '1w': 10080,
}


def timeframe_scale(timeframe: str) -> float:
    """Faktor, mit dem eine bei 1h kalibrierte Periode multipliziert wird."""
    minutes = TF_MINUTES.get(timeframe, 60)
    return 60.0 / minutes


def sp(period: int, scale: float, minimum: int = 2) -> int:
    """
    Skaliert eine einzelne, bei 1h kalibrierte Periode auf den aktuellen
    Timeframe. Mindestwert 2 (rolling(1)/ein einzelner Punkt liefert keine
    sinnvolle Statistik mehr).
    """
    return max(minimum, round(period * scale))
