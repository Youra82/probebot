"""
Report generator: produces human-readable forensic analysis reports.
Uses rich for terminal rendering.
"""
import json
import numpy as np
from datetime import datetime
from typing import List, Optional, Dict

try:
    from rich.console import Console
    from rich.table import Table
    from rich.panel import Panel
    from rich.text import Text
    from rich import box
    HAS_RICH = True
except ImportError:
    HAS_RICH = False

from ..detection.detector import Movement
from ..forensics.database import ForensicsDB


console = Console() if HAS_RICH else None


def print_split_box(symbol: str, timeframe: str, start: str, split_date: str, end: str,
                     train_n: int, test_n: int):
    """Zeigt den tatsaechlichen 70/30-Split mit echten Daten fuer diesen Lauf —
    immer sichtbar (auch im --quiet Modus), da User dies unabhaengig von der
    sonstigen Verbose-Ausgabe sehen moechten."""
    if HAS_RICH:
        console.print(Panel(
            f"[bold cyan]70 / 30 SPLIT — {symbol} {timeframe}[/]\n\n"
            f"[white]{start}[/] [green]──70% TRAINING──[/] [white]{split_date}[/] [red]──30% OOS──[/] [white]{end}[/]\n\n"
            f"[green]Training:[/] {train_n} Bewegungen (Lernen NUR hier)\n"
            f"[red]OOS:[/]      {test_n} Bewegungen (nie gesehen — ehrliche Prüfung)",
            border_style="cyan", expand=False,
        ))
    else:
        print(f"\n70/30 Split — {symbol} {timeframe}")
        print(f"  TRAINING [{start} → {split_date}]: {train_n} Bewegungen")
        print(f"  OOS      [{split_date} → {end}]:   {test_n} Bewegungen  ← nie gesehen")


def print_header(symbol: str, timeframe: str, start: str, end: str, n_movements: int):
    msg = (
        f"\n{'='*70}\n"
        f"  PROBEBOT — MARKET FORENSICS REPORT\n"
        f"  Symbol: {symbol} | TF: {timeframe} | "
        f"Period: {start} → {end}\n"
        f"  Detected: {n_movements} significant movements\n"
        f"{'='*70}\n"
    )
    if HAS_RICH:
        console.print(Panel(
            f"[bold cyan]PROBEBOT — MARKET FORENSICS[/]\n"
            f"[white]Symbol:[/] {symbol}  [white]TF:[/] {timeframe}  "
            f"[white]Period:[/] {start} → {end}\n"
            f"[bold yellow]{n_movements} significant movements detected[/]",
            border_style="cyan", expand=False
        ))
    else:
        print(msg)


def print_movement_summary(movements: List[Movement]):
    from collections import Counter
    type_counts = Counter(m.move_type for m in movements)
    dir_counts = Counter(m.direction for m in movements)

    if HAS_RICH:
        t = Table(title="Movement Type Distribution", box=box.SIMPLE)
        t.add_column("Type", style="yellow")
        t.add_column("Count", style="white", justify="right")
        t.add_column("Direction", style="cyan")
        for mtype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            up = sum(1 for m in movements if m.move_type == mtype and m.direction == 'UP')
            dn = cnt - up
            t.add_row(mtype, str(cnt), f"▲{up} ▼{dn}")
        console.print(t)
    else:
        print("\nMovement Type Distribution:")
        for mtype, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            print(f"  {mtype}: {cnt}")


def print_correlations(
    correlations: Dict[str, List[dict]],
    top_n: int = 15,
):
    """Print the most predictive features per movement type."""
    for move_type, ranked_or_dict in correlations.items():
        # Support both old (list) and new (dict with 'rows') format
        ranked = ranked_or_dict.get('rows', ranked_or_dict) if isinstance(ranked_or_dict, dict) else ranked_or_dict
        if not ranked:
            continue
        top = [r for r in ranked if abs(r['t_statistic']) >= 2.0][:top_n]
        if not top:
            continue

        if HAS_RICH:
            title = f"[bold]{move_type}[/] — Top Predictive Features"
            t = Table(title=title, box=box.SIMPLE_HEAD)
            t.add_column("Feature", style="cyan", no_wrap=True)
            t.add_column("T-Stat", justify="right", style="yellow")
            t.add_column("Avg Before", justify="right")
            t.add_column("Avg All", justify="right")
            t.add_column("Lift", justify="right")
            t.add_column("Hit%", justify="right")
            t.add_column("Interpretation", style="white")

            for r in top:
                t_stat = r['t_statistic']
                color = "green" if t_stat > 0 else "red"
                interp = _interpret_feature(r)
                t.add_row(
                    r['feature'],
                    f"[{color}]{t_stat:+.2f}[/]",
                    f"{r['mean_before']:.4f}",
                    f"{r['mean_all']:.4f}",
                    f"{r['lift_factor']:+.2f}×",
                    f"{r['predictive_pct']:.0f}%",
                    interp,
                )
            console.print(t)
        else:
            print(f"\n=== {move_type} — Top Predictive Features ===")
            for r in top:
                print(f"  {r['feature']:40s}  t={r['t_statistic']:+.2f}  "
                      f"before={r['mean_before']:.4f}  all={r['mean_all']:.4f}  "
                      f"hit={r['predictive_pct']:.0f}%")


