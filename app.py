"""Streamlit UI for hh-ai-job-matcher.

Run from the project root:

    streamlit run app.py

The UI reads the same SQLite database used by the CLI (results/hh_scraper.sqlite3)
and lets you browse analyzed vacancies, filter by track and recommendation,
and trigger pipeline stages without dropping into the terminal.
"""
from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.express as px
import streamlit as st

import analyzer
import database


BASE_DIR = Path(__file__).resolve().parent
RESULTS_DIR = BASE_DIR / "results"

TRACK_LABELS = {
    "backend": "Backend",
    "ai_automation": "AI Automation",
    "telegram_bot": "Telegram Bot",
    "data_analytics": "Data Analytics",
    "ml": "ML",
    "qa": "QA",
    "devops": "DevOps",
    "fullstack": "FullStack",
    "mobile": "Mobile",
    "other": "Other",
}

RECOMMENDATION_LABELS = {
    "apply": "🟢 Apply",
    "maybe": "🟡 Maybe",
    "skip": "🔴 Skip",
}

RECOMMENDATION_COLORS = {
    "apply": "#10b981",
    "maybe": "#f59e0b",
    "skip": "#ef4444",
}


# ---------- Data layer ----------

@st.cache_data(ttl=10)
def load_matches() -> pd.DataFrame:
    """Load all analyzed vacancies from SQLite."""
    matches = database.get_matches(min_score=0)
    if not matches:
        return pd.DataFrame()
    df = pd.DataFrame(matches)
    df["fit_score"] = df["fit_score"].apply(lambda v: float(v) / 100 if v > 1 else float(v))
    df["my_fit_for_them"] = df["my_fit_for_them"].astype(float)
    df["their_fit_for_me"] = df["their_fit_for_me"].astype(float)
    df["track"] = df["track"].fillna("other")
    df["recommendation"] = df["recommendation"].fillna("skip")
    df["company"] = df["company"].fillna("не указана")
    df["salary"] = df["salary"].fillna("не указана")
    df["location"] = df["location"].fillna("не указана")
    return df


@st.cache_data(ttl=10)
def load_stats() -> dict[str, Any]:
    return database.database_stats()


def clear_caches() -> None:
    load_matches.clear()
    load_stats.clear()


# ---------- Pipeline runners ----------

