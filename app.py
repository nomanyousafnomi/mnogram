"""
╔══════════════════════════════════════════════════════════════════════╗
║                         MNOGRAM v1.0                                 ║
║         Cloud-Native Enterprise Media Sharing Platform               ║
║                                                                      ║
║  Architecture: Stateless services → Azure Front Door → Blob/CosmosDB║
║  Roles: Admin | Creator | Consumer                                   ║
╚══════════════════════════════════════════════════════════════════════╝

CLOUD-NATIVE SCALABILITY CONCEPTS DEMONSTRATED:
- Horizontal autoscaling simulation
- Azure Blob Storage for media objects
- Azure Cosmos DB for metadata/comments
- Azure Cognitive Services for AI tagging & moderation
- Redis caching simulation
- CDN edge caching simulation
- Load balancing simulation
- Async queue processing simulation
- Live observability & distributed tracing
"""

import streamlit as st
import time
import random
import math
import sqlite3
import os
import io
import json
import datetime
import base64
import hashlib
from pathlib import Path

import plotly.graph_objects as go
import plotly.express as px
import pandas as pd
import numpy as np

# ─────────────────────────────────────────────────────────────────────
#  AZURE CONFIGURATION  (paste your keys here)
# ─────────────────────────────────────────────────────────────────────
# ─────────────────────────────────────────────────────────────────────
#  AZURE CONFIGURATION (read from environment variables)
# ─────────────────────────────────────────────────────────────────────
AZURE_STORAGE_CONNECTION_STRING = os.environ.get("AZURE_STORAGE_CONNECTION_STRING", "")
AZURE_COSMOS_URI                = os.environ.get("AZURE_COSMOS_URI", "")
AZURE_COSMOS_KEY                = os.environ.get("AZURE_COSMOS_KEY", "")
AZURE_COGNITIVE_KEY             = os.environ.get("AZURE_COGNITIVE_KEY", "")
AZURE_COGNITIVE_ENDPOINT        = os.environ.get("AZURE_COGNITIVE_ENDPOINT", "")

# Container/DB names
BLOB_CONTAINER   = "mnogram-media"
COSMOS_DATABASE  = "mnogram-db"
COSMOS_CONTAINER = "media-items"

# Detect whether real Azure creds are present
USE_AZURE = bool(AZURE_STORAGE_CONNECTION_STRING) and bool(AZURE_COSMOS_URI) and bool(AZURE_COSMOS_KEY)

# ─────────────────────────────────────────────────────────────────────
#  LOCAL SQLITE FALLBACK
# ─────────────────────────────────────────────────────────────────────
DB_PATH = "mnogram_local.db"

def init_db():
    """Initialise local SQLite database (used when Azure credentials absent)."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE,
            role TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uploader TEXT,
            title TEXT,
            caption TEXT,
            location TEXT,
            tags TEXT,
            ai_tags TEXT,
            ai_caption TEXT,
            sentiment TEXT,
            moderation TEXT,
            file_name TEXT,
            file_data BLOB,
            blob_url TEXT,
            likes INTEGER DEFAULT 0,
            views INTEGER DEFAULT 0,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS comments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            media_id INTEGER,
            commenter TEXT,
            comment TEXT,
            sentiment TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            level TEXT,
            service TEXT,
            message TEXT,
            trace_id TEXT,
            created_at TEXT
        );
        CREATE TABLE IF NOT EXISTS metrics_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts TEXT,
            active_users INTEGER,
            uploads_per_min REAL,
            throughput INTEGER,
            latency_ms REAL,
            node_count INTEGER,
            cache_hit REAL,
            queue_depth INTEGER
        );
    """)
    conn.commit()
    conn.close()

init_db()

# ─────────────────────────────────────────────────────────────────────
#  AZURE BLOB STORAGE HELPERS
# ─────────────────────────────────────────────────────────────────────
def upload_to_blob(file_bytes: bytes, blob_name: str) -> str:
    """Upload bytes to Azure Blob Storage; returns public URL or local path."""
    if not USE_AZURE:
        return f"local://{blob_name}"
    try:
        from azure.storage.blob import BlobServiceClient
        client = BlobServiceClient.from_connection_string(AZURE_STORAGE_CONNECTION_STRING)
        container = client.get_container_client(BLOB_CONTAINER)
        try:
            container.create_container()
        except Exception:
            pass
        blob = container.get_blob_client(blob_name)
        blob.upload_blob(file_bytes, overwrite=True)
        return blob.url
    except ImportError:
        add_log("WARN", "BlobStorage", "azure-storage-blob not installed; using local fallback")
        return f"local://{blob_name}"

# ─────────────────────────────────────────────────────────────────────
#  AZURE COGNITIVE SERVICES HELPERS
# ─────────────────────────────────────────────────────────────────────
def analyze_image_with_ai(file_bytes: bytes) -> dict:
    """Call Azure Computer Vision; returns tags, caption, moderation result."""
    if not USE_AZURE:
        return _mock_ai_analysis()
    try:
        import requests as req
        headers = {
            "Ocp-Apim-Subscription-Key": AZURE_COGNITIVE_KEY,
            "Content-Type": "application/octet-stream",
        }
        params = {"visualFeatures": "Categories,Description,Tags,Adult", "language": "en"}
        resp = req.post(
            f"{AZURE_COGNITIVE_ENDPOINT}/vision/v3.2/analyze",
            headers=headers, params=params, data=file_bytes, timeout=10
        )
        data = resp.json()
        tags    = [t["name"] for t in data.get("tags", [])[:5]]
        caption = data.get("description", {}).get("captions", [{}])[0].get("text", "AI caption unavailable")
        adult   = data.get("adult", {})
        mod     = "FLAGGED" if adult.get("isAdultContent") or adult.get("isRacyContent") else "APPROVED"
        return {"tags": tags, "caption": caption, "moderation": mod}
    except Exception as e:
        add_log("ERROR", "CognitiveServices", str(e))
        return _mock_ai_analysis()

def _mock_ai_analysis() -> dict:
    tag_pool = ["photography","nature","urban","portrait","travel","architecture",
                "food","art","lifestyle","technology","fashion","sport","animals"]
    return {
        "tags":       random.sample(tag_pool, 4),
        "caption":    random.choice([
            "A stunning visual captured with exceptional detail.",
            "An artistic composition showcasing modern aesthetics.",
            "Vibrant colors and dynamic framing in this shot.",
            "A candid moment frozen in time with beautiful lighting.",
        ]),
        "moderation": "APPROVED",
    }

def analyze_sentiment(text: str) -> str:
    """Naive local sentiment – replace with Azure Text Analytics in prod."""
    pos = ["love","great","amazing","beautiful","fantastic","excellent","wonderful"]
    neg = ["hate","bad","awful","terrible","horrible","disgusting","worst"]
    t = text.lower()
    p = sum(w in t for w in pos)
    n = sum(w in t for w in neg)
    if p > n: return "😊 Positive"
    if n > p: return "😡 Negative"
    return "😐 Neutral"

