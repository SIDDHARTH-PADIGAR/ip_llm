from typing import List, Dict
import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

import plotly.express as px
import plotly.graph_objects as go
import pandas as pd
from datetime import datetime, timedelta

def build_event_timeline(events: List[Dict]) -> go.Figure:
    """Build a lollipop chronological timeline to avoid overlapping labels.

    Strategy:
    - convert event dates to datetimes, drop invalid rows
    - sort events by date
    - assign alternating stem heights (1, -1, 2, -2, 3, -3, ...)
      so nearby events use different vertical positions and labels don't collide
    - draw vertical stems as shapes and markers + text as scatter points
    """
    if not events:
        fig = go.Figure()
        fig.update_layout(title="No events to display")
        return fig

    df = pd.DataFrame(events)

    if "date" not in df.columns:
        fig = go.Figure()
        fig.update_layout(title="No events to display")
        return fig

    # Coerce to datetime and drop invalid
    df["date"] = pd.to_datetime(df["date"], errors="coerce", infer_datetime_format=True)
    df = df.dropna(subset=["date"]).sort_values("date").reset_index(drop=True)
    if df.empty:
        fig = go.Figure()
        fig.update_layout(title="No events with valid dates")
        return fig

    # Assign alternating vertical positions to reduce label collisions
    def stem_height(idx):
        sign = 1 if idx % 2 == 0 else -1
        magnitude = (idx // 2) + 1
        return sign * magnitude

    df["y"] = [stem_height(i) for i in range(len(df))]

    # Build figure
    fig = go.Figure()

    # Add stems as shapes for crisp vertical lines
    for _, row in df.iterrows():
        fig.add_shape(
            type="line",
            x0=row["date"],
            y0=0,
            x1=row["date"],
            y1=row["y"],
            line=dict(color="#2c3e50", width=2),
            xref="x",
            yref="y"
        )

    # Add marker points at the top of each stem
    fig.add_trace(go.Scatter(
        x=df["date"],
        y=df["y"],
        mode="markers+text",
        marker=dict(size=10, color="#1f77b4"),
        text=df["desc"].fillna(""),
        # position labels above positive stems, below negative stems
        textposition=[("top center" if y > 0 else "bottom center") for y in df["y"]],
        hovertemplate=df.apply(lambda r: f"{r['date'].strftime('%Y-%m-%d')}<br>{r.get('code','')} — {r.get('desc','')}<br>{(r.get('text') or '')[:400]}", axis=1),
        showlegend=False
    ))

    # Draw a central baseline
    fig.add_shape(type="line",
                  x0=df["date"].min() - pd.Timedelta(days=2),
                  y0=0,
                  x1=df["date"].max() + pd.Timedelta(days=2),
                  y1=0,
                  line=dict(color="lightgray", width=1),
                  xref="x",
                  yref="y")

    # Layout tweaks
    # Determine y-axis range with a small margin
    max_y = max(abs(v) for v in df["y"]) + 1
    fig.update_yaxes(range=[-max_y, max_y], showticklabels=False, zeroline=False)

    fig.update_xaxes(
        tickformat="%Y-%m-%d",
        showgrid=True,
        gridcolor="lightgrey",
        tickangle= -45,
        dtick="M3"  # try 3-month ticks; Plotly adapts if range small
    )

    fig.update_layout(
        title="Chronological Event Timeline",
        height=350 + min(300, len(df) * 10),
        margin=dict(l=40, r=40, t=60, b=80),
        hovermode="closest"
    )

    return fig

def build_claim_evolution(claim_versions: List[Dict]) -> go.Figure:
    """
    claim_versions: [{'version':'Original','claims':[{'id':'1','text':'...'}, ...]}, ...]
    Produces line chart of claim text length across versions per claim id.
    """
    rows = []
    # create an ordered index for versions to keep x-axis consistent
    for ix, v in enumerate(claim_versions):
        version_label = v.get("version", str(ix))
        for c in v.get("claims", []):
            cid = str(c.get("id", ""))
            text = c.get("text", "") or ""
            rows.append({
                "version_order": ix,
                "version_label": version_label,
                "claim_id": cid,
                "length": len(text),
                "text": text
            })
    if not rows:
        fig = go.Figure()
        fig.update_layout(title="No claim data")
        return fig

    df = pd.DataFrame(rows)
    # pivot-like line chart
    fig = go.Figure()
    for cid, group in df.groupby("claim_id"):
        fig.add_trace(go.Scatter(
            x=group["version_label"],
            y=group["length"],
            mode="lines+markers",
            name=f"Claim {cid}",
            text=[t[:400] for t in group["text"]],
            hovertemplate="%{x}<br>Length: %{y}<extra></extra>"
        ))
    fig.update_layout(
        title="Claim text length across versions",
        xaxis_title="Version",
        yaxis_title="Characters",
        height=450,
        margin=dict(l=80, r=20, t=40, b=60)
    )
    return fig