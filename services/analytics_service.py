"""
Analytics charts.

Builds Matplotlib figures that are embedded directly into CustomTkinter frames
via ``FigureCanvasTkAgg``.  Every chart:

*   re-themes itself for Light/Dark mode (Matplotlib has no idea what
    CustomTkinter's appearance mode is, so we set every colour explicitly);
*   draws a threshold line where a percentage is plotted, because the only
    number a college actually cares about is "is this above 75?";
*   degrades to a readable "No data" placeholder rather than an empty axis.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import matplotlib
matplotlib.use("TkAgg")            # must precede pyplot import

import matplotlib.pyplot as plt
from matplotlib.figure import Figure

from config.settings import config
from config.theme import CHART_COLORS, SEMANTIC, STATUS_COLORS, palette
from core.logger import get_logger
from models import attendance as attendance_model

logger = get_logger("services.analytics")

plt.rcParams["font.family"] = ["Segoe UI", "DejaVu Sans", "sans-serif"]


# ---------------------------------------------------------------------------
# Figure scaffolding
# ---------------------------------------------------------------------------
def _new_figure(width: float = 6.4, height: float = 3.4, mode: str | None = None):
    """Create a themed figure + axes pair."""
    colours = palette(mode)
    figure = Figure(figsize=(width, height), dpi=100)
    figure.patch.set_facecolor(colours["surface"])

    axes = figure.add_subplot(111)
    axes.set_facecolor(colours["surface"])

    for side in ("top", "right"):
        axes.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        axes.spines[side].set_color(colours["border"])

    axes.tick_params(colors=colours["text_muted"], labelsize=8)
    axes.grid(True, axis="y", alpha=0.22, color=colours["border"], linewidth=0.7)
    axes.set_axisbelow(True)
    return figure, axes


def _title(axes, text: str, mode: str | None = None) -> None:
    axes.set_title(text, fontsize=11, fontweight="bold",
                   color=palette(mode)["text"], pad=12)


def _empty(figure, axes, message: str = "No data available for this selection",
           mode: str | None = None):
    """Placeholder so a chart panel never renders as a blank box."""
    axes.clear()
    axes.text(0.5, 0.5, message, ha="center", va="center",
              fontsize=10, color=palette(mode)["text_muted"], transform=axes.transAxes)
    axes.set_xticks([])
    axes.set_yticks([])
    for spine in axes.spines.values():
        spine.set_visible(False)
    figure.tight_layout()
    return figure


def _threshold_line(axes, mode: str | None = None) -> None:
    """Horizontal marker at the exam-eligibility threshold."""
    threshold = float(config.get("attendance_threshold", 75))
    axes.axhline(threshold, color=SEMANTIC["danger"], linestyle="--",
                 linewidth=1.2, alpha=0.75, zorder=1)
    axes.text(0.995, threshold + 1.5, f"{threshold:.0f}% required",
              transform=axes.get_yaxis_transform(), ha="right", va="bottom",
              fontsize=7, color=SEMANTIC["danger"])


def _bar_colours(values: list[float]) -> list[str]:
    """Traffic-light colouring against the threshold."""
    threshold = float(config.get("attendance_threshold", 75))
    return [SEMANTIC["success"] if v >= threshold
            else SEMANTIC["warning"] if v >= threshold - 10
            else SEMANTIC["danger"] for v in values]


def _wrap(label: str, limit: int = 16) -> str:
    """Break a long category label across two lines so it stays readable."""
    text = str(label)
    if len(text) <= limit:
        return text
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 <= limit:
            current = f"{current} {word}".strip()
        else:
            lines.append(current)
            current = word
        if len(lines) == 1 and len(" ".join(words[words.index(word):])) > limit:
            continue
    lines.append(current)
    return "\n".join(lines[:2])


# ---------------------------------------------------------------------------
# Trend charts
# ---------------------------------------------------------------------------
def daily_trend_chart(days: int = 30, branch_id: int | None = None,
                      semester_id: int | None = None, subject_id: int | None = None,
                      mode: str | None = None, **figure_kwargs):
    """Attendance percentage per day over a rolling window."""
    to_date = datetime.now().strftime("%Y-%m-%d")
    from_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

    figure, axes = _new_figure(mode=mode, **figure_kwargs)
    try:
        rows = attendance_model.get_daily_series(
            from_date, to_date, branch_id=branch_id,
            semester_id=semester_id, subject_id=subject_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("daily_trend_chart query failed: %s", exc)
        return _empty(figure, axes, "Could not load trend data", mode)

    if not rows:
        _title(axes, f"Daily Attendance Trend (last {days} days)", mode)
        return _empty(figure, axes, mode=mode)

    dates = [str(r["class_date"])[5:] for r in rows]          # MM-DD
    values = [float(r["percentage"] or 0) for r in rows]

    axes.plot(dates, values, color=CHART_COLORS[0], linewidth=2.2,
              marker="o", markersize=4, zorder=3)
    axes.fill_between(range(len(dates)), values, alpha=0.14,
                      color=CHART_COLORS[0], zorder=2)
    _threshold_line(axes, mode)

    axes.set_ylim(0, 105)
    axes.set_ylabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])
    _title(axes, f"Daily Attendance Trend (last {days} days)", mode)

    # Thin the x labels so they never collide.
    step = max(1, len(dates) // 12)
    axes.set_xticks(range(0, len(dates), step))
    axes.set_xticklabels(dates[::step], rotation=45, ha="right")

    figure.tight_layout()
    return figure


def monthly_trend_chart(year: int | None = None, branch_id: int | None = None,
                        semester_id: int | None = None, mode: str | None = None,
                        **figure_kwargs):
    """Month-by-month bars for a calendar year."""
    year = year or datetime.now().year
    figure, axes = _new_figure(mode=mode, **figure_kwargs)

    try:
        rows = attendance_model.get_monthly_series(year, branch_id, semester_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("monthly_trend_chart query failed: %s", exc)
        return _empty(figure, axes, "Could not load monthly data", mode)

    if not rows:
        _title(axes, f"Monthly Attendance - {year}", mode)
        return _empty(figure, axes, mode=mode)

    labels, values = [], []
    for row in rows:
        raw = str(row["month"])
        try:
            labels.append(datetime.strptime(raw[-2:], "%m").strftime("%b"))
        except ValueError:
            labels.append(raw)
        values.append(float(row["percentage"] or 0))

    bars = axes.bar(labels, values, color=_bar_colours(values), width=0.62, zorder=3)
    _threshold_line(axes, mode)

    for bar, value in zip(bars, values):
        axes.text(bar.get_x() + bar.get_width() / 2, value + 1.5, f"{value:.0f}",
                  ha="center", va="bottom", fontsize=7.5,
                  color=palette(mode)["text_muted"])

    axes.set_ylim(0, 108)
    axes.set_ylabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])
    _title(axes, f"Monthly Attendance - {year}", mode)
    figure.tight_layout()
    return figure


def weekly_chart(weeks: int = 8, branch_id: int | None = None,
                 mode: str | None = None, **figure_kwargs):
    """Aggregate the daily series into ISO weeks."""
    to_date = datetime.now()
    from_date = to_date - timedelta(weeks=weeks)

    figure, axes = _new_figure(mode=mode, **figure_kwargs)
    try:
        rows = attendance_model.get_daily_series(
            from_date.strftime("%Y-%m-%d"), to_date.strftime("%Y-%m-%d"),
            branch_id=branch_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("weekly_chart query failed: %s", exc)
        return _empty(figure, axes, "Could not load weekly data", mode)

    if not rows:
        _title(axes, f"Weekly Attendance (last {weeks} weeks)", mode)
        return _empty(figure, axes, mode=mode)

    buckets: dict[str, list[int]] = {}
    for row in rows:
        try:
            week = datetime.strptime(str(row["class_date"]), "%Y-%m-%d")
        except ValueError:
            continue
        key = f"W{week.isocalendar()[1]}"
        attended, total = buckets.setdefault(key, [0, 0])
        buckets[key] = [attended + int(row["attended"] or 0),
                        total + int(row["total"] or 0)]

    labels = list(buckets)
    values = [round(100.0 * p / t, 1) if t else 0.0 for p, t in buckets.values()]

    bars = axes.bar(labels, values, color=_bar_colours(values), width=0.6, zorder=3)
    _threshold_line(axes, mode)
    for bar, value in zip(bars, values):
        axes.text(bar.get_x() + bar.get_width() / 2, value + 1.5, f"{value:.0f}",
                  ha="center", va="bottom", fontsize=7.5,
                  color=palette(mode)["text_muted"])

    axes.set_ylim(0, 108)
    axes.set_ylabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])
    _title(axes, f"Weekly Attendance (last {weeks} weeks)", mode)
    figure.tight_layout()
    return figure


# ---------------------------------------------------------------------------
# Comparison charts
# ---------------------------------------------------------------------------
def comparison_chart(group_by: str, from_date: str | None = None,
                     to_date: str | None = None, mode: str | None = None,
                     horizontal: bool = True, **figure_kwargs):
    """Branch / Subject / Faculty / Semester comparison.

    Rendered horizontally by default -- category names in a college are long
    ("Electronics Engineering"), and horizontal bars keep them legible.
    """
    figure, axes = _new_figure(mode=mode, **figure_kwargs)
    label = group_by.title()

    try:
        rows = attendance_model.get_comparison(group_by, from_date, to_date)
    except Exception as exc:                    # noqa: BLE001
        logger.error("comparison_chart(%s) failed: %s", group_by, exc)
        return _empty(figure, axes, f"Could not load {label.lower()} data", mode)

    if not rows:
        _title(axes, f"{label}-wise Attendance Comparison", mode)
        return _empty(figure, axes, mode=mode)

    rows = rows[:12]
    labels = [str(r["label"] or "-") for r in rows]
    values = [float(r["percentage"] or 0) for r in rows]
    colours = _bar_colours(values)

    if horizontal:
        positions = range(len(labels))
        axes.barh(positions, values, color=colours, height=0.62, zorder=3)
        axes.set_yticks(list(positions))
        axes.set_yticklabels([_wrap(l, 22) for l in labels], fontsize=8)
        axes.invert_yaxis()
        axes.set_xlim(0, 108)
        axes.set_xlabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])

        threshold = float(config.get("attendance_threshold", 75))
        axes.axvline(threshold, color=SEMANTIC["danger"], linestyle="--",
                     linewidth=1.2, alpha=0.75)

        for index, value in enumerate(values):
            axes.text(value + 1.5, index, f"{value:.1f}%", va="center",
                      fontsize=7.5, color=palette(mode)["text_muted"])

        axes.grid(True, axis="x", alpha=0.22, color=palette(mode)["border"])
        axes.grid(False, axis="y")
    else:
        bars = axes.bar([_wrap(l) for l in labels], values, color=colours,
                        width=0.6, zorder=3)
        _threshold_line(axes, mode)
        for bar, value in zip(bars, values):
            axes.text(bar.get_x() + bar.get_width() / 2, value + 1.5, f"{value:.0f}",
                      ha="center", va="bottom", fontsize=7.5,
                      color=palette(mode)["text_muted"])
        axes.set_ylim(0, 108)
        axes.tick_params(axis="x", rotation=30)

    _title(axes, f"{label}-wise Attendance Comparison", mode)
    figure.tight_layout()
    return figure


def branch_comparison_chart(**kwargs):
    return comparison_chart("branch", **kwargs)


def subject_comparison_chart(**kwargs):
    return comparison_chart("subject", **kwargs)


def faculty_comparison_chart(**kwargs):
    return comparison_chart("faculty", **kwargs)


def semester_comparison_chart(**kwargs):
    return comparison_chart("semester", **kwargs)


# ---------------------------------------------------------------------------
# Distribution charts
# ---------------------------------------------------------------------------
def status_donut_chart(from_date: str | None = None, to_date: str | None = None,
                       branch_id: int | None = None, mode: str | None = None,
                       **figure_kwargs):
    """Present / Absent / Late / Leave breakdown as a donut."""
    figure, axes = _new_figure(mode=mode, **figure_kwargs)

    try:
        distribution = attendance_model.get_status_distribution(from_date, to_date, branch_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("status_donut_chart failed: %s", exc)
        return _empty(figure, axes, "Could not load status data", mode)

    distribution = {k: v for k, v in (distribution or {}).items() if v}
    if not distribution:
        _title(axes, "Attendance Status Distribution", mode)
        return _empty(figure, axes, mode=mode)

    labels = list(distribution)
    values = [distribution[k] for k in labels]
    colours = [STATUS_COLORS.get(k, SEMANTIC["neutral"]) for k in labels]
    total = sum(values)

    wedges, _, autotexts = axes.pie(
        values, colors=colours, startangle=95, counterclock=False,
        autopct=lambda pct: f"{pct:.0f}%" if pct >= 4 else "",
        pctdistance=0.79, wedgeprops={"width": 0.42, "edgecolor": palette(mode)["surface"],
                                      "linewidth": 2})
    for text in autotexts:
        text.set_color("white")
        text.set_fontsize(8)
        text.set_fontweight("bold")

    axes.text(0, 0.06, f"{total:,}", ha="center", va="center", fontsize=17,
              fontweight="bold", color=palette(mode)["text"])
    axes.text(0, -0.16, "RECORDS", ha="center", va="center", fontsize=7.5,
              color=palette(mode)["text_muted"])

    axes.legend(wedges, [f"{l}  ({distribution[l]:,})" for l in labels],
                loc="center left", bbox_to_anchor=(1.0, 0.5), frameon=False,
                fontsize=8, labelcolor=palette(mode)["text_muted"])
    axes.set_ylabel("")
    _title(axes, "Attendance Status Distribution", mode)
    figure.tight_layout()
    return figure


def student_trend_chart(student_id: int, days: int = 30, mode: str | None = None,
                        **figure_kwargs):
    """Personal attendance trend for the student dashboard."""
    figure, axes = _new_figure(mode=mode, **figure_kwargs)

    try:
        rows = attendance_model.get_student_trend(student_id, days)
    except Exception as exc:                    # noqa: BLE001
        logger.error("student_trend_chart failed: %s", exc)
        return _empty(figure, axes, "Could not load your attendance trend", mode)

    if not rows:
        _title(axes, "My Attendance Trend", mode)
        return _empty(figure, axes, "No attendance recorded yet", mode)

    dates = [str(r["class_date"])[5:] for r in rows]
    # get_student_trend returns raw counts, not a percentage column.
    values = [round(100.0 * int(r["attended"] or 0) / int(r["total"]), 1)
              if r["total"] else 0.0 for r in rows]

    axes.plot(dates, values, color=CHART_COLORS[0], linewidth=2.2,
              marker="o", markersize=4, zorder=3)
    axes.fill_between(range(len(dates)), values, alpha=0.14, color=CHART_COLORS[0])
    _threshold_line(axes, mode)

    axes.set_ylim(0, 105)
    axes.set_ylabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])
    _title(axes, f"My Attendance Trend (last {days} days)", mode)

    step = max(1, len(dates) // 10)
    axes.set_xticks(range(0, len(dates), step))
    axes.set_xticklabels(dates[::step], rotation=45, ha="right")
    figure.tight_layout()
    return figure


def subject_wise_chart(student_id: int, mode: str | None = None, **figure_kwargs):
    """A student's percentage in each subject."""
    figure, axes = _new_figure(mode=mode, **figure_kwargs)

    try:
        rows = attendance_model.get_student_subject_summary(student_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("subject_wise_chart failed: %s", exc)
        return _empty(figure, axes, "Could not load subject data", mode)

    if not rows:
        _title(axes, "Subject-wise Attendance", mode)
        return _empty(figure, axes, "No subject attendance yet", mode)

    labels = [str(r["subject_code"] or r["subject_name"]) for r in rows]
    values = [float(r["percentage"] or 0) for r in rows]

    positions = range(len(labels))
    axes.barh(positions, values, color=_bar_colours(values), height=0.6, zorder=3)
    axes.set_yticks(list(positions))
    axes.set_yticklabels(labels, fontsize=8)
    axes.invert_yaxis()
    axes.set_xlim(0, 108)

    threshold = float(config.get("attendance_threshold", 75))
    axes.axvline(threshold, color=SEMANTIC["danger"], linestyle="--", linewidth=1.2)

    for index, (value, row) in enumerate(zip(values, rows)):
        axes.text(value + 1.5, index,
                  f"{value:.0f}%  ({row['attended']}/{row['total_classes']})",
                  va="center", fontsize=7.5, color=palette(mode)["text_muted"])

    axes.set_xlabel("Attendance %", fontsize=9, color=palette(mode)["text_muted"])
    axes.grid(True, axis="x", alpha=0.22, color=palette(mode)["border"])
    axes.grid(False, axis="y")
    _title(axes, "Subject-wise Attendance", mode)
    figure.tight_layout()
    return figure


def defaulter_distribution_chart(branch_id: int | None = None,
                                 semester_id: int | None = None,
                                 mode: str | None = None, **figure_kwargs):
    """Histogram of students across attendance bands."""
    figure, axes = _new_figure(mode=mode, **figure_kwargs)

    from services.report_service import get_class_summary_data
    try:
        rows = get_class_summary_data(branch_id=branch_id, semester_id=semester_id)
    except Exception as exc:                    # noqa: BLE001
        logger.error("defaulter_distribution_chart failed: %s", exc)
        return _empty(figure, axes, "Could not load distribution data", mode)

    rows = [r for r in rows if r["total_classes"]]
    if not rows:
        _title(axes, "Attendance Distribution", mode)
        return _empty(figure, axes, mode=mode)

    bands = ["0-40%", "40-55%", "55-65%", "65-75%", "75-85%", "85-100%"]
    edges = [0, 40, 55, 65, 75, 85, 101]
    counts = [0] * len(bands)

    for row in rows:
        percentage = float(row["percentage"] or 0)
        for index in range(len(bands)):
            if edges[index] <= percentage < edges[index + 1]:
                counts[index] += 1
                break

    colours = [SEMANTIC["danger"], SEMANTIC["danger"], SEMANTIC["warning"],
               SEMANTIC["warning"], SEMANTIC["success"], SEMANTIC["success"]]

    bars = axes.bar(bands, counts, color=colours, width=0.62, zorder=3)
    for bar, count in zip(bars, counts):
        if count:
            axes.text(bar.get_x() + bar.get_width() / 2, count + 0.15, str(count),
                      ha="center", va="bottom", fontsize=8,
                      color=palette(mode)["text_muted"])

    axes.set_ylabel("Students", fontsize=9, color=palette(mode)["text_muted"])
    axes.tick_params(axis="x", labelsize=8)
    _title(axes, f"Attendance Distribution ({len(rows)} students)", mode)
    figure.tight_layout()
    return figure


# ---------------------------------------------------------------------------
# Registry used by the Analytics screen
# ---------------------------------------------------------------------------
CHART_REGISTRY = {
    "Daily Trend":            daily_trend_chart,
    "Weekly Attendance":      weekly_chart,
    "Monthly Attendance":     monthly_trend_chart,
    "Branch Comparison":      branch_comparison_chart,
    "Subject Comparison":     subject_comparison_chart,
    "Faculty Comparison":     faculty_comparison_chart,
    "Semester Comparison":    semester_comparison_chart,
    "Status Distribution":    status_donut_chart,
    "Attendance Distribution": defaulter_distribution_chart,
}


def save_chart(figure, path, dpi: int = 150) -> bool:
    """Persist a figure to PNG -- used when embedding charts into reports."""
    try:
        figure.savefig(str(path), dpi=dpi, bbox_inches="tight",
                       facecolor=figure.get_facecolor())
        return True
    except Exception as exc:                    # noqa: BLE001
        logger.error("Could not save chart to %s: %s", path, exc)
        return False