def print_movement_detail(
    movement: Movement,
    drill_down: Optional[dict],
    similar: Optional[List[dict]] = None,
):
    """Full detail report for a single movement."""
    ts = str(movement.timestamp)[:16]
    dir_sym = "▲ UP" if movement.direction == 'UP' else "▼ DOWN"
    mag = movement.magnitude_pct

    if HAS_RICH:
        header = (
            f"[bold]{movement.move_type}[/]  {dir_sym}  "
            f"[{'green' if mag > 0 else 'red'}]{mag:+.2f}%[/]  "
            f"({movement.atr_multiple:.1f}×ATR)  "
            f"[dim]{ts}[/]"
        )
        console.print(Panel(header, border_style="yellow", expand=False))

        # Context
        ctx = movement.context
        ctx_lines = []
        for k, v in ctx.items():
            if isinstance(v, float):
                ctx_lines.append(f"  {k}: {v:.3f}")
            elif v is not None:
                ctx_lines.append(f"  {k}: {v}")
        if ctx_lines:
            console.print("[bold]Context at movement candle:[/]")
            console.print("\n".join(ctx_lines))
    else:
        print(f"\n{'─'*60}")
        print(f"  {movement.move_type} | {dir_sym} | {mag:+.2f}% | {movement.atr_multiple:.1f}×ATR | {ts}")
        ctx = movement.context
        for k, v in ctx.items():
            if isinstance(v, float):
                print(f"    {k}: {v:.3f}")

    if drill_down:
        _print_drill_down(drill_down)

    if similar:
        _print_similar(similar)


def print_clusters(clusters: dict):
    if not clusters:
        return

    if HAS_RICH:
        console.print("\n[bold cyan]Pattern Clusters[/]")
        for cid, cluster in clusters.items():
            panel_text = (
                f"[bold]Cluster {cid}[/]  |  {cluster['n']} events  "
                f"|  Dir: {cluster['dominant_direction']}  "
                f"|  Avg move: {cluster['avg_magnitude_pct']:+.2f}%\n"
                f"Types: {', '.join(cluster['movement_types'])}\n\n"
                f"[yellow]Key fingerprint:[/]\n"
            )
            for feat in cluster['key_features'][:6]:
                direction_sym = "▲" if feat['t_stat'] > 0 else "▼"
                panel_text += (
                    f"  {direction_sym} {feat['feature']}: "
                    f"cluster={feat['cluster_mean']:.3f} vs others={feat.get('other_mean', feat.get('global_mean', 0)):.3f}  "
                    f"(t={feat['t_stat']:+.2f})\n"
                )
            console.print(Panel(panel_text, border_style="magenta"))
    else:
        print("\n=== Pattern Clusters ===")
        for cid, cluster in clusters.items():
            print(f"\nCluster {cid}: {cluster['n']} events | {cluster['dominant_direction']} | "
                  f"avg {cluster['avg_magnitude_pct']:+.2f}%")
            for feat in cluster['key_features'][:6]:
                print(f"  {feat['feature']}: {feat['cluster_mean']:.3f} vs {feat.get('other_mean', feat.get('global_mean', 0)):.3f}")


def save_report_json(
    path: str,
    symbol: str,
    timeframe: str,
    start: str,
    end: str,
    movements: List[Movement],
    correlations: dict,
    clusters: dict,
    drill_down_results: dict,
):
    data = {
        'generated_at': datetime.now().isoformat(),
        'symbol': symbol,
        'timeframe': timeframe,
        'period': {'start': start, 'end': end},
        'n_movements': len(movements),
        'movements': [
            {
                'timestamp': str(m.timestamp),
                'type': m.move_type,
                'direction': m.direction,
                'magnitude_pct': m.magnitude_pct,
                'atr_multiple': m.atr_multiple,
                'context': m.context,
            }
            for m in movements
        ],
        'correlations': {
            mtype: [
                {k: v for k, v in r.items() if k not in ('symbol', 'timeframe')}
                for r in ranked[:20]
            ]
            for mtype, ranked in correlations.items()
        },
        'clusters': clusters,
        'drill_down': drill_down_results,
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, default=str)
    print(f"\n  Report saved to: {path}")