def _run_cli(stage: str) -> tuple[int, str, str]:
    """Run main.py --only stage as a subprocess and return (returncode, stdout, stderr)."""
    process = subprocess.run(
        [sys.executable, str(BASE_DIR / "main.py"), "--only", stage],
        cwd=BASE_DIR,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return process.returncode, process.stdout, process.stderr


def run_stage_with_progress(stage: str, label: str) -> None:
    with st.status(f"Запускаю {label}…", expanded=True) as status:
        st.write(f"Стадия: `{stage}`")
        code, out, err = _run_cli(stage)
        if out:
            st.code(out[-3000:], language="text")
        if err:
            st.code(err[-1500:], language="text")
        if code == 0:
            status.update(label=f"{label} — готово", state="complete")
            clear_caches()
        else:
            status.update(label=f"{label} — ошибка (код {code})", state="error")


# ---------- Page setup ----------

st.set_page_config(
    page_title="HH AI Job Matcher",
    page_icon="🎯",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Custom CSS for cleaner look.
st.markdown(
    """
    <style>
    .main > div { padding-top: 1.5rem; }
    .vacancy-card {
        background: var(--secondary-background-color);
        padding: 1.2rem 1.4rem;
        border-radius: 12px;
        border-left: 4px solid #6366f1;
        margin-bottom: 0.8rem;
    }
    .vacancy-card.apply { border-left-color: #10b981; }
    .vacancy-card.maybe { border-left-color: #f59e0b; }
    .vacancy-card.skip { border-left-color: #ef4444; }
    .vacancy-title { font-size: 1.1rem; font-weight: 600; margin-bottom: 0.25rem; }
    .vacancy-meta { color: var(--text-color-secondary, #888); font-size: 0.9rem; }
    .score-pill {
        display: inline-block;
        padding: 0.15rem 0.6rem;
        border-radius: 999px;
        font-size: 0.85rem;
        font-weight: 600;
        margin-right: 0.4rem;
    }
    .reason-list { margin: 0.4rem 0 0 0; padding-left: 1.2rem; }
    div[data-testid="stMetricValue"] { font-size: 1.6rem; }
    </style>
    """,
    unsafe_allow_html=True,
)


# ---------- Sidebar ----------

with st.sidebar:
    st.title("🎯 HH Job Matcher")
    st.caption("AI-анализатор вакансий на hh.ru")

    st.divider()

    st.subheader("Действия")
    if st.button("📡 Scrape (собрать новые)", use_container_width=True):
        run_stage_with_progress("scrape", "сбор вакансий")
    if st.button("🧠 Analyze (необработанные)", use_container_width=True):
        run_stage_with_progress("analyze", "анализ вакансий")
    if st.button("♻️ Reanalyze (всё заново)", use_container_width=True):
        run_stage_with_progress("reanalyze", "пересчёт всех анализов")
    if st.button("📝 Сгенерировать отчёт", use_container_width=True):
        run_stage_with_progress("report", "генерация отчёта")
    with st.expander("Опасные действия"):
        if st.button("🧹 Очистить raw_html", use_container_width=True):
            run_stage_with_progress("purge-html", "очистка raw_html")

    st.divider()

    st.subheader("Фильтры")
    df = load_matches()
    if df.empty:
        st.info("База пуста. Запусти scrape и analyze.")
        all_tracks: list[str] = []
        all_recs: list[str] = []
    else:
        all_tracks = sorted(df["track"].unique().tolist())
        all_recs = sorted(df["recommendation"].unique().tolist())

    selected_recs = st.multiselect(
        "Рекомендация",
        options=all_recs,
        default=all_recs,
        format_func=lambda r: RECOMMENDATION_LABELS.get(r, r),
    )
    selected_tracks = st.multiselect(
        "Трек",
        options=all_tracks,
        default=all_tracks,
        format_func=lambda t: TRACK_LABELS.get(t, t),
    )
    min_fit = st.slider("Минимальный fit_score", 0.0, 1.0, 0.0, 0.05)
    search_text = st.text_input("Поиск по title / company / описанию", "")

    st.divider()
    st.caption(f"DB: `{database.DEFAULT_DB_PATH.relative_to(BASE_DIR)}`")


# ---------- Main area ----------

st.title("🎯 HH AI Job Matcher")

if df.empty:
    st.warning(
        "В базе пока нет проанализированных вакансий. "
        "Нажми **Scrape** в сайдбаре для сбора, потом **Analyze**."
    )
    st.stop()

stats = load_stats()


# Top metrics row
col1, col2, col3, col4, col5 = st.columns(5)
col1.metric("Всего в БД", stats["total"])
col2.metric("Проанализировано", stats["analyzed"])
col3.metric(
    "🟢 Apply", stats["per_recommendation"].get("apply", 0),
    help="Вакансии с fit_score >= 0.75",
)
col4.metric(
    "🟡 Maybe", stats["per_recommendation"].get("maybe", 0),
    help="Вакансии с 0.55 <= fit_score < 0.75",
)
col5.metric("Средний fit", f"{stats['avg_fit_score']:.2f}")


# Apply filters
filtered = df.copy()
if selected_recs:
    filtered = filtered[filtered["recommendation"].isin(selected_recs)]
if selected_tracks:
    filtered = filtered[filtered["track"].isin(selected_tracks)]
filtered = filtered[filtered["fit_score"] >= min_fit]
if search_text.strip():
    needle = search_text.lower()
    mask = (
        filtered["title"].fillna("").str.lower().str.contains(needle, na=False)
        | filtered["company"].fillna("").str.lower().str.contains(needle, na=False)
        | filtered["description"].fillna("").str.lower().str.contains(needle, na=False)
    )
    filtered = filtered[mask]


# Tabs
tab_browse, tab_charts, tab_table = st.tabs(["📋 Карточки", "📊 Графики", "🗂 Таблица"])


with tab_charts:
    if filtered.empty:
        st.info("Под текущие фильтры ничего не попало.")
    else:
        chart_col_left, chart_col_right = st.columns(2)

        with chart_col_left:
            st.subheader("Распределение fit_score")
            hist = px.histogram(
                filtered,
                x="fit_score",
                color="recommendation",
                nbins=20,
                color_discrete_map=RECOMMENDATION_COLORS,
                labels={"fit_score": "fit_score", "count": "кол-во вакансий"},
            )
            hist.update_layout(height=380, bargap=0.05)
            st.plotly_chart(hist, use_container_width=True)

        with chart_col_right:
            st.subheader("По трекам")
            track_counts = (
                filtered.groupby("track").size().reset_index(name="count").sort_values("count")
            )
            track_counts["track_label"] = track_counts["track"].map(
                lambda t: TRACK_LABELS.get(t, t)
            )
            bar = px.bar(
                track_counts,
                x="count",
                y="track_label",
                orientation="h",
                text="count",
                color="count",
                color_continuous_scale="Blues",
            )
            bar.update_layout(height=380, showlegend=False, yaxis_title=None, xaxis_title="вакансий")
            st.plotly_chart(bar, use_container_width=True)

        st.subheader("Я ↔ Они: пересечение")
        scatter = px.scatter(
            filtered,
            x="my_fit_for_them",
            y="their_fit_for_me",
            color="recommendation",
            color_discrete_map=RECOMMENDATION_COLORS,
            hover_data={"title": True, "company": True, "fit_score": ":.2f"},
            labels={
                "my_fit_for_them": "Я подхожу им →",
                "their_fit_for_me": "Они подходят мне →",
            },
        )
        scatter.add_shape(
            type="line", x0=0, y0=0, x1=1, y1=1,
            line=dict(color="rgba(150,150,150,0.4)", dash="dash"),
        )
        scatter.update_layout(height=460)
        st.plotly_chart(scatter, use_container_width=True)


with tab_table:
    if filtered.empty:
        st.info("Под текущие фильтры ничего не попало.")
    else:
        display_columns = [
            "fit_score", "recommendation", "track",
            "title", "company", "salary", "location", "url",
        ]
        st.dataframe(
            filtered[display_columns].sort_values("fit_score", ascending=False),
            use_container_width=True,
            hide_index=True,
            column_config={
                "fit_score": st.column_config.ProgressColumn(
                    "Fit", min_value=0.0, max_value=1.0, format="%.2f"
                ),
                "recommendation": st.column_config.TextColumn("Rec"),
                "track": st.column_config.TextColumn("Трек"),
                "title": st.column_config.TextColumn("Вакансия", width="large"),
                "company": st.column_config.TextColumn("Компания"),
                "salary": st.column_config.TextColumn("ЗП"),
                "location": st.column_config.TextColumn("Локация"),
                "url": st.column_config.LinkColumn("hh.ru", display_text="открыть"),
            },
        )


with tab_browse:
    if filtered.empty:
        st.info("Под текущие фильтры ничего не попало.")
    else:
        sort_options = {
            "fit_score (по убыванию)": ("fit_score", False),
            "fit_score (по возрастанию)": ("fit_score", True),
            "Дата анализа (новые сверху)": ("analyzed_at", False),
        }
        sort_choice = st.selectbox("Сортировка", options=list(sort_options.keys()))
        sort_col, ascending = sort_options[sort_choice]
        sorted_view = filtered.sort_values(sort_col, ascending=ascending)

        st.caption(f"Показано {len(sorted_view)} из {len(df)} вакансий")

        for _, row in sorted_view.iterrows():
            rec = row["recommendation"]
            rec_label = RECOMMENDATION_LABELS.get(rec, rec)
            rec_color = RECOMMENDATION_COLORS.get(rec, "#6366f1")
            track_label = TRACK_LABELS.get(row["track"], row["track"])

            with st.container():
                st.markdown(
                    f"<div class='vacancy-card {rec}'>"
                    f"<div class='vacancy-title'>{row['title']}</div>"
                    f"<div class='vacancy-meta'>"
                    f"<b>{row['company']}</b> · 💰 {row['salary']} · 📍 {row['location']}"
                    f"</div>"
                    f"<div style='margin-top: 0.6rem;'>"
                    f"<span class='score-pill' style='background:{rec_color}20; color:{rec_color};'>"
                    f"fit {row['fit_score']:.2f}</span>"
                    f"<span class='score-pill' style='background:#6366f120; color:#6366f1;'>{track_label}</span>"
                    f"<span class='score-pill' style='background:#88888820;'>я→них {row['my_fit_for_them']:.2f}</span>"
                    f"<span class='score-pill' style='background:#88888820;'>они→я {row['their_fit_for_me']:.2f}</span>"
                    f"<span class='score-pill' style='background:{rec_color}20; color:{rec_color};'>{rec_label}</span>"
                    f"</div>"
                    f"</div>",
                    unsafe_allow_html=True,
                )

                with st.expander("Подробнее"):
                    detail_left, detail_right = st.columns([2, 1])
                    with detail_left:
                        if row.get("reasons"):
                            st.markdown("**Почему подходит**")
                            for reason in row["reasons"]:
                                st.markdown(f"- {reason}")
                        if row.get("red_flags"):
                            st.markdown("**Red flags**")
                            for flag in row["red_flags"]:
                                st.markdown(f"- ⚠️ {flag}")
                        if row.get("description"):
                            st.markdown("**Описание вакансии**")
                            description = str(row["description"])
                            preview_limit = 1500
                            if len(description) > preview_limit:
                                st.markdown(description[:preview_limit] + "…")
                                with st.popover("Показать целиком"):
                                    st.markdown(description)
                            else:
                                st.markdown(description)

                    with detail_right:
                        st.markdown("**Скоры**")
                        st.progress(row["fit_score"], text=f"fit {row['fit_score']:.2f}")
                        st.progress(row["my_fit_for_them"], text=f"я → них {row['my_fit_for_them']:.2f}")
                        st.progress(row["their_fit_for_me"], text=f"они → я {row['their_fit_for_me']:.2f}")
                        st.markdown(
                            f"[🔗 Открыть на hh.ru]({row['url']})", unsafe_allow_html=True
                        )