# ─────────────────────────────────────────────────────────────────────
#  DATABASE HELPERS
# ─────────────────────────────────────────────────────────────────────
def db_conn():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def save_media(uploader, title, caption, location, tags, ai_result, file_name, file_bytes, blob_url):
    conn = db_conn()
    conn.execute("""
        INSERT INTO media (uploader,title,caption,location,tags,ai_tags,ai_caption,
                           sentiment,moderation,file_name,file_data,blob_url,created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (uploader, title, caption, location, tags,
          json.dumps(ai_result.get("tags",[])),
          ai_result.get("caption",""),
          analyze_sentiment(caption),
          ai_result.get("moderation","APPROVED"),
          file_name, file_bytes, blob_url,
          datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    add_log("INFO", "UploadService", f"Media '{title}' uploaded by {uploader}")

def get_all_media():
    conn = db_conn()
    rows = conn.execute("SELECT * FROM media ORDER BY id DESC").fetchall()
    conn.close()
    return rows

def get_media_by_id(mid):
    conn = db_conn()
    row = conn.execute("SELECT * FROM media WHERE id=?", (mid,)).fetchone()
    conn.close()
    return row

def save_comment(media_id, commenter, comment):
    conn = db_conn()
    conn.execute("""
        INSERT INTO comments (media_id,commenter,comment,sentiment,created_at)
        VALUES (?,?,?,?,?)
    """, (media_id, commenter, comment, analyze_sentiment(comment),
          datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()
    add_log("INFO", "CommentService", f"Comment by {commenter} on media #{media_id}")

def get_comments(media_id):
    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM comments WHERE media_id=? ORDER BY id DESC", (media_id,)
    ).fetchall()
    conn.close()
    return rows

def like_media(media_id):
    conn = db_conn()
    conn.execute("UPDATE media SET likes=likes+1 WHERE id=?", (media_id,))
    conn.commit(); conn.close()

def increment_views(media_id):
    conn = db_conn()
    conn.execute("UPDATE media SET views=views+1 WHERE id=?", (media_id,))
    conn.commit(); conn.close()

# ─────────────────────────────────────────────────────────────────────
#  LOG HELPERS
# ─────────────────────────────────────────────────────────────────────
def add_log(level, service, message):
    trace_id = hashlib.md5(f"{time.time()}{random.random()}".encode()).hexdigest()[:8]
    conn = db_conn()
    conn.execute("""
        INSERT INTO logs (level,service,message,trace_id,created_at)
        VALUES (?,?,?,?,?)
    """, (level, service, message, trace_id,
          datetime.datetime.utcnow().isoformat()))
    conn.commit(); conn.close()

def get_logs(limit=100):
    conn = db_conn()
    rows = conn.execute(
        "SELECT * FROM logs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return rows

# ─────────────────────────────────────────────────────────────────────
#  SCALABILITY METRICS SIMULATION
# ─────────────────────────────────────────────────────────────────────
def generate_live_metrics(t: float) -> dict:
    """
    Simulate realistic cloud-native metrics.
    t = seconds since epoch; used to create periodic traffic patterns.
    """
    hour_cycle   = math.sin(t / 3600 * 2 * math.pi) * 0.5 + 0.5   # daily rhythm
    spike        = 1.5 if random.random() < 0.05 else 1.0           # random traffic spike
    base_users   = int(250 + hour_cycle * 600 + random.gauss(0, 20))
    active_users = int(base_users * spike)
    uploads_pm   = round(active_users * 0.04 + random.gauss(0, 2), 1)
    throughput   = int(active_users * 12 + random.gauss(0, 50))
    latency_ms   = round(80 + random.gauss(0, 15) + (spike - 1) * 120, 1)
    storage_gb   = round(120 + t / 86400 * 3 + random.gauss(0, 0.5), 2)
    bandwidth_mb = round(throughput * 0.8 + random.gauss(0, 30), 1)
    node_count   = max(2, min(20, int(active_users / 80) + 1))
    cache_hit    = round(min(98, 72 + hour_cycle * 15 + random.gauss(0, 3)), 1)
    queue_depth  = max(0, int(uploads_pm * 0.4 + random.gauss(0, 5)))
    db_ops       = int(throughput * 1.4)
    failed_req   = int(throughput * random.uniform(0.001, 0.008))
    return {
        "active_users":   active_users,
        "uploads_pm":     uploads_pm,
        "throughput":     throughput,
        "latency_ms":     latency_ms,
        "storage_gb":     storage_gb,
        "bandwidth_mb":   bandwidth_mb,
        "node_count":     node_count,
        "cache_hit":      cache_hit,
        "queue_depth":    queue_depth,
        "db_ops":         db_ops,
        "failed_req":     failed_req,
        "success_req":    throughput - failed_req,
        "spike_active":   spike > 1.0,
        "cpu_pct":        min(95, int(30 + (active_users / 800) * 60 + random.gauss(0, 5))),
        "mem_pct":        min(90, int(45 + (node_count / 20) * 30 + random.gauss(0, 3))),
    }

# ─────────────────────────────────────────────────────────────────────
#  PLOTLY THEME HELPERS
# ─────────────────────────────────────────────────────────────────────
DARK_BG   = "#0d1117"
CARD_BG   = "#161b22"
ACCENT    = "#00d4ff"
ACCENT2   = "#7c3aed"
SUCCESS   = "#22c55e"
WARN      = "#f59e0b"
DANGER    = "#ef4444"
TEXT_MAIN = "#e6edf3"
TEXT_DIM  = "#8b949e"

PLOTLY_LAYOUT = dict(
    paper_bgcolor="rgba(0,0,0,0)",
    plot_bgcolor="rgba(0,0,0,0)",
    font=dict(color=TEXT_MAIN, family="monospace"),
    margin=dict(l=10, r=10, t=30, b=10),
    xaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
    yaxis=dict(gridcolor="#21262d", zerolinecolor="#21262d"),
)

def sparkline(values, color=ACCENT, title=""):
    fig = go.Figure(go.Scatter(
        y=values, mode="lines", line=dict(color=color, width=2),
        fill="tozeroy", fillcolor=f"rgba({int(color[1:3],16)},{int(color[3:5],16)},{int(color[5:7],16)},0.15)"
    ))
    fig.update_layout(**PLOTLY_LAYOUT, title=dict(text=title, font=dict(size=12)), height=120)
    fig.update_xaxes(showticklabels=False)
    return fig

def gauge_chart(value, max_val, title, color=ACCENT):
    fig = go.Figure(go.Indicator(
        mode="gauge+number",
        value=value,
        domain={"x": [0, 1], "y": [0, 1]},
        title={"text": title, "font": {"color": TEXT_MAIN, "size": 13}},
        gauge=dict(
            axis=dict(range=[0, max_val], tickcolor=TEXT_DIM),
            bar=dict(color=color),
            bgcolor="#21262d",
            bordercolor="#30363d",
            steps=[
                dict(range=[0, max_val * 0.6],  color="#1a3a2a"),
                dict(range=[max_val * 0.6, max_val * 0.85], color="#3a2f10"),
                dict(range=[max_val * 0.85, max_val], color="#3a1010"),
            ],
        ),
        number=dict(font=dict(color=TEXT_MAIN, size=28)),
    ))
    fig.update_layout(**PLOTLY_LAYOUT, height=200)
    return fig

# ─────────────────────────────────────────────────────────────────────
#  SESSION STATE INITIALISATION
# ─────────────────────────────────────────────────────────────────────
def init_session():
    defaults = {
        "logged_in":      False,
        "username":       None,
        "role":           None,
        "metrics_history": {"ts": [], "users": [], "throughput": [],
                             "latency": [], "nodes": [], "cache": [], "queue": []},
        "page":           "dashboard",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_session()

# ─────────────────────────────────────────────────────────────────────
#  GLOBAL CSS
# ─────────────────────────────────────────────────────────────────────
st.markdown("""
<style>
  @import url('https://fonts.googleapis.com/css2?family=JetBrains+Mono:wght@400;600;700&family=Inter:wght@300;400;600;700&display=swap');

  html, body, [class*="css"] {
      background-color: #0d1117 !important;
      color: #e6edf3 !important;
      font-family: 'Inter', sans-serif;
  }
  .stApp { background-color: #0d1117 !important; }

  /* Sidebar */
  [data-testid="stSidebar"] {
      background: linear-gradient(180deg, #161b22 0%, #0d1117 100%) !important;
      border-right: 1px solid #30363d !important;
  }
  [data-testid="stSidebar"] * { color: #e6edf3 !important; }

  /* Cards */
  .metric-card {
      background: #161b22;
      border: 1px solid #30363d;
      border-radius: 12px;
      padding: 18px 20px;
      margin-bottom: 12px;
      transition: border-color 0.2s;
  }
  .metric-card:hover { border-color: #00d4ff; }
  .metric-card .label {
      font-size: 11px; color: #8b949e; text-transform: uppercase;
      letter-spacing: 1.2px; font-family: 'JetBrains Mono', monospace;
  }
  .metric-card .value {
      font-size: 32px; font-weight: 700; color: #00d4ff;
      font-family: 'JetBrains Mono', monospace; line-height: 1.1;
  }
  .metric-card .delta {
      font-size: 12px; color: #22c55e; font-family: 'JetBrains Mono', monospace;
  }

  /* Alert badges */
  .badge-info    { background:#0c2d48; color:#00d4ff; border:1px solid #00d4ff; border-radius:4px; padding:2px 8px; font-size:11px; font-family:'JetBrains Mono',monospace; }
  .badge-success { background:#0a2e1a; color:#22c55e; border:1px solid #22c55e; border-radius:4px; padding:2px 8px; font-size:11px; }
  .badge-warn    { background:#2e1f0a; color:#f59e0b; border:1px solid #f59e0b; border-radius:4px; padding:2px 8px; font-size:11px; }
  .badge-danger  { background:#2e0a0a; color:#ef4444; border:1px solid #ef4444; border-radius:4px; padding:2px 8px; font-size:11px; }

  /* Logo */
  .logo-text {
      font-family: 'JetBrains Mono', monospace;
      font-size: 26px; font-weight: 700;
      background: linear-gradient(135deg, #00d4ff, #7c3aed);
      -webkit-background-clip: text; -webkit-text-fill-color: transparent;
      letter-spacing: -1px;
  }
  .logo-sub { font-size:10px; color:#8b949e; letter-spacing:2px; text-transform:uppercase; font-family:'JetBrains Mono',monospace; }

  /* Sections */
  .section-header {
      font-size:13px; color:#8b949e; text-transform:uppercase;
      letter-spacing:2px; font-family:'JetBrains Mono',monospace;
      margin:20px 0 8px; border-bottom:1px solid #21262d; padding-bottom:6px;
  }

  /* Log rows */
  .log-row { font-family:'JetBrains Mono',monospace; font-size:11px; padding:3px 0; border-bottom:1px solid #21262d; }

  /* Upload zone */
  [data-testid="stFileUploader"] {
      background:#161b22; border:2px dashed #30363d; border-radius:12px;
  }

  /* Buttons */
  .stButton > button {
      background: linear-gradient(135deg, #00d4ff22, #7c3aed22) !important;
      border: 1px solid #30363d !important; color: #e6edf3 !important;
      border-radius: 8px !important; font-family:'JetBrains Mono',monospace !important;
      transition: all 0.2s !important;
  }
  .stButton > button:hover {
      border-color: #00d4ff !important;
      background: linear-gradient(135deg, #00d4ff33, #7c3aed33) !important;
  }

  /* Inputs */
  .stTextInput input, .stTextArea textarea, .stSelectbox select {
      background: #161b22 !important; border: 1px solid #30363d !important;
      color: #e6edf3 !important; border-radius: 8px !important;
  }

  /* Tabs */
  .stTabs [data-baseweb="tab-list"] { background: #161b22; border-radius: 8px; }
  .stTabs [data-baseweb="tab"] { color: #8b949e !important; }
  .stTabs [aria-selected="true"] { color: #00d4ff !important; border-bottom: 2px solid #00d4ff; }

  /* Media card */
  .media-card {
      background:#161b22; border:1px solid #30363d; border-radius:12px;
      padding:16px; margin-bottom:16px; transition:border-color 0.2s;
  }
  .media-card:hover { border-color: #7c3aed; }

  /* Nodes grid */
  .node-grid { display:flex; flex-wrap:wrap; gap:6px; }
  .node-box {
      width:28px; height:28px; border-radius:6px; border:1px solid #22c55e;
      background:#0a2e1a; display:flex; align-items:center; justify-content:center;
      font-size:9px; font-family:'JetBrains Mono',monospace; color:#22c55e;
  }
  .node-box.scaling { border-color:#f59e0b; background:#2e1f0a; color:#f59e0b; }
  .node-box.draining { border-color:#ef4444; background:#2e0a0a; color:#ef4444; }

  /* Topology pipeline */
  .pipeline {
      display:flex; align-items:center; gap:0; flex-wrap:wrap;
      background:#161b22; border:1px solid #30363d; border-radius:12px;
      padding:16px; margin-bottom:16px;
  }
  .pipe-node {
      background:#21262d; border:1px solid #30363d; border-radius:8px;
      padding:10px 14px; font-size:11px; font-family:'JetBrains Mono',monospace;
      color:#00d4ff; text-align:center; min-width:90px;
  }
  .pipe-arrow { color:#8b949e; font-size:18px; padding:0 4px; }

  /* Media image */
  .media-img { width:100%; border-radius:8px; object-fit:cover; max-height:260px; }

  /* Scrollable log panel */
  .log-panel { max-height:340px; overflow-y:auto; font-family:'JetBrains Mono',monospace; font-size:11px; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
#  DEMO CREDENTIALS
# ─────────────────────────────────────────────────────────────────────
USERS = {
    "admin":   {"password": "admin123",   "role": "admin"},
    "creator": {"password": "creator123", "role": "creator"},
    "user":    {"password": "user123",    "role": "consumer"},
}

# ─────────────────────────────────────────────────────────────────────
#  LOGIN PAGE
# ─────────────────────────────────────────────────────────────────────
def show_login():
    col1, col2, col3 = st.columns([1, 1.4, 1])
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.markdown("""
        <div style='text-align:center; margin-bottom:40px;'>
          <div class='logo-text'>MNOGRAM</div>
          <div class='logo-sub'>Enterprise Media Platform · Cloud-Native</div>
        </div>
        """, unsafe_allow_html=True)

        st.markdown("""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:16px; padding:32px;'>
        """, unsafe_allow_html=True)

        username = st.text_input("Username", placeholder="admin / creator / user")
        password = st.text_input("Password", type="password", placeholder="Enter password")

        if st.button("Sign In here", use_container_width=True):
            u = USERS.get(username)
            if u and u["password"] == password:
                st.session_state.logged_in = True
                st.session_state.username  = username
                st.session_state.role      = u["role"]
                add_log("INFO", "AuthService", f"Login: {username} ({u['role']})")
                st.rerun()
            else:
                st.error("Invalid credentials")

        st.markdown("</div>", unsafe_allow_html=True)
        st.markdown("""
        <div style='text-align:center; margin-top:20px; font-size:12px; color:#8b949e; font-family:JetBrains Mono,monospace;'>
          admin/admin123 &nbsp;·&nbsp; creator/creator123 &nbsp;·&nbsp; user/user123
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
#  SIDEBAR NAVIGATION
# ─────────────────────────────────────────────────────────────────────
def show_sidebar():
    with st.sidebar:
        st.markdown("""
        <div style='padding:20px 0 10px;'>
          <div class='logo-text'>MNOGRAM</div>
          <div class='logo-sub'>v1.0 · Cloud-Native</div>
        </div>
        <hr style='border-color:#30363d; margin:10px 0 20px;'>
        """, unsafe_allow_html=True)

        role = st.session_state.role

        # Azure status indicator
        az_color = SUCCESS if USE_AZURE else WARN
        az_label = "Azure Connected" if USE_AZURE else "Local Mode (SQLite)"
        st.markdown(f"""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:8px; padding:8px 12px; margin-bottom:16px; font-size:11px; font-family:JetBrains Mono,monospace;'>
          <span style='color:{az_color};'>●</span>&nbsp; {az_label}
        </div>
        """, unsafe_allow_html=True)

        st.markdown("<div class='section-header'>Navigation</div>", unsafe_allow_html=True)

        pages = []
        if role == "admin":
            pages = [
                ("📊", "Admin Dashboard",      "admin_dashboard"),
                ("🔬", "Scaling Metrics",       "scaling"),
                ("🗂️", "Upload Monitor",        "upload_monitor"),
                ("📋", "System Logs",           "logs"),
                ("🗺️", "Architecture",          "architecture"),
                ("🖼️", "Browse Media",          "browse"),
            ]
        elif role == "creator":
            pages = [
                ("⬆️", "Upload Media",          "upload"),
                ("🖼️", "My Posts",              "my_posts"),
                ("🗺️", "Architecture",          "architecture"),
            ]
        else:
            pages = [
                ("🖼️", "Browse Media",          "browse"),
                ("🔍", "Search",                "search"),
                ("🗺️", "Architecture",          "architecture"),
            ]

        for icon, label, page_key in pages:
            active = st.session_state.page == page_key
            bg = "#00d4ff22" if active else "transparent"
            border = "#00d4ff" if active else "transparent"
            if st.button(f"{icon}  {label}", key=f"nav_{page_key}", use_container_width=True):
                st.session_state.page = page_key
                st.rerun()

        st.markdown("<hr style='border-color:#30363d; margin:20px 0;'>", unsafe_allow_html=True)
        st.markdown(f"""
        <div style='font-size:12px; color:#8b949e; font-family:JetBrains Mono,monospace; padding:0 4px;'>
          👤 <b style='color:#e6edf3;'>{st.session_state.username}</b><br>
          🔑 <span style='color:#00d4ff;'>{st.session_state.role.upper()}</span>
        </div>
        """, unsafe_allow_html=True)
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("⏻  Sign Out", use_container_width=True):
            for key in list(st.session_state.keys()):
                del st.session_state[key]
            st.rerun()

# ─────────────────────────────────────────────────────────────────────
#  PAGE: ADMIN DASHBOARD
# ─────────────────────────────────────────────────────────────────────
def page_admin_dashboard():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace; margin-bottom:4px;'>📊 Admin Dashboard</h2>", unsafe_allow_html=True)
    st.markdown("<div style='color:#8b949e; font-size:13px; margin-bottom:24px; font-family:JetBrains Mono,monospace;'>Real-time platform observability · Auto-refresh every 3 s</div>", unsafe_allow_html=True)

    # Refresh controls
    col_r1, col_r2, _ = st.columns([1, 1, 5])
    with col_r1:
        auto_refresh = st.checkbox("Live", value=True)
    with col_r2:
        if st.button("↻ Refresh"):
            st.rerun()

    t  = time.time()
    m  = generate_live_metrics(t)
    h  = st.session_state.metrics_history

    # Append to history (keep last 60 points)
    h["ts"].append(datetime.datetime.utcnow().strftime("%H:%M:%S"))
    h["users"].append(m["active_users"])
    h["throughput"].append(m["throughput"])
    h["latency"].append(m["latency_ms"])
    h["nodes"].append(m["node_count"])
    h["cache"].append(m["cache_hit"])
    h["queue"].append(m["queue_depth"])
    for k in h:
        if len(h[k]) > 60:
            h[k].pop(0)

    # Spike alert
    if m["spike_active"]:
        st.markdown("""
        <div style='background:#2e1f0a; border:1px solid #f59e0b; border-radius:8px; padding:12px 16px; margin-bottom:16px; font-family:JetBrains Mono,monospace; font-size:12px;'>
          ⚠️ <b style='color:#f59e0b;'>TRAFFIC SPIKE DETECTED</b> · Autoscaler triggered · Adding nodes…
        </div>
        """, unsafe_allow_html=True)

    # KPI Row 1
    c1, c2, c3, c4 = st.columns(4)
    cards = [
        (c1, "Active Users",      f"{m['active_users']:,}",  "+12%",  ACCENT),
        (c2, "Req / sec",         f"{m['throughput']:,}",    "+8%",   ACCENT2),
        (c3, "Latency (ms)",      f"{m['latency_ms']}",      "-3ms",  SUCCESS if m['latency_ms'] < 120 else WARN),
        (c4, "Server Nodes",      str(m['node_count']),      "auto",  SUCCESS),
    ]
    for col, label, val, delta, color in cards:
        with col:
            st.markdown(f"""
            <div class='metric-card'>
              <div class='label'>{label}</div>
              <div class='value' style='color:{color};'>{val}</div>
              <div class='delta'>{delta}</div>
            </div>
            """, unsafe_allow_html=True)

    # KPI Row 2
    c5, c6, c7, c8 = st.columns(4)
    cards2 = [
        (c5, "Storage (GB)",      f"{m['storage_gb']}",      "growing",   WARN),
        (c6, "Cache Hit %",       f"{m['cache_hit']}%",      "Redis+CDN", SUCCESS),
        (c7, "Upload Queue",      str(m['queue_depth']),     "items",     ACCENT),
        (c8, "DB Ops / sec",      f"{m['db_ops']:,}",        "CosmosDB",  ACCENT2),
    ]
    for col, label, val, delta, color in cards2:
        with col:
            st.markdown(f"""
            <div class='metric-card'>
              <div class='label'>{label}</div>
              <div class='value' style='color:{color};'>{val}</div>
              <div class='delta'>{delta}</div>
            </div>
            """, unsafe_allow_html=True)

    # Charts row
    col_a, col_b = st.columns(2)
    with col_a:
        fig = go.Figure()
        fig.add_trace(go.Scatter(x=h["ts"], y=h["users"], name="Users",
                                 line=dict(color=ACCENT, width=2), fill="tozeroy",
                                 fillcolor="rgba(0,212,255,0.1)"))
        fig.add_trace(go.Scatter(x=h["ts"], y=h["throughput"], name="Req/s",
                                 line=dict(color=ACCENT2, width=2), yaxis="y2"))
        fig.update_layout(**PLOTLY_LAYOUT, height=220,
                          title="Active Users vs Request Throughput",
                          yaxis2=dict(overlaying="y", side="right", gridcolor="#21262d"))
        st.plotly_chart(fig, use_container_width=True)

    with col_b:
        fig2 = go.Figure()
        fig2.add_trace(go.Scatter(x=h["ts"], y=h["latency"], name="Latency ms",
                                  line=dict(color=WARN, width=2), fill="tozeroy",
                                  fillcolor="rgba(245,158,11,0.1)"))
        fig2.add_hline(y=200, line_dash="dash", line_color=DANGER, annotation_text="SLA 200ms")
        fig2.update_layout(**PLOTLY_LAYOUT, height=220, title="API Latency (ms)")
        st.plotly_chart(fig2, use_container_width=True)

    # Gauges
    col_g1, col_g2, col_g3, col_g4 = st.columns(4)
    with col_g1:
        st.plotly_chart(gauge_chart(m["cpu_pct"],  100, "CPU %",     ACCENT),  use_container_width=True)
    with col_g2:
        st.plotly_chart(gauge_chart(m["mem_pct"],  100, "Memory %",  ACCENT2), use_container_width=True)
    with col_g3:
        st.plotly_chart(gauge_chart(m["cache_hit"],100, "Cache Hit", SUCCESS), use_container_width=True)
    with col_g4:
        st.plotly_chart(gauge_chart(m["failed_req"], max(1, m["throughput"]) // 10, "Errors", DANGER), use_container_width=True)

    # Node grid
    st.markdown("<div class='section-header'>Compute Nodes (Horizontal Scaling)</div>", unsafe_allow_html=True)
    node_html = "<div class='node-grid'>"
    for i in range(m["node_count"]):
        cls = "scaling" if i == m["node_count"] - 1 and m["spike_active"] else ""
        node_html += f"<div class='node-box {cls}'>N{i+1:02d}</div>"
    node_html += "</div>"
    st.markdown(node_html, unsafe_allow_html=True)
    st.markdown(f"<div style='font-size:11px; color:#8b949e; margin-top:6px; font-family:JetBrains Mono,monospace;'>Autoscaler policy: scale-out when CPU > 70% · min=2 max=20 nodes</div>", unsafe_allow_html=True)

    # Node count chart
    fig_nodes = go.Figure(go.Bar(x=h["ts"], y=h["nodes"],
                                  marker_color=[SUCCESS if n < 8 else WARN if n < 14 else DANGER for n in h["nodes"]]))
    fig_nodes.update_layout(**PLOTLY_LAYOUT, height=160, title="Node Count Over Time")
    st.plotly_chart(fig_nodes, use_container_width=True)

    # Request breakdown donut
    col_d1, col_d2 = st.columns([1, 2])
    with col_d1:
        labels = ["Success", "4xx", "5xx", "Timeout"]
        vals   = [m["success_req"], int(m["failed_req"] * 0.6), int(m["failed_req"] * 0.3), int(m["failed_req"] * 0.1)]
        fig_d  = go.Figure(go.Pie(labels=labels, values=vals, hole=0.6,
                                   marker_colors=[SUCCESS, WARN, DANGER, TEXT_DIM]))
        fig_d.update_layout(**PLOTLY_LAYOUT, height=220, title="Request Status")
        st.plotly_chart(fig_d, use_container_width=True)
    with col_d2:
        fig_bw = go.Figure(go.Scatter(x=h["ts"],
                                      y=[random.uniform(200, 600) for _ in h["ts"]],
                                      fill="tozeroy", line=dict(color=ACCENT2),
                                      fillcolor="rgba(124,58,237,0.15)"))
        fig_bw.update_layout(**PLOTLY_LAYOUT, height=220, title="Bandwidth (MB/s)")
        st.plotly_chart(fig_bw, use_container_width=True)

    # Live activity feed
    st.markdown("<div class='section-header'>Live Activity Feed</div>", unsafe_allow_html=True)
    events = [
        ("INFO",  "LoadBalancer",   "Request routed to node N03 · latency 82ms"),
        ("INFO",  "CacheLayer",     "Cache HIT for media/popular-123 · CDN edge NYC"),
        ("WARN",  "Autoscaler",     "CPU threshold 71% · scaling evaluation triggered"),
        ("INFO",  "BlobStorage",    "Object uploaded: media/img-4829.jpg · 2.4 MB"),
        ("INFO",  "CosmosDB",       "Document write: comments collection · RU/s: 4.2"),
        ("INFO",  "CDNEdge",        "Cache MISS → origin fetch · Tokyo PoP"),
        ("INFO",  "AuthService",    "JWT validated · user session refreshed"),
        ("ERROR", "UploadWorker",   "Worker-7 timeout on large video · retrying…"),
        ("INFO",  "QueueProcessor", f"Queue depth: {m['queue_depth']} items · 3 workers processing"),
        ("INFO",  "HealthCheck",    "All 6 services healthy · uptime 99.94%"),
    ]
    random.shuffle(events)
    log_html = "<div class='log-panel'>"
    for lvl, svc, msg in events[:8]:
        color = {"INFO": "#00d4ff", "WARN": "#f59e0b", "ERROR": "#ef4444"}.get(lvl, TEXT_DIM)
        ts = (datetime.datetime.utcnow() - datetime.timedelta(seconds=random.randint(1, 30))).strftime("%H:%M:%S")
        log_html += f"<div class='log-row'><span style='color:#8b949e;'>{ts}</span> <span style='color:{color};'>[{lvl}]</span> <span style='color:#7c3aed;'>{svc}</span> {msg}</div>"
    log_html += "</div>"
    st.markdown(log_html, unsafe_allow_html=True)

    if auto_refresh:
        time.sleep(3)
        st.rerun()

# ─────────────────────────────────────────────────────────────────────
#  PAGE: SCALING METRICS
# ─────────────────────────────────────────────────────────────────────
def page_scaling():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🔬 Scaling Metrics</h2>", unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["📈 Autoscaling", "🌐 CDN & Cache", "⚙️ Queue", "🔗 Load Balancer"])

    with tab1:
        st.markdown("#### Horizontal Pod Autoscaler (HPA) Simulation")
        # Simulated scaling history
        ts = [(datetime.datetime.utcnow() - datetime.timedelta(minutes=60 - i)).strftime("%H:%M") for i in range(60)]
        demand  = [int(200 + 400 * abs(math.sin(i / 10)) + random.gauss(0, 30)) for i in range(60)]
        nodes   = [max(2, min(20, d // 80)) for d in demand]
        cpu_pct = [min(95, int(30 + (d / 800) * 65 + random.gauss(0, 5))) for d in demand]

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=ts, y=demand, name="Active Users", line=dict(color=ACCENT, width=2)))
        fig.add_trace(go.Bar(x=ts, y=nodes, name="Node Count", marker_color=SUCCESS, opacity=0.6, yaxis="y2"))
        fig.update_layout(**PLOTLY_LAYOUT, height=280, title="Traffic vs Auto-Scaled Nodes",
                          yaxis2=dict(overlaying="y", side="right", range=[0,25]))
        st.plotly_chart(fig, use_container_width=True)

        col1, col2 = st.columns(2)
        with col1:
            fig2 = go.Figure(go.Scatter(x=ts, y=cpu_pct, fill="tozeroy",
                                         line=dict(color=WARN, width=2),
                                         fillcolor="rgba(245,158,11,0.1)"))
            fig2.add_hline(y=70, line_dash="dash", line_color=DANGER, annotation_text="Scale-out threshold")
            fig2.update_layout(**PLOTLY_LAYOUT, height=220, title="Average CPU %")
            st.plotly_chart(fig2, use_container_width=True)
        with col2:
            lat = [round(80 + random.gauss(0, 20) + max(0, (c - 70) * 2), 1) for c in cpu_pct]
            fig3 = go.Figure(go.Scatter(x=ts, y=lat, fill="tozeroy",
                                         line=dict(color=ACCENT2, width=2),
                                         fillcolor="rgba(124,58,237,0.1)"))
            fig3.add_hline(y=200, line_dash="dash", line_color=DANGER, annotation_text="SLA")
            fig3.update_layout(**PLOTLY_LAYOUT, height=220, title="Latency (ms)")
            st.plotly_chart(fig3, use_container_width=True)

        st.markdown("""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; font-family:JetBrains Mono,monospace; font-size:12px;'>
          <b style='color:#00d4ff;'>Autoscaling Policy</b><br><br>
          <span style='color:#8b949e;'>Scale-OUT trigger:</span> CPU &gt; 70% for 60s OR queue depth &gt; 100<br>
          <span style='color:#8b949e;'>Scale-IN trigger :</span> CPU &lt; 30% for 300s AND queue depth &lt; 10<br>
          <span style='color:#8b949e;'>Min replicas     :</span> 2 &nbsp; <span style='color:#8b949e;'>Max replicas:</span> 20<br>
          <span style='color:#8b949e;'>Cooldown period  :</span> 120 seconds<br>
          <span style='color:#8b949e;'>Provider         :</span> Azure Container Apps / AKS HPA
        </div>
        """, unsafe_allow_html=True)

    with tab2:
        st.markdown("#### CDN & Redis Cache Performance")
        regions = ["East US", "West Europe", "Southeast Asia", "Brazil South", "Australia East"]
        hits    = [random.randint(70, 99) for _ in regions]
        latency = [random.randint(8, 45)  for _ in regions]

        fig_cdn = go.Figure(go.Bar(x=regions, y=hits, marker_color=ACCENT,
                                    text=[f"{h}%" for h in hits], textposition="auto"))
        fig_cdn.update_layout(**PLOTLY_LAYOUT, height=240, title="CDN Cache Hit % by Region")
        st.plotly_chart(fig_cdn, use_container_width=True)

        fig_lat = go.Figure(go.Bar(x=regions, y=latency, marker_color=ACCENT2,
                                    text=[f"{l}ms" for l in latency], textposition="auto"))
        fig_lat.update_layout(**PLOTLY_LAYOUT, height=240, title="Edge Latency by Region (ms)")
        st.plotly_chart(fig_lat, use_container_width=True)

        # Cache tiers
        st.markdown("""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; font-family:JetBrains Mono,monospace; font-size:12px;'>
          <b style='color:#00d4ff;'>Multi-Layer Cache Strategy</b><br><br>
          L1: Browser cache (Cache-Control: max-age=300)<br>
          L2: Azure CDN edge PoPs  → ~8ms avg latency<br>
          L3: Azure Redis Cache    → ~1ms avg latency<br>
          L4: App in-memory LRU    → &lt;0.1ms<br>
          L5: Azure Blob Storage   → origin (cache miss only)
        </div>
        """, unsafe_allow_html=True)

    with tab3:
        st.markdown("#### Upload Queue & Worker Simulation")
        h = st.session_state.metrics_history
        ts_q = h["ts"] if h["ts"] else ["--"]
        q    = h["queue"] if h["queue"] else [0]
        fig_q = go.Figure()
        fig_q.add_trace(go.Scatter(x=ts_q, y=q, name="Queue Depth", fill="tozeroy",
                                    line=dict(color=WARN, width=2), fillcolor="rgba(245,158,11,0.1)"))
        fig_q.update_layout(**PLOTLY_LAYOUT, height=220, title="Upload Queue Depth")
        st.plotly_chart(fig_q, use_container_width=True)

        # Worker status
        workers = [{"id": i+1, "status": random.choice(["IDLE","PROCESSING","PROCESSING"]),
                    "jobs_done": random.randint(10, 200), "avg_ms": random.randint(200, 2000)}
                   for i in range(6)]
        df_w = pd.DataFrame(workers)
        df_w.columns = ["Worker ID", "Status", "Jobs Done", "Avg Process Time (ms)"]
        st.dataframe(df_w, use_container_width=True)

    with tab4:
        st.markdown("#### Load Balancer Distribution")
        nodes_n = ["N01", "N02", "N03", "N04", "N05", "N06"]
        reqs    = [random.randint(800, 1200) for _ in nodes_n]
        fig_lb  = go.Figure(go.Bar(x=nodes_n, y=reqs, marker_color=ACCENT2,
                                    text=reqs, textposition="auto"))
        fig_lb.add_hline(y=sum(reqs) / len(reqs), line_dash="dash",
                          line_color=SUCCESS, annotation_text="Avg load")
        fig_lb.update_layout(**PLOTLY_LAYOUT, height=240, title="Request Distribution per Node (Round-Robin)")
        st.plotly_chart(fig_lb, use_container_width=True)

        st.markdown("""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; font-family:JetBrains Mono,monospace; font-size:12px;'>
          <b style='color:#00d4ff;'>Load Balancing Strategy</b><br><br>
          Algorithm: Weighted Round-Robin with health-aware routing<br>
          Health check interval: 10 seconds<br>
          Sticky sessions: Disabled (stateless services)<br>
          Drain timeout: 30 seconds before scale-in<br>
          Provider: Azure Front Door + Application Gateway
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
#  PAGE: UPLOAD MONITOR
# ─────────────────────────────────────────────────────────────────────
def page_upload_monitor():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🗂️ Upload Monitor</h2>", unsafe_allow_html=True)

    media = get_all_media()
    total_uploads = len(media)
    total_likes   = sum(row[14] for row in media) if media else 0
    total_views   = sum(row[15] for row in media) if media else 0

    c1, c2, c3 = st.columns(3)
    for col, label, val, color in [
        (c1, "Total Uploads",  str(total_uploads), ACCENT),
        (c2, "Total Likes",    str(total_likes),   SUCCESS),
        (c3, "Total Views",    str(total_views),   ACCENT2),
    ]:
        with col:
            st.markdown(f"""
            <div class='metric-card'>
              <div class='label'>{label}</div>
              <div class='value' style='color:{color};'>{val}</div>
            </div>
            """, unsafe_allow_html=True)

    st.markdown("<div class='section-header'>Recent Uploads</div>", unsafe_allow_html=True)
    if media:
        cols = ["ID", "Uploader", "Title", "Location", "AI Tags", "Moderation", "Likes", "Views", "Uploaded"]
        rows = []
        for row in media[:50]:
            ai_tags = json.loads(row[6]) if row[6] else []
            rows.append([row[0], row[1], row[2], row[4],
                         ", ".join(ai_tags), row[9], row[14], row[15], row[16][:19]])
        df = pd.DataFrame(rows, columns=cols)
        st.dataframe(df, use_container_width=True)
    else:
        st.info("No uploads yet. Creators can upload media in the Upload Media page.")

    # Upload activity over time (simulated)
    st.markdown("<div class='section-header'>Upload Rate (simulated stream)</div>", unsafe_allow_html=True)
    ts_range = [(datetime.datetime.utcnow() - datetime.timedelta(minutes=60 - i)).strftime("%H:%M") for i in range(60)]
    upload_rate = [max(0, int(total_uploads * 0.1 + random.gauss(0, 2) + 2 * abs(math.sin(i / 8)))) for i in range(60)]
    fig = go.Figure(go.Bar(x=ts_range, y=upload_rate, marker_color=ACCENT))
    fig.update_layout(**PLOTLY_LAYOUT, height=200, title="Uploads per Minute")
    st.plotly_chart(fig, use_container_width=True)

# ─────────────────────────────────────────────────────────────────────
#  PAGE: SYSTEM LOGS
# ─────────────────────────────────────────────────────────────────────
def page_logs():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>📋 System Logs</h2>", unsafe_allow_html=True)

    # Inject some simulated background logs
    sim_logs = [
        ("INFO",  "CDNEdge",        "Cache warmed for top-100 assets"),
        ("INFO",  "BlobStorage",    "Lifecycle policy: archived 12 objects to cool tier"),
        ("WARN",  "CosmosDB",       "RU/s approaching 80% of provisioned throughput"),
        ("INFO",  "AuthService",    "Token rotation completed for 23 active sessions"),
        ("INFO",  "HealthCheck",    "All endpoints healthy · /health 200 OK"),
        ("INFO",  "QueueProcessor", "Batch processed 47 uploads in 12.4s"),
        ("ERROR", "UploadWorker",   "Timeout processing video transcode job #8821 · retrying"),
        ("INFO",  "Autoscaler",     "Scale-out complete: 6 → 8 nodes in 34s"),
    ]
    if random.random() < 0.3:
        lvl, svc, msg = random.choice(sim_logs)
        add_log(lvl, svc, msg)

    logs = get_logs(200)
    if not logs:
        st.info("No logs yet.")
        return

    # Filters
    col_f1, col_f2 = st.columns(2)
    with col_f1:
        filter_level = st.selectbox("Filter by Level", ["ALL", "INFO", "WARN", "ERROR"])
    with col_f2:
        filter_svc = st.text_input("Filter by Service", placeholder="e.g. AuthService")

    log_html = "<div class='log-panel'>"
    for row in logs:
        lid, lvl, svc, msg, tid, ts = row
        if filter_level != "ALL" and lvl != filter_level:
            continue
        if filter_svc and filter_svc.lower() not in svc.lower():
            continue
        color = {"INFO": "#00d4ff", "WARN": "#f59e0b", "ERROR": "#ef4444"}.get(lvl, TEXT_DIM)
        log_html += f"""
        <div class='log-row'>
          <span style='color:#8b949e;'>{ts[:19]}</span>&nbsp;
          <span style='color:{color};'>[{lvl}]</span>&nbsp;
          <span style='color:#7c3aed;'>{svc}</span>&nbsp;
          <span style='color:#e6edf3;'>{msg}</span>&nbsp;
          <span style='color:#30363d;'>trace={tid}</span>
        </div>"""
    log_html += "</div>"
    st.markdown(log_html, unsafe_allow_html=True)

    if st.button("⬇️ Export Logs (JSON)"):
        data = [{"id": r[0], "level": r[1], "service": r[2], "message": r[3],
                 "trace_id": r[4], "timestamp": r[5]} for r in logs]
        st.download_button("Download logs.json", json.dumps(data, indent=2),
                           file_name="mnogram_logs.json", mime="application/json")

# ─────────────────────────────────────────────────────────────────────
#  PAGE: UPLOAD MEDIA (creator only)
# ─────────────────────────────────────────────────────────────────────
def page_upload():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>⬆️ Upload Media</h2>", unsafe_allow_html=True)
    st.markdown("<div style='color:#8b949e; font-size:13px; margin-bottom:20px;'>Uploads go to Azure Blob Storage · AI tagging via Cognitive Services</div>", unsafe_allow_html=True)

    with st.form("upload_form"):
        uploaded_file = st.file_uploader("Choose image or video", type=["jpg","jpeg","png","gif","mp4","webm"])
        title         = st.text_input("Title", placeholder="Give your post a title")
        caption       = st.text_area("Caption", placeholder="Write a caption…", height=80)
        col1, col2    = st.columns(2)
        with col1:
            location  = st.text_input("Location", placeholder="e.g. London, UK")
        with col2:
            tags      = st.text_input("Tag people", placeholder="@alice, @bob")
        submitted = st.form_submit_button("🚀 Upload & Publish")

    if submitted:
        if not uploaded_file:
            st.error("Please select a file to upload.")
            return
        if not title:
            st.error("Please enter a title.")
            return

        with st.spinner("🔄 Processing upload pipeline…"):
            # Simulate queue enqueue
            add_log("INFO", "QueueService", f"Job enqueued: {uploaded_file.name}")
            time.sleep(0.4)

            file_bytes = uploaded_file.read()

            # Azure Blob upload (or local fallback)
            blob_name = f"{st.session_state.username}/{int(time.time())}_{uploaded_file.name}"
            add_log("INFO", "BlobStorage", f"Uploading {len(file_bytes)//1024} KB to {BLOB_CONTAINER}/{blob_name}")
            blob_url = upload_to_blob(file_bytes, blob_name)
            time.sleep(0.3)

            # AI analysis
            add_log("INFO", "CognitiveServices", "Running image analysis…")
            ai_result = analyze_image_with_ai(file_bytes)
            time.sleep(0.3)

            # Moderation gate
            if ai_result.get("moderation") == "FLAGGED":
                st.error("🚫 Content moderation: this image has been flagged. Upload rejected.")
                add_log("WARN", "ContentModeration", f"Media '{title}' FLAGGED by AI moderator")
                return

            # Store in DB
            save_media(st.session_state.username, title, caption, location, tags,
                       ai_result, uploaded_file.name, file_bytes, blob_url)
            add_log("INFO", "CosmosDB", f"Metadata persisted for '{title}'")

        st.success("✅ Media uploaded and published successfully!")
        col_a, col_b = st.columns(2)
        with col_a:
            st.markdown(f"""
            <div style='background:#161b22; border:1px solid #30363d; border-radius:12px; padding:16px; font-family:JetBrains Mono,monospace; font-size:12px;'>
              <b style='color:#00d4ff;'>AI Analysis Results</b><br><br>
              <b>Tags:</b> {", ".join(ai_result.get("tags",[]))}<br>
              <b>Caption:</b> {ai_result.get("caption","")}<br>
              <b>Moderation:</b> <span style='color:#22c55e;'>{ai_result.get("moderation","")}</span><br>
              <b>Sentiment:</b> {analyze_sentiment(caption)}<br>
              <b>Blob URL:</b> <span style='color:#8b949e;'>{blob_url[:60]}…</span>
            </div>
            """, unsafe_allow_html=True)
        with col_b:
            if uploaded_file.type and uploaded_file.type.startswith("image"):
                st.image(file_bytes, use_container_width=True, caption=title)

# ─────────────────────────────────────────────────────────────────────
#  PAGE: MY POSTS (creator)
# ─────────────────────────────────────────────────────────────────────
def page_my_posts():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🖼️ My Posts</h2>", unsafe_allow_html=True)
    all_media = get_all_media()
    my_media  = [r for r in all_media if r[1] == st.session_state.username]
    if not my_media:
        st.info("You haven't uploaded anything yet. Head to 'Upload Media' to get started!")
        return
    _render_media_grid(my_media, can_comment=False)

# ─────────────────────────────────────────────────────────────────────
#  PAGE: BROWSE MEDIA
# ─────────────────────────────────────────────────────────────────────
def page_browse():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🖼️ Browse Media</h2>", unsafe_allow_html=True)
    media = get_all_media()
    if not media:
        st.info("No media yet. Creators can upload in the 'Upload Media' section.")
        return
    _render_media_grid(media, can_comment=True)

def _render_media_grid(media_rows, can_comment=True):
    cols = st.columns(2)
    for i, row in enumerate(media_rows):
        mid, uploader, title, caption, location, tags, ai_tags, ai_caption, sentiment, mod, file_name, file_data, blob_url, likes, views, created_at = row
        col = cols[i % 2]
        with col:
            st.markdown(f"""
            <div class='media-card'>
              <div style='font-size:15px; font-weight:600; color:#e6edf3;'>{title}</div>
              <div style='font-size:11px; color:#8b949e; font-family:JetBrains Mono,monospace; margin-bottom:8px;'>
                @{uploader} · {location} · {created_at[:10]}
              </div>
            """, unsafe_allow_html=True)

            # Display image if available
            if file_data:
                try:
                    if file_name.lower().endswith((".jpg", ".jpeg", ".png", ".gif")):
                        st.image(bytes(file_data), use_container_width=True)
                except Exception:
                    st.markdown("<div style='color:#8b949e; font-size:12px;'>[Image unavailable]</div>", unsafe_allow_html=True)

            st.markdown(f"""
              <div style='font-size:13px; color:#e6edf3; margin:8px 0;'>{caption}</div>
              <div style='font-size:11px; color:#8b949e; font-family:JetBrains Mono,monospace;'>
                ✨ AI: {ai_caption}<br>
                🏷️ {", ".join(json.loads(ai_tags) if ai_tags else [])}<br>
                {sentiment} &nbsp;·&nbsp; 📍 {location}
              </div>
            </div>
            """, unsafe_allow_html=True)

            c_like, c_view, c_comment = st.columns(3)
            with c_like:
                if st.button(f"❤️ {likes}", key=f"like_{mid}"):
                    like_media(mid)
                    increment_views(mid)
                    add_log("INFO", "EngagementService", f"Like on media #{mid}")
                    st.rerun()
            with c_view:
                st.markdown(f"<div style='font-size:13px; padding-top:8px; color:#8b949e;'>👁️ {views}</div>", unsafe_allow_html=True)
            with c_comment:
                pass

            if can_comment:
                with st.expander(f"💬 Comments ({len(get_comments(mid))})"):
                    comments = get_comments(mid)
                    for c in comments:
                        st.markdown(f"<div style='font-size:12px; border-bottom:1px solid #21262d; padding:4px 0; font-family:JetBrains Mono,monospace;'><b>@{c[2]}</b>: {c[3]} <span style='color:#8b949e;'>{c[5]}</span></div>", unsafe_allow_html=True)
                    new_comment = st.text_input("Add a comment…", key=f"comment_input_{mid}")
                    if st.button("Post", key=f"post_comment_{mid}"):
                        if new_comment.strip():
                            save_comment(mid, st.session_state.username, new_comment.strip())
                            st.rerun()

# ─────────────────────────────────────────────────────────────────────
#  PAGE: SEARCH
# ─────────────────────────────────────────────────────────────────────
def page_search():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🔍 Search Media</h2>", unsafe_allow_html=True)
    query = st.text_input("Search by title, caption, tags, or location", placeholder="e.g. nature, London, portrait…")
    if not query:
        st.info("Enter a search term to find media.")
        return
    media = get_all_media()
    q = query.lower()
    results = [r for r in media if q in (r[2] or "").lower()
               or q in (r[3] or "").lower()
               or q in (r[4] or "").lower()
               or q in (r[6] or "").lower()]
    st.markdown(f"<div style='color:#8b949e; font-size:13px; margin-bottom:16px;'>{len(results)} result(s) for <b>'{query}'</b></div>", unsafe_allow_html=True)
    if results:
        add_log("INFO", "SearchService", f"Query '{query}' → {len(results)} results")
        _render_media_grid(results, can_comment=True)
    else:
        st.warning("No results found.")

# ─────────────────────────────────────────────────────────────────────
#  PAGE: ARCHITECTURE
# ─────────────────────────────────────────────────────────────────────
def page_architecture():
    st.markdown("<h2 style='color:#e6edf3; font-family:JetBrains Mono,monospace;'>🗺️ Scalability Architecture</h2>", unsafe_allow_html=True)
    st.markdown("<div style='color:#8b949e; margin-bottom:24px;'>How Mnogram scales to millions of users using cloud-native Azure services.</div>", unsafe_allow_html=True)

    # Pipeline diagram
    pipe = [
        "👤 Users", "→", "Azure Front Door\n(CDN + WAF)", "→",
        "App Service\n(Stateless)", "→", "Azure Blob\nStorage", "→",
        "Cosmos DB\n(Global)", "→", "Cognitive\nServices"
    ]
    html = "<div class='pipeline'>"
    for item in pipe:
        if item == "→":
            html += "<div class='pipe-arrow'>→</div>"
        else:
            html += f"<div class='pipe-node'>{item}</div>"
    html += "</div>"
    st.markdown(html, unsafe_allow_html=True)

    tab1, tab2, tab3, tab4 = st.tabs(["📐 Design Principles", "☁️ Azure Services", "🔄 Data Flow", "📊 Capacity Plan"])

    with tab1:
        principles = [
            ("Stateless Services", "No session state stored on app servers. All state in Cosmos DB / Redis. Enables perfect horizontal scaling."),
            ("12-Factor App",      "Config via environment vars, logs as streams, disposable processes with fast startup/shutdown."),
            ("Event-Driven",       "Uploads trigger queue events → workers process async. Decouples producers from consumers."),
            ("CDN-First",          "Static assets + media served via Azure CDN edge PoPs. Reduces origin load by ~85%."),
            ("Database per Service","Each microservice owns its data store. No shared schemas. Reduces coupling."),
            ("Circuit Breaker",    "Automatic failure detection; falls back gracefully when downstream services are slow."),
            ("Observability",      "Distributed tracing (trace IDs), structured logs, metrics → Azure Monitor / App Insights."),
            ("Zero-Trust Security","JWT auth on every request. No implicit trust between services. RBAC enforced."),
        ]
        for name, desc in principles:
            st.markdown(f"""
            <div style='background:#161b22; border-left:3px solid #00d4ff; border-radius:0 8px 8px 0; padding:12px 16px; margin-bottom:10px;'>
              <b style='color:#00d4ff; font-family:JetBrains Mono,monospace;'>{name}</b><br>
              <span style='font-size:13px; color:#8b949e;'>{desc}</span>
            </div>
            """, unsafe_allow_html=True)

    with tab2:
        services = [
            ("Azure Front Door",        "Global HTTP load balancer + CDN + WAF. Routes traffic to nearest healthy origin."),
            ("Azure App Service",       "Managed PaaS for Streamlit app. Auto-scales 2–20 instances. Supports containers."),
            ("Azure Blob Storage",      "Object storage for all uploaded media. 11 9s durability. Globally replicated."),
            ("Azure Cosmos DB",         "Globally distributed NoSQL DB. Stores metadata, comments, users. <10ms p99 latency."),
            ("Azure Cognitive Services","Computer Vision API: auto-tag images, generate captions, moderate content."),
            ("Azure Cache for Redis",   "In-memory cache layer. ~1ms latency. Reduces Cosmos DB RU consumption by 60%."),
            ("Azure Service Bus",       "Durable message queue for upload jobs. At-least-once delivery. Dead-letter queue."),
            ("Azure Monitor",           "Centralized metrics, logs, alerts. App Insights for distributed tracing."),
            ("Azure Container Registry","Stores Docker images. Integrated with AKS/Container Apps for deployments."),
        ]
        for name, desc in services:
            st.markdown(f"""
            <div style='background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 16px; margin-bottom:8px;'>
              <b style='color:#7c3aed; font-family:JetBrains Mono,monospace;'>☁️ {name}</b><br>
              <span style='font-size:13px; color:#8b949e;'>{desc}</span>
            </div>
            """, unsafe_allow_html=True)

    with tab3:
        st.markdown("""
```
UPLOAD FLOW (Creator → Media Published):
────────────────────────────────────────
1. Creator POSTs /upload → Front Door (TLS termination)
2. Front Door routes → App Service instance (round-robin)
3. App authenticates JWT → valid session
4. File streamed → Azure Blob Storage (multipart upload)
5. Metadata written → Cosmos DB (media collection)
6. Event published → Azure Service Bus queue
7. Worker picks up event → calls Cognitive Services
8. AI tags + moderation result written back → Cosmos DB
9. CDN cache invalidated for affected feed pages
10. Creator sees success; consumers see new post on next poll

BROWSE FLOW (Consumer → Feed):
──────────────────────────────
1. Consumer GET /media → Front Door
2. Front Door checks CDN cache → HIT 85% of time (served from edge)
3. On MISS → App Service → Redis CACHE check
4. On Redis MISS → Cosmos DB query (indexed by timestamp)
5. Response cached in Redis (TTL 60s) + CDN (TTL 300s)
6. Media files served directly from Blob Storage via CDN URL
```
        """)

    with tab4:
        tiers = [
            ("Tier 1 – MVP",       "1k DAU",   "1 node",    "5 GB",  "$50/mo"),
            ("Tier 2 – Growth",    "50k DAU",  "4 nodes",   "500 GB","$400/mo"),
            ("Tier 3 – Scale",     "500k DAU", "12 nodes",  "5 TB",  "$3,500/mo"),
            ("Tier 4 – Enterprise","5M DAU",   "50+ nodes", "50 TB", "$25,000/mo"),
        ]
        df = pd.DataFrame(tiers, columns=["Tier", "Daily Active Users", "Compute", "Storage", "Est. Cost"])
        st.dataframe(df, use_container_width=True)

        st.markdown("""
        <div style='background:#161b22; border:1px solid #30363d; border-radius:12px; padding:20px; margin-top:16px; font-family:JetBrains Mono,monospace; font-size:12px;'>
          <b style='color:#00d4ff;'>Cost Optimisation Tips</b><br><br>
          • Use Blob Storage lifecycle policies to tier cold media to Archive (~80% cheaper)<br>
          • Cosmos DB autoscale: provision at min RU, burst to max<br>
          • App Service Premium P1v3 reserved instances: 40–60% discount vs pay-as-you-go<br>
          • Azure CDN offloads ~85% of media bandwidth from origin<br>
          • Redis Cache reduces Cosmos RU by 60%, cutting DB cost significantly
        </div>
        """, unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────────────────
#  MAIN ROUTER
# ─────────────────────────────────────────────────────────────────────
def main():
    st.set_page_config(
        page_title="Mnogram · Enterprise Media Platform",
        page_icon="🔷",
        layout="wide",
        initial_sidebar_state="expanded",
    )

    if not st.session_state.get("logged_in"):
        show_login()
        return

    show_sidebar()

    page = st.session_state.get("page", "dashboard")
    role = st.session_state.get("role")

    if role == "admin":
        if page == "admin_dashboard": page_admin_dashboard()
        elif page == "scaling":       page_scaling()
        elif page == "upload_monitor":page_upload_monitor()
        elif page == "logs":          page_logs()
        elif page == "architecture":  page_architecture()
        elif page == "browse":        page_browse()
        else:                         page_admin_dashboard()
    elif role == "creator":
        if page == "upload":          page_upload()
        elif page == "my_posts":      page_my_posts()
        elif page == "architecture":  page_architecture()
        else:                         page_upload()
    else:  # consumer
        if page == "browse":          page_browse()
        elif page == "search":        page_search()
        elif page == "architecture":  page_architecture()
        else:                         page_browse()

if __name__ == "__main__":
    main()