# ─── Private helpers ──────────────────────────────────────────────────────────

def _print_drill_down(drill_down: dict):
    if HAS_RICH:
        console.print("\n[bold cyan]Multi-Timeframe Drill-Down[/]")
    else:
        print("\n  --- Multi-Timeframe Drill-Down ---")

    for tf, level in drill_down.items():
        if isinstance(level, dict) and 'error' not in level:
            if HAS_RICH:
                score = level.get('entry_confidence', 0)
                score_color = "green" if score >= 6 else "yellow" if score >= 3 else "red"
                signals = level.get('entry_signals', [])[:5]
                precursors = level.get('precursors', [])[:5]
                entry_ts = level.get('entry_ts', 'N/A')

                lines = [
                    f"[bold]{tf}[/]  Confidence: [{score_color}]{score}/10[/]",
                    f"  Entry: {entry_ts}  |  Regime: {level.get('regime')}",
                    f"  RSI: {level.get('rsi_14')}  ADX: {level.get('adx')}  "
                    f"Entropy: {level.get('entropy_20')}  Hurst: {level.get('hurst_60')}",
                ]
                if precursors:
                    lines.append(f"\n  [yellow]Precursors:[/]")
                    for p in precursors:
                        lines.append(f"    • {p}")
                if signals:
                    lines.append(f"\n  [green]Entry signals:[/]")
                    for s in signals[:5]:
                        lines.append(f"    ✓ {s}")

                console.print(Panel("\n".join(lines), border_style="blue", expand=False))
            else:
                score = level.get('entry_confidence', 0)
                print(f"\n  [{tf}] Confidence: {score}/10  Entry: {level.get('entry_ts')}")
                for p in level.get('precursors', []):
                    print(f"    ◦ {p}")
                for s in level.get('entry_signals', [])[:5]:
                    print(f"    ✓ {s}")
        elif isinstance(level, dict) and 'error' in level:
            if HAS_RICH:
                console.print(f"  [dim]{tf}: {level['error']}[/]")
            else:
                print(f"  {tf}: error — {level['error']}")


def _print_similar(similar: List[dict]):
    if not similar:
        return
    if HAS_RICH:
        console.print(f"\n[bold]Similar Historical Events (by pre-condition fingerprint):[/]")
        for i, s in enumerate(similar):
            sim_score = s.get('similarity_score', 0)
            color = "green" if sim_score > 0.8 else "yellow"
            console.print(
                f"  {i+1}. [{color}]{s.get('timestamp', '')[:16]}[/]  "
                f"{s.get('move_type')}  {s.get('magnitude_pct', 0):+.2f}%  "
                f"similarity=[{color}]{sim_score:.2f}[/]"
            )
    else:
        print("\n  Similar historical events:")
        for s in similar:
            print(f"    {s.get('timestamp', '')[:16]}  "
                  f"{s.get('move_type')}  {s.get('magnitude_pct', 0):+.2f}%  "
                  f"similarity={s.get('similarity_score', 0):.2f}")


def _interpret_feature(r: dict) -> str:
    feat = r['feature']
    t = r['t_statistic']
    mean_b = r['mean_before']
    mean_a = r['mean_all']

    # Known interpretations
    interpretations = {
        'entropy_20': (
            f"Entropy {'HIGH' if t > 0 else 'LOW'} ({mean_b:.3f} vs avg {mean_a:.3f}) — "
            f"{'Chaos building' if t > 0 else 'Order forming'}"
        ),
        'hurst_60': (
            f"Hurst={'LOW (mean-rev)' if mean_b < 0.45 else 'HIGH (trending)' if mean_b > 0.55 else 'MID'}"
        ),
        'rsi_14': (
            f"RSI {'HIGH' if mean_b > 55 else 'LOW' if mean_b < 45 else 'NEUTRAL'} ({mean_b:.1f})"
        ),
        'adx': f"ADX {'strong' if mean_b > 25 else 'weak'} trend ({mean_b:.1f})",
        'volume_ratio': f"Volume {'surge' if mean_b > 1.5 else 'low'} ({mean_b:.2f}×avg)",
        'ema_alignment': f"EMA stack {'bullish' if mean_b > 0 else 'bearish'} ({mean_b:.1f})",
        'atr_z': f"ATR {'expanded' if mean_b > 0 else 'compressed'} ({mean_b:+.2f}σ)",
    }

    if feat in interpretations:
        return interpretations[feat]

    direction = "elevated" if t > 0 else "suppressed"
    return f"{feat} {direction} before move"
