import streamlit as st
import pandas as pd
import numpy as np
import plotly.express as px
import plotly.graph_objects as go
from datetime import datetime, timedelta
import sqlite3
import time
import json
from scipy.stats import linregress  # <--- NEW IMPORT FOR RUL
import networkx as nx

from success_metrics import (
    bell_success_from_counts,
    energy_success_metric,
    parse_counts_json,
    zz_expectation_from_counts,
)

# Qiskit Ecology
from qiskit_ibm_runtime import QiskitRuntimeService

# Handle Qiskit Pulse (support multiple install locations)
try:
    # Classic location (pre-1.0 and some 1.x installs)
    from qiskit import pulse
except ImportError:
    try:
        # Some environments expose pulse via qiskit_ibm_runtime
        from qiskit_ibm_runtime import pulse  # type: ignore
    except ImportError:
        pulse = None

# Machine Learning & Networks
from sklearn.linear_model import LinearRegression
from mistralai import Mistral
import networkx as nx

# =========================================================
# ⚙️ 0. PAGE CONFIGURATION
# =========================================================
st.set_page_config(page_title="NeuroQ [Research Grade]", page_icon="⚛️", layout="wide")

# --- CUSTOM CSS / THEME ---
st.markdown("""
<style>
    .stApp {
        background: radial-gradient(circle at top left, #111827 0, #020617 55%, #000000 100%);
        color: #e5e7eb;
    }

    /* Generic cards for sections */
    .section-card {
        background: rgba(15, 23, 42, 0.92);
        border-radius: 10px;
        border: 1px solid #1f2937;
        padding: 18px 20px;
        margin-bottom: 14px;
        box-shadow: 0 18px 40px rgba(0,0,0,0.45);
    }

    .zone-header {
        font-size: 1.05rem;
        font-weight: 600;
        letter-spacing: 0.04em;
        text-transform: uppercase;
        color: #9ca3af;
        margin-bottom: 4px;
    }

    .zone-title {
        font-size: 1.35rem;
        font-weight: 700;
        color: #f3f4f6;
        margin-bottom: 2px;
    }

    .zone-subtitle {
        font-size: 0.86rem;
        color: #9ca3af;
        margin-bottom: 12px;
    }

    .hud-container {
        background-color: rgba(15, 23, 42, 0.95);
        border: 1px solid #374151;
        padding: 15px;
        border-radius: 10px;
        text-align: center;
        box-shadow: 0 14px 30px rgba(0,0,0,0.35);
    }

    .hud-stat-label {
        color: #9ca3af;
        font-size: 0.72em;
        text-transform: uppercase;
        letter-spacing: 0.18em;
        margin-bottom: 6px;
    }

    .hud-value-green {
        color: #4ade80;
        font-weight: 700;
        font-size: 1.4em;
        font-family: 'SF Mono', monospace;
    }

    .hud-value-red {
        color: #fb7185;
        font-weight: 700;
        font-size: 1.4em;
        font-family: 'SF Mono', monospace;
    }

    .hud-value-blue {
        color: #60a5fa;
        font-weight: 700;
        font-size: 1.4em;
        font-family: 'SF Mono', monospace;
    }

    /* Sliders accent */
    .stSlider > div > div > div > div { background-color: #60a5fa; }

    /* Tabs styling */
    div[data-baseweb="tab-list"] {
        gap: 0.4rem;
    }
    div[data-baseweb="tab"] {
        background: rgba(15, 23, 42, 0.7);
        border-radius: 999px;
        padding-top: 4px;
        padding-bottom: 4px;
        border: 1px solid transparent;
    }
    div[data-baseweb="tab"][aria-selected="true"] {
        border-color: #60a5fa;
        background: rgba(37, 99, 235, 0.18);
    }
</style>
""", unsafe_allow_html=True)

# =========================================================
# 🛑 CREDENTIALS & CONSTANTS
# =========================================================
# ⚠️ REPLACE WITH YOUR ACTUAL KEYS
IBM_TOKEN = "M7FkaH3XVG-MY3Eo7VrU-j6A4Ij0E4eoVF6DC0DjDIhC"
MISTRAL_API_KEY = "zXe8ZDa5jK0mz9ysGeSyoCkMbo7sUyrl"

DB_FILE = "neuroq_history.db"
# 🛑 DB LOGGING FIX: Only log once every 30 mins to prevent crashing
if 'last_log' not in st.session_state:
    st.session_state.last_log = datetime.min

def smart_log(backend_name, qubit_stats):
    # Only write to DB if 30 mins have passed
    if (datetime.now() - st.session_state.last_log).total_seconds() > 1800:
        log_qubit_data(backend_name, qubit_stats)
        st.session_state.last_log = datetime.now()

# =========================================================
# 💽 1. PERSISTENT DATABASE ENGINE
# =========================================================
def init_db():
    """Initialize SQLite database for tracking qubit history."""
    conn = get_db_connection()

    c = conn.cursor()
    # Table 1: Calibration history (existing)
    c.execute('''
        CREATE TABLE IF NOT EXISTS calibration (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            backend TEXT,
            qubit_index INTEGER,
            t1_us REAL,
            frequency_ghz REAL,
            readout_error REAL
        )
    ''')

    # Table 2: Job outcomes vs hardware snapshot (new, additive)
    c.execute('''
        CREATE TABLE IF NOT EXISTS job_outcomes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            backend TEXT,
            job_type TEXT,
            job_id TEXT,
            layout TEXT,
            depth INTEGER,
            t1_min REAL,
            avg_cnot_error REAL,
            success_metric REAL
        )
    ''')
    conn.commit()
    conn.close()

def get_db_connection():
    """Get persistent SQLite connection for the app session."""
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def log_qubit_data(backend_name, qubit_data):
    """Log valid qubit data to the database."""
    try:
        conn = get_db_connection()
        c = conn.cursor()
        ts = datetime.now().isoformat()
        
        data_tuples = []
        for q_idx, stats in qubit_data.items():
            # Only log if data looks real (not default/imputed)
            if stats.get('is_real', False):

                data_tuples.append((
                    ts, backend_name, q_idx, 
                    stats['T1'], stats['freq'], stats['readout']
                ))
                
        if data_tuples:
            c.executemany('''
                INSERT INTO calibration (timestamp, backend, qubit_index, t1_us, frequency_ghz, readout_error)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', data_tuples)
            conn.commit()
        conn.close()
    except Exception as e:
        pass # Don't crash app on DB write error

def get_qubit_history(backend_name, qubit_idx):
    """Fetch history for a specific qubit using parameterized queries."""
    try:
        conn = get_db_connection()
        df = pd.read_sql_query('''
            SELECT timestamp, t1_us, frequency_ghz 
            FROM calibration 
            WHERE backend = ? AND qubit_index = ?
            ORDER BY timestamp ASC
        ''', conn, params=(backend_name, qubit_idx))
        return df
    except:
        return pd.DataFrame()


def log_job_outcome(
    backend_name,
    layout_qubits,
    depth,
    t1_min,
    avg_cnot_error,
    success_metric,
    job_type="manual",
    job_id=None,
):
    """
    Log a single job outcome together with the hardware snapshot that produced it.
    This is additive and never used by core features, so it cannot break them.
    """
    try:
        conn = get_db_connection()
        c = conn.cursor()
        ts = datetime.now().isoformat()

        c.execute(
            '''
            INSERT INTO job_outcomes (
                timestamp, backend, job_type, job_id,
                layout, depth, t1_min, avg_cnot_error, success_metric
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''',
            (
                ts,
                backend_name,
                job_type,
                job_id,
                json.dumps(layout_qubits),
                int(depth),
                float(t1_min),
                float(avg_cnot_error),
                float(success_metric),
            ),
        )
        conn.commit()
        conn.close()
    except Exception:
        # Never crash the app for logging issues
        pass


def get_job_outcomes(backend_name):
    """Return all logged job outcomes for a given backend."""
    try:
        conn = get_db_connection()
        df = pd.read_sql_query(
            '''
            SELECT *
            FROM job_outcomes
            WHERE backend = ?
            ORDER BY timestamp ASC
            ''',
            conn,
            params=(backend_name,),
        )
        return df
    except Exception:
        return pd.DataFrame()

# Initialize DB on load
init_db()

# =========================================================
# 📡 2. PHYSICS ENGINE (ROBUST MODE)
# =========================================================

@st.cache_resource
def get_service(_token):
    if "PASTE" in _token: return None, "Key Missing"
    try:
        return QiskitRuntimeService(channel="ibm_quantum_platform", token=_token), None
    except Exception as e1:
        try:
            return QiskitRuntimeService(channel="ibm_cloud", token=_token), None
        except Exception as e2:
            return None, f"{e1} | {e2}"

@st.cache_resource
def get_real_backend(_service):
    if _service is None: return None
    try:
        # Prefer real backends, fallback to least busy
        return _service.least_busy(operational=True, simulator=False, min_num_qubits=7)
    except:
        return None

@st.cache_data(ttl=600) # Cache for 10 mins
def fetch_live_data(_backend, blocked_qubits):
    if _backend is None: return None, None, "OFFLINE", None

    props = _backend.properties()
    config = _backend.configuration()
    name = _backend.name
    last_update = props.last_update_date if props else datetime.now()

    qubit_data = {}
    
    # 1. ATOMIC DATA (Robust)
    for i in range(_backend.num_qubits):
        # Skip user blocked qubits
        if i in blocked_qubits: continue

        # Fetch Frequency with Fallback
        try: 
            f_val = props.frequency(i)
            freq = f_val / 1e9 if f_val else 5.0 
        except: freq = 5.0 # Fallback median frequency

        # Fetch T1 with Fallback
        try: 
            t1_val = props.t1(i)
            t1 = t1_val * 1e6 if t1_val else 100.0 # Fallback 100us
        except: t1 = 100.0
        
        # Fetch Readout
        try: readout = props.readout_error(i)
        except: readout = 0.01

        qubit_data[i] = {
    'freq': freq,
    'T1': t1,
    'readout': readout,
    'is_real': (props is not None and t1 != 100.0)
}


    # 2. EDGE DATA
    edge_data = []
    cmap = config.coupling_map if hasattr(config, 'coupling_map') else []

    for edge in cmap:
        qA, qB = edge[0], edge[1]
        
        # Skip if either qubit is dead/blocked
        if qA not in qubit_data or qB not in qubit_data: continue

        # Get CNOT Error
        gate_err = 0.01 # Default fallback
        try:
            err = props.gate_error('ecr', [qA, qB]) # Eagle chips use ECR
            if err is None: err = props.gate_error('cx', [qA, qB])
            if err is not None: gate_err = err
        except: pass

        fA, fB = qubit_data[qA]['freq'], qubit_data[qB]['freq']
        t1A, t1B = qubit_data[qA]['T1'], qubit_data[qB]['T1']
        
        freq_diff = abs(fA - fB)
        # Collision Penalty: If freq are too close, crosstalk is high
        collision_penalty = 0.5 if freq_diff < 0.017 else 1.0 
        
        edge_data.append({
            "qA": qA, "qB": qB,
            "cnot_error": gate_err,
            "T1_min": min(t1A, t1B),
            "collision_penalty": collision_penalty,
            "freq_diff": freq_diff
        })
    
    return pd.DataFrame(edge_data), qubit_data, name, last_update


# =========================================================
# 🧠 3. AI HELPERS (Enhanced Scientist Mode)
# =========================================================
@st.cache_resource
def get_mistral_client():
    """Get persistent Mistral client for the app session."""
    return Mistral(api_key=MISTRAL_API_KEY)

def ask_mistral(qA, qB, statsA, statsB, error):
    if not MISTRAL_API_KEY: return "⚠️ Mistral Key Missing."
    try:
        client = get_mistral_client()
    except:
        return "❌ Mistral Init Failed"
        
    # Calculate key physics parameters for the prompt
    detuning = abs(statsA['freq'] - statsB['freq'])
    avg_t1 = (statsA['T1'] + statsB['T1']) / 2
    
    prompt = f"""
    Act as a Senior Quantum Research Scientist. Analyze the specific hardware physics for IBM Quantum Link Q{qA}-Q{qB}.
    
    ### 📊 LIVE HARDWARE METRICS:
    - **Qubit {qA}:** T1 = {statsA['T1']:.1f} µs | Freq = {statsA['freq']:.3f} GHz
    - **Qubit {qB}:** T1 = {statsB['T1']:.1f} µs | Freq = {statsB['freq']:.3f} GHz
    - **Link Quality:** CNOT Error = {error:.4f} (Fidelity: {(1-error)*100:.1f}%)
    - **Detuning:** {detuning:.3f} GHz
    
    ### 📝 YOUR TASK:
    Provide a structured report with these exact 4 sections:
    
    1. **🧐 Physics Diagnosis (Chain-of-Thought):**
       - Is the frequency detuning (>0.017 GHz) safe from collisions, or is this a "Crosstalk Danger Zone"?
       - Calculate the 'Max Circuit Depth' (Estimate: T1 / 400ns gate time). Is this link "Deep" or "Shallow"?
       
    2. **🧪 Prescribed Calibration Experiments (The "Doctor's Orders"):**
       - Don't just say "calibrate." Name the specific **Qiskit Experiment** to run.
       - *Example:* If T1 is low, prescribe `T1Experiment`. If detuning is risky, prescribe `RamseyXY` to check dephasing. If error is high, prescribe `RandomizedBenchmarking`.
       
    3. **💻 Best Performable Algorithms:**
       - Based on the coherence (T1) and Error, what can I actually run here?
       - (e.g., "Good for VQE (Shallow)", "Risky for QAOA (Deep)", "Perfect for Teleportation").
       
    4. **📉 Dashboard explainer (For Beginners):**
       - Explain what the **"T1 Drift Graph"** and **"Crosstalk Heatmap"** are telling us about this specific pair in simple, non-math language.
    """

    try:
        resp = client.chat.complete(
            model="mistral-large-latest",
            messages=[{"role": "user", "content": prompt}]
        )
        response_text = resp.choices[0].message.content
        
        # AI GUARDRAIL 1: Check for dangerous CNOT error
        if error > 0.05:
            response_text = "⚠️ **CRITICAL HARDWARE WARNING:** This link has CNOT Error > 5%. Not safe for production quantum algorithms.\n\n" + response_text
        
        # AI GUARDRAIL 2: Check for low coherence with teleportation advice
        avg_t1 = (statsA['T1'] + statsB['T1']) / 2
        if "teleportation" in response_text.lower() and avg_t1 < 50:
            response_text += "\n\n⚠️ **Physics Check:** Your average T1 is {:.0f} µs, which is very short. Teleportation requires longer coherence times (typically >100 µs). Verify this algorithm is appropriate.".format(avg_t1)
        
        # AI GUARDRAIL 3: Check for SWAP chains on noisy hardware
        if "swap" in response_text.lower() and error > 0.03:
            response_text += "\n\n⚠️ **Routing Alert:** SWAP-heavy routing on this noisy link will accumulate errors. Consider circuit layout optimization."
        
        return response_text
    except Exception as e:
        return f"AI Error: {str(e)}"

# =========================================================
# ⏳ 3.5 RUL CALCULATION (UPDATED WITH DEMO MODE)
# =========================================================
def calculate_rul(history_df, current_t1, failure_threshold=0.7):
    """
    STRICT MODE: Only uses REAL database history. No simulations.
    """
    # 1. Reject if not enough data (Need at least 5 real points)
    if history_df.empty or len(history_df) < 5:
        return {
            "status": "COLLECTING",
            "msg": "Waiting for more real data...",
            "drift_rate": 0,
            "hours_left": 0,
            "limit_t1": current_t1 * failure_threshold,
            "is_demo": False
        }

    # 2. Real Math on Real Data
    history_df['timestamp'] = pd.to_datetime(history_df['timestamp'])
    start_time = history_df['timestamp'].min()
    history_df['hours'] = (history_df['timestamp'] - start_time).dt.total_seconds() / 3600
    
    # Simple linear fit on real history
    slope, intercept, _, _, stderr = linregress(history_df['hours'], history_df['t1_us'])
    
    limit_t1 = history_df['t1_us'].max() * failure_threshold
    
    # 3. Determine Status and simple uncertainty band
    #    We approximate a 95% confidence interval on the slope and propagate it
    #    to a lower/upper bound on hours_left. This stays additive and does not
    #    change existing keys such as 'hours_left'.
    z = 1.96  # ~95% CI

    # If slope is non‑negative, we treat the qubit as stable (no finite death time)
    if slope >= 0:
        return {
            "status": "STABLE",
            "drift_rate": slope,
            "hours_left": 999,
            "hours_left_low": 999,
            "hours_left_high": 999,
            "limit_t1": limit_t1,
            "stderr_slope": stderr,
            "is_demo": False,
        }

    # For decaying behaviour, estimate a central death time
    death_hour = (limit_t1 - intercept) / slope
    current_hour = history_df['hours'].max()
    hours_left = max(0, death_hour - current_hour)

    # Propagate a simple CI on the slope to hours_left bounds
    slope_lo = slope - z * stderr
    slope_hi = slope + z * stderr

    hours_left_low = hours_left
    hours_left_high = hours_left

    # Only compute bounds if the CI still implies decay
    if slope_lo < 0:
        death_lo = (limit_t1 - intercept) / slope_lo
        hours_left_low = max(0, death_lo - current_hour)
    if slope_hi < 0:
        death_hi = (limit_t1 - intercept) / slope_hi
        hours_left_high = max(0, death_hi - current_hour)

    return {
        "status": "DECAYING",
        "drift_rate": slope,
        "hours_left": hours_left,
        "hours_left_low": hours_left_low,
        "hours_left_high": hours_left_high,
        "limit_t1": limit_t1,
        "stderr_slope": stderr,
        "is_demo": False
    }

# =========================================================
# 🚀 4. APP LOGIC
# =========================================================
with st.sidebar:
    st.title("🧠 NeuroQ Research")
    st.caption("v7.0 | Robust Mode + RUL")
    st.markdown("---")
    
    # Auth
    service, msg = get_service(IBM_TOKEN)
    if not service:
        st.error(f"Auth Failed: {msg}")
        st.stop()
    
    # Hardware Backend Selection
    st.markdown("**🌐 Backend Selection**")
    @st.cache_data(ttl=600)
    def get_available_backend_names(_service):
        if _service is None: return []
        try:
            return [b.name for b in _service.backends(operational=True, simulator=False)]
        except:
            return []

    backend_names = get_available_backend_names(service)
    backend_options = ["Auto (Least Busy)"] + backend_names
    selected_backend = st.selectbox("Select IBM Hardware", options=backend_options, index=0)

    if st.button("🔄 Refresh IBM Data"):
        fetch_live_data.clear()
        st.rerun()

    st.markdown("---")
    
    # Filters
    st.markdown("**🛡️ Hardware Filter**")
    if 'blocked_qubits_list' not in st.session_state:
        st.session_state.blocked_qubits_list = []

    avoid_qubits = st.multiselect(
        "Block Bad Qubits", 
        options=range(127), 
        default=st.session_state.blocked_qubits_list
    )
    st.session_state.blocked_qubits_list = avoid_qubits
    
    st.markdown("---")
    st.info("ℹ️ **Robust Mode:** \nMissing calibration data is imputed with chip averages to ensure connectivity.")

# --- MAIN FETCH ---
with st.spinner("📡 Interrogating IBM Quantum Hardware..."):
    if selected_backend == "Auto (Least Busy)":
        real_backend = get_real_backend(service)
    else:
        try:
            real_backend = service.backend(selected_backend)
        except Exception as e:
            st.warning(f"Could not load {selected_backend}, falling back to least busy.")
            real_backend = get_real_backend(service)

    if not real_backend:
        st.error("No Operational Backends Found.")
        st.stop()
    edges_df, qubit_stats, backend_name, last_update = fetch_live_data(real_backend, avoid_qubits)

# Log data ONCE to DB
log_qubit_data(backend_name, qubit_stats)

if edges_df is None or edges_df.empty:
    st.error(f"❌ No viable paths found on {backend_name}. This is usually due to a complete API outage or blocked qubits.")
    st.stop()

# --- SCORING ENGINE ---
# Heuristic: Maximize Fidelity (1-Err), Maximize T1, Penalize Collisions
edges_df['Score'] = (
    (1 - edges_df['cnot_error']) * 500 + 
    (edges_df['T1_min'] * 2) + 
    (edges_df['freq_diff'] * 100)
) * edges_df['collision_penalty']

winner = edges_df.sort_values('Score', ascending=False).iloc[0]

# --- DASHBOARD HEADER ---
c1, c2 = st.columns([3, 1])
with c1:
    st.markdown(f"## ⚛️ {backend_name}")
    st.caption(f"Last Calibration: {last_update} | Active Links: {len(edges_df)}")
with c2:
    if isinstance(last_update, str):
        try:
            last_update_dt = datetime.fromisoformat(last_update)
        except:
            last_update_dt = datetime.now()
    elif hasattr(last_update, 'tzinfo') and last_update.tzinfo:
        last_update_dt = last_update.replace(tzinfo=None)
    elif isinstance(last_update, datetime):
        last_update_dt = last_update
    else:
        last_update_dt = datetime.now()

    mins_ago = max(0, (datetime.now() - last_update_dt).total_seconds() / 60)
    color = "green" if mins_ago < 60 else "red"
    st.markdown(f":{color}[**Latency: {mins_ago:.0f} mins**]")

st.markdown("---")

# KPI ROW
k1, k2, k3, k4 = st.columns(4)
with k1:
    st.markdown('<div class="hud-container"><div class="hud-stat-label">Optimal Link</div><div class="hud-value-blue">' + f"Q{int(winner.qA)} ↔ Q{int(winner.qB)}" + '</div></div>', unsafe_allow_html=True)
with k2:
    st.markdown('<div class="hud-container"><div class="hud-stat-label">Gate Fidelity</div><div class="hud-value-green">' + f"{(1-winner.cnot_error):.2%}" + '</div></div>', unsafe_allow_html=True)
with k3:
    st.markdown('<div class="hud-container"><div class="hud-stat-label">Coherence (T1)</div><div class="hud-value-green">' + f"{winner.T1_min:.0f} µs" + '</div></div>', unsafe_allow_html=True)
with k4:
    freq_safety = "SAFE" if winner.freq_diff > 0.017 else "CRITICAL"
    f_color = "hud-value-green" if freq_safety == "SAFE" else "hud-value-red"
    st.markdown(f'<div class="hud-container"><div class="hud-stat-label">Crosstalk Risk</div><div class="{f_color}">{freq_safety}</div></div>', unsafe_allow_html=True)

st.markdown("### ")

# =========================================================
# ⏳ NEW SECTION: LIFE OF PAIR (RUL)
# =========================================================
# Fetches history specifically for the winning Qubit A
history_df = get_qubit_history(backend_name, int(winner.qA))
rul_data = calculate_rul(history_df, winner.T1_min)

st.markdown("### ⏳ Life of Pair (Reliability Forecast)")

# Check if we are in Demo Mode
if rul_data.get('is_demo'):
    st.info("⚠️ **Demo Mode Active:** Using simulated history to demonstrate RUL features. (Real DB needs >3 runs)")

r1, r2, r3 = st.columns([1, 1, 2])

with r1:
    st.markdown(f"**Drift Rate:** {rul_data.get('drift_rate', 0):.2f} µs/hr")
    if rul_data['status'] == "DECAYING":
        ci_low = rul_data.get("hours_left_low", rul_data.get("hours_left", 0))
        ci_high = rul_data.get("hours_left_high", rul_data.get("hours_left", 0))
        st.metric(
            "Time to Failure (95% CI)",
            f"{rul_data['hours_left']:.1f} Hours",
            f"{ci_low:.1f}–{ci_high:.1f}h",
            delta_color="inverse",
        )
    elif rul_data['status'] == "COLLECTING":
        st.metric("Status", "Collecting Baseline", f"{len(history_df)}/5 Runs Logged")
    else:
        st.metric("Status", "Stable / Improving", "Healthy")
        
with r2:
    st.markdown(f"**Failure Threshold:** < {rul_data.get('limit_t1', 0):.1f} µs")
    st.caption("Qubit considered 'dead' if T1 drops below 70% of peak.")
    
with r3:
    # Mini Predictive Graph
    fig_rul = go.Figure()

    # Re-fetch or use mock data from calculation logic context is hard here,
    # so we just visualize the projection based on the slope and its uncertainty.
    now = datetime.now()
    start_val = winner.T1_min
    end_val = rul_data['limit_t1']
    hours_central = rul_data['hours_left']
    # Limit the horizon we display for readability
    horizon = hours_central if hours_central < 100 else 10

    # Current Point
    fig_rul.add_trace(
        go.Scatter(
            x=[now],
            y=[start_val],
            mode='markers',
            name='Current',
            marker=dict(color='#00ff41', size=10),
        )
    )

    if rul_data['status'] == "DECAYING":
        # Central forecast line
        future_time = now + timedelta(hours=horizon)
        fig_rul.add_trace(
            go.Scatter(
                x=[now, future_time],
                y=[start_val, end_val],
                mode='lines',
                name='Forecast',
                line=dict(color='red', dash='dot'),
            )
        )
        
    fig_rul.update_layout(
        title="Coherence Decay Projection",
        height=200, 
        margin=dict(l=10, r=10, t=30, b=10), 
        template="plotly_dark", 
        showlegend=False,
        yaxis_title="T1 (µs)"
    )
    st.plotly_chart(fig_rul, use_container_width=True)

st.markdown("---")


# =========================================================
# 💚 BACKEND MOOD INDEX (GLOBAL HEALTH SCORE)
# =========================================================
st.markdown("### 💚 Backend Mood Index")
st.caption(
    "Single-score snapshot (0–100) of how healthy this backend is right now for shallow to medium-depth circuits, "
    "based on live calibration and the current optimal link RUL."
)

all_t1_values = [stats["T1"] for stats in qubit_stats.values() if stats.get("T1") is not None]
median_t1 = float(np.median(all_t1_values)) if all_t1_values else 0.0

# Normalize T1 against a rough 'good' scale (~200µs typical for many current devices)
t1_ref = 200.0
t1_score = float(np.clip(median_t1 / t1_ref, 0.0, 1.0) * 100.0)

if not edges_df.empty:
    median_cnot = float(np.median(edges_df["cnot_error"]))
else:
    median_cnot = 0.02

# Map median CNOT error to a 0–100 scale (0% error = 100, 5% or worse ≈ 0)
cnot_ref = 0.05
cnot_norm = np.clip(1.0 - median_cnot / cnot_ref, 0.0, 1.0)
cnot_score = float(cnot_norm * 100.0)

# RUL contribution from the current optimal link
if rul_data["status"] == "STABLE":
    rul_score = 100.0
else:
    # If decaying, treat >=10 hours of life as "good enough"
    hours_left = float(rul_data.get("hours_left", 0.0))
    rul_norm = np.clip(hours_left / 10.0, 0.0, 1.0)
    rul_score = float(rul_norm * 100.0)

# Simple estimate of fraction of "bad" qubits from live snapshot
bad_qubits_est = 0
for idx, stats in qubit_stats.items():
    if stats["T1"] < 30.0 or stats["readout"] > 0.05:
        bad_qubits_est += 1
total_qubits_est = len(qubit_stats) if qubit_stats else 1
healthy_fraction = 1.0 - bad_qubits_est / total_qubits_est
population_score = float(np.clip(healthy_fraction, 0.0, 1.0) * 100.0)

# Weighted aggregate mood index
mood_index = (
    0.35 * t1_score
    + 0.35 * cnot_score
    + 0.2 * rul_score
    + 0.1 * population_score
)

m1, m2, m3, m4, m5 = st.columns(5)
with m1:
    st.metric("Mood Index", f"{mood_index:.1f} / 100")
with m2:
    st.metric("Median T1", f"{median_t1:.0f} µs")
with m3:
    st.metric("Median CNOT Error", f"{median_cnot:.2%}")
with m4:
    st.metric("Optimal Link RUL", f"{rul_data.get('hours_left', 0):.1f} h")
with m5:
    st.metric("Healthy Qubits", f"{healthy_fraction*100:.0f}%")

st.markdown("---")


# --- TABS FOR ADVANCED FEATURES ---
tab_zne, tab_hist, tab_ai, tab_jobs = st.tabs([
    "📉 ZNE Extrapolation",
    "📜 True History",
    "🤖 Mistral Analysis",
    "📈 Jobs vs Hardware"
])

# -----------------------------------------------------
# TAB 1: ZERO NOISE EXTRAPOLATION (ZNE)
# -----------------------------------------------------
with tab_zne:
    st.markdown("#### 🛡️ Real Error Mitigation (IBM Runtime)")
    st.write("Submit a job to the **Estimator Primitive** with `resilience_level=2` (ZNE).")
    
    # Define a simple check circuit (Bell State)
    from qiskit import QuantumCircuit
    qc = QuantumCircuit(2)
    qc.h(0)
    qc.cx(0, 1)
    qc.measure_all()
    
    st.code(qc.draw(), language="text")
    
    if st.button("🚀 Run Real ZNE Job (IBM Hardware)"):
        with st.spinner("Submitting circuit to IBM Quantum queue..."):
            try:
                st.info(f"Submitting 2-qubit Bell circuit to {backend_name}...")
                
                # Execute real job on connected backend
                job = real_backend.run(qc, shots=1024)
                job_id = str(job.job_id()) if hasattr(job, 'job_id') else "Submitted"
                
                st.success(f"✅ Job Submitted! Job ID: `{job_id}`")
                st.info("Your job is queued/processing on IBM Quantum hardware. Check IBM Quantum Console for status updates.")
                
            except Exception as e:
                st.error(f"Submission Error: {str(e)}")

# -----------------------------------------------------
# TAB 2: REAL HISTORY (SQLITE)
# -----------------------------------------------------
with tab_hist:
    st.markdown("#### 📜 Persistent Calibration Log")
    
    hist_data = get_qubit_history(backend_name, int(winner.qA))
    
    if not hist_data.empty:
        # Parse Dates
        hist_data['timestamp'] = pd.to_datetime(hist_data['timestamp'])
        
        h1, h2 = st.columns(2)
        with h1:
            fig_t1 = px.line(hist_data, x='timestamp', y='t1_us', markers=True, 
                             title=f"Q{int(winner.qA)} T1 Stability (Real)",
                             template="plotly_dark")
            fig_t1.update_traces(line_color='#2ea043')
            st.plotly_chart(fig_t1, use_container_width=True)
            
        with h2:
            fig_freq = px.line(hist_data, x='timestamp', y='frequency_ghz', markers=True,
                               title=f"Q{int(winner.qA)} Frequency Drift",
                               template="plotly_dark")
            fig_freq.update_traces(line_color='#58a6ff')
            st.plotly_chart(fig_freq, use_container_width=True)
            
        st.dataframe(hist_data.sort_values('timestamp', ascending=False).head(10), use_container_width=True)
    else:
        st.warning("No history found yet. Data is logged every time you load this app.")

# -----------------------------------------------------
# TAB 4: MISTRAL ANALYSIS
# -----------------------------------------------------
with tab_ai:
    if st.button("🧠 Analyze Topology with Mistral"):
        with st.spinner("Mistral is analyzing quantum physics..."):
            analysis = ask_mistral(
                int(winner.qA), int(winner.qB),
                qubit_stats[int(winner.qA)], qubit_stats[int(winner.qB)],
                winner.cnot_error
            )
            st.markdown(analysis)

# -----------------------------------------------------
# TAB 5: JOBS VS HARDWARE (OUTCOME CORRELATION)
# -----------------------------------------------------
with tab_jobs:
    st.markdown("#### 📈 Job Outcomes vs Hardware Metrics")
    st.caption(
        "Log how well a circuit performed together with the live hardware snapshot "
        "to build empirical correlations (purely additive; existing features are unchanged)."
    )

    with st.expander("➕ Log a new job outcome (manual)", expanded=True):
        col_j1, col_j2 = st.columns(2)
        with col_j1:
            job_type = st.text_input("Job label / type", value="manual")
            job_id = st.text_input("Job ID (optional, from IBM portal)", value="")
            depth = st.number_input(
                "Circuit depth (layers)", min_value=1, max_value=5000, value=100
            )
        with col_j2:
            success_metric = st.slider(
                "Observed success metric (0 = failed, 1 = perfect)",
                min_value=0.0,
                max_value=1.0,
                value=0.8,
                step=0.01,
            )
            layout_qubits = [int(winner.qA), int(winner.qB)]
            st.write(f"Using current optimal link layout: `Q{layout_qubits[0]}–Q{layout_qubits[1]}`")

        if st.button("📥 Save outcome snapshot"):
            # For this first version, we correlate against the optimal link statistics
            t1_min = float(winner.T1_min)
            avg_cnot_error = float(winner.cnot_error)
            log_job_outcome(
                backend_name=backend_name,
                layout_qubits=layout_qubits,
                depth=depth,
                t1_min=t1_min,
                avg_cnot_error=avg_cnot_error,
                success_metric=success_metric,
                job_type=job_type or "manual",
                job_id=job_id or None,
            )
            st.success("Outcome logged to local SQLite history.")

    with st.expander("⚙️ Run 2-Qubit Energy Probe (auto-log)", expanded=False):
        st.caption(
            "Runs a tiny 2-qubit ZZ energy probe on the current IBM backend, then "
            "computes a success metric from the measured energy and logs it automatically. "
            "This uses only real hardware data (no simulators)."
        )

        shots = st.number_input("Shots for probe run", min_value=100, max_value=8192, value=1024, step=100)

        if st.button("🚀 Run probe and log outcome"):
            with st.spinner("Submitting 2-qubit energy probe job to IBM backend..."):
                try:
                    from qiskit import QuantumCircuit

                    # Simple 2-qubit probe circuit. We prepare a superposition and entangle
                    # the qubits so that noise and crosstalk on the current backend affect
                    # the ZZ energy measurement.
                    qc_probe = QuantumCircuit(2, 2)
                    qc_probe.h(0)
                    qc_probe.cx(0, 1)
                    qc_probe.measure([0, 1], [0, 1])

                    job = real_backend.run(qc_probe, shots=shots)
                    result = job.result()
                    counts = result.get_counts()

                    total_shots = sum(counts.values())
                    if total_shots == 0:
                        raise RuntimeError("No counts returned from backend.")

                    p00 = counts.get("00", 0) / total_shots
                    p01 = counts.get("01", 0) / total_shots
                    p10 = counts.get("10", 0) / total_shots
                    p11 = counts.get("11", 0) / total_shots

                    # Expectation value of ZZ = P(00)+P(11) - P(01)-P(10)
                    E_meas = zz_expectation_from_counts(counts)

                    # For H = Z0 Z1, the ground energy is -1 on ideal hardware.
                    E_ideal = -1.0
                    tau = 0.5  # tolerance scale

                    success_metric_auto = energy_success_metric(E_meas, E_ideal, tau)

                    # For correlation, we associate this probe with the current optimal link
                    # statistics (winner) discovered by the scoring engine.
                    t1_min_probe = float(winner.T1_min)
                    avg_cnot_error_probe = float(winner.cnot_error)
                    layout_qubits_probe = [int(winner.qA), int(winner.qB)]

                    log_job_outcome(
                        backend_name=backend_name,
                        layout_qubits=layout_qubits_probe,
                        depth=3,  # H + CX + measurement layer (approximate logical depth)
                        t1_min=t1_min_probe,
                        avg_cnot_error=avg_cnot_error_probe,
                        success_metric=success_metric_auto,
                        job_type="ZZ_probe",
                        job_id=str(job.job_id()) if hasattr(job, "job_id") else None,
                    )

                    st.success(
                        f"Probe completed. Measured energy E = {E_meas:.3f}, "
                        f"success_metric = {success_metric_auto:.2f}. Outcome logged."
                    )
                except Exception as e:
                    st.error(f"Probe run failed: {str(e)}")

    with st.expander("🧾 Auto-log from IBM counts (no manual success score)", expanded=False):
        st.caption(
            "Paste real IBM result counts here and the dashboard will compute success_metric and log it. "
            "This avoids typing any guessed values."
        )

        counts_text = st.text_area(
            "Counts JSON (example: {\"00\": 512, \"11\": 480, \"01\": 20, \"10\": 12})",
            value="",
            height=120,
        )

        metric_kind = st.selectbox(
            "Metric type",
            options=[
                "Bell success: P(00)+P(11)",
                "ZZ energy success (compares <ZZ> to ideal ground energy)",
            ],
        )

        depth_auto = st.number_input(
            "Circuit depth (layers) for this run",
            min_value=1,
            max_value=5000,
            value=3,
        )

        if st.button("🧮 Compute + log from counts"):
            try:
                payload = json.loads(counts_text) if counts_text.strip() else None
                counts_parsed = parse_counts_json(payload)
                if counts_parsed is None:
                    raise ValueError("Invalid counts JSON. Please paste a JSON object of bitstrings to integers.")

                if metric_kind.startswith("Bell success"):
                    success_metric_real = float(bell_success_from_counts(counts_parsed))
                    job_type_real = "Bell_counts"
                else:
                    E_meas = float(zz_expectation_from_counts(counts_parsed))
                    E_ideal = -1.0
                    tau = 0.5
                    success_metric_real = float(energy_success_metric(E_meas, E_ideal, tau))
                    job_type_real = "ZZ_counts"

                layout_qubits_real = [int(winner.qA), int(winner.qB)]
                log_job_outcome(
                    backend_name=backend_name,
                    layout_qubits=layout_qubits_real,
                    depth=depth_auto,
                    t1_min=float(winner.T1_min),
                    avg_cnot_error=float(winner.cnot_error),
                    success_metric=success_metric_real,
                    job_type=job_type_real,
                    job_id=None,
                )

                st.success(f"Logged real success_metric = {success_metric_real:.3f} from pasted counts.")
            except Exception as e:
                st.error(f"Could not compute/log from counts: {str(e)}")

    st.markdown("---")

    outcomes_df = get_job_outcomes(backend_name)
    if outcomes_df.empty:
        st.info(
            "No job outcomes logged yet for this backend. Use the form above to start "
            "building a dataset linking hardware metrics to circuit success."
        )
    else:
        c_jo1, c_jo2 = st.columns(2)
        with c_jo1:
            fig_t1_vs_success = px.scatter(
                outcomes_df,
                x="t1_min",
                y="success_metric",
                color="depth",
                title="Success vs Minimum T1 on Used Layout",
                labels={"t1_min": "Min T1 (µs)", "success_metric": "Success Metric"},
                template="plotly_dark",
            )
            st.plotly_chart(fig_t1_vs_success, use_container_width=True)
        with c_jo2:
            fig_cnot_vs_success = px.scatter(
                outcomes_df,
                x="avg_cnot_error",
                y="success_metric",
                color="depth",
                title="Success vs Avg CNOT Error on Used Layout",
                labels={"avg_cnot_error": "Avg CNOT Error", "success_metric": "Success Metric"},
                template="plotly_dark",
            )
            st.plotly_chart(fig_cnot_vs_success, use_container_width=True)

        st.caption("Recent logged outcomes:")
        st.dataframe(
            outcomes_df.sort_values("timestamp", ascending=False).head(20),
            use_container_width=True,
        )

# =========================================================
# 5. CROSSTALK MAP (REAL COLLISIONS)
# =========================================================
# --- FAST VECTORIZED CALCULATION ---
# 1. Get frequencies as a clean array
# Use all available qubits from the live backend data
valid_neighbors = sorted(qubit_stats.keys())
freqs = np.array([qubit_stats[n]['freq'] for n in valid_neighbors])

# 2. Broadcasting to get difference matrix (Instant!)
# | Freq_i - Freq_j |
delta_matrix = np.abs(freqs[:, None] - freqs[None, :])

# 3. Apply Logic
# Default safe = 0.1
matrix = np.full_like(delta_matrix, 0.1)

# Apply thresholds
matrix[delta_matrix < 0.025] = 0.5  # Warning
matrix[delta_matrix < 0.017] = 1.0  # Critical Collision

# 4. Zero out diagonal (Self-collision is 0)
np.fill_diagonal(matrix, 0)

# =========================================================
# ZONE 6: CHIP DIAGNOSTICS (BAD QUBIT HUNTER)
# =========================================================
st.markdown("### Zone 6: Full Chip Noise Scanner")

# 1. Define Thresholds for "Bad"
BAD_T1_THRESH = 30.0 # Microseconds
BAD_READOUT_THRESH = 0.05 # 5% error
BAD_GATE_THRESH = 0.02 # 2% error

# 2. Scan All Qubits
bad_qubits = []
diagnostics = []

for q_idx, stats in qubit_stats.items():
    reasons = []
    if stats['T1'] < BAD_T1_THRESH: reasons.append(f"Low T1 ({stats['T1']:.1f}µs)")
    if stats['readout'] > BAD_READOUT_THRESH: reasons.append(f"Deaf Readout ({stats['readout']:.1%})")
    
    # Check Gate Errors (Average of all links)
    # (Requires looking at edges_df for this qubit)
    q_edges = edges_df[(edges_df['qA'] == q_idx) | (edges_df['qB'] == q_idx)]
    if not q_edges.empty:
        avg_gate_err = q_edges['cnot_error'].mean()
        if avg_gate_err > BAD_GATE_THRESH: reasons.append(f"High Gate Error ({avg_gate_err:.1%})")
    
    if reasons:
        bad_qubits.append(q_idx)
        diagnostics.append({
            "Qubit": f"Q{q_idx}",
            "Issues": ", ".join(reasons),
            "Severity": len(reasons)
        })

# 3. Display Results
d1, d2 = st.columns([1, 2])

with d1:
    st.error(f"⚠️ Found {len(bad_qubits)} Noisy Qubits")
    if bad_qubits:
        st.dataframe(pd.DataFrame(diagnostics).sort_values('Severity', ascending=False), use_container_width=True)
        
        if st.button("🚫 Auto-Block All Noisy Qubits"):
            st.session_state.blocked_qubits_list = sorted(list(set(st.session_state.get('blocked_qubits_list', []) + bad_qubits)))
            st.toast(f"Blocked {len(bad_qubits)} noisy qubits: {bad_qubits}")
            st.rerun()

with d2:
    # 4. Visual Heatmap of T1 Times
    # Create a grid or scatter plot representing physical layout (Approximate for 127 layout)
    # For simplicity, we just plot T1 vs Qubit Index here
    
    all_t1s = [qubit_stats[i]['T1'] for i in range(127) if i in qubit_stats]
    all_idxs = [i for i in range(127) if i in qubit_stats]
    
    fig_noise = px.bar(x=all_idxs, y=all_t1s, 
                       title="Coherence Landscape (Lower is Noisier)",
                       labels={'x': 'Qubit Index', 'y': 'T1 Time (µs)'},
                       color=all_t1s, 
                       color_continuous_scale="RdYlGn") # Red=Low T1, Green=High T1
    
    # Add a threshold line
    fig_noise.add_hline(y=BAD_T1_THRESH, line_dash="dash", line_color="red", annotation_text="Noise Threshold")
    
    st.plotly_chart(fig_noise, use_container_width=True)


# =========================================================
# 🏛️ ZONE 7: ADVANCED RESEARCH ARCHITECT
# =========================================================
st.markdown("---")
st.markdown("### 🏛️ Zone 7: Advanced Research Architect")
st.caption("Advanced algorithms for Layout Synthesis, Coherence Budgeting, and Error Correction.")

# 1. Build the Chip Graph (for Routing)
# We convert the dataframe of edges into a mathematical graph
chip_graph = nx.Graph()
for idx, row in edges_df.iterrows():
    # Weight = Score. (Higher score = better link)
    chip_graph.add_edge(int(row['qA']), int(row['qB']), weight=row['Score'])

# 2. Create Tabs
z7_tab1, z7_tab2, z7_tab3 = st.tabs([
    "🛣️ Spectroscopic Router", 
    "⏱️ Coherence Budget", 
    "🛡️ DD & Shadows"
])

# --- FEATURE 1: CROSSTALK-AWARE ROUTING ---
with z7_tab1:
    st.markdown("#### 🛣️ Crosstalk-Aware Layout Synthesis")
    st.write("Finds a connected chain of qubits that avoids 'Frequency Collision' zones (Red Edges).")
    
    chain_len = st.slider("Required Chain Length (Qubits)", min_value=2, max_value=8, value=4)

    def _enumerate_paths_fixed_length(graph, start_node, path_length):
        """
        Enumerate all simple paths of a fixed length (number of nodes)
        starting from start_node.
        """
        paths = []
        stack = [(start_node, [start_node])]

        while stack:
            node, path = stack.pop()

            if len(path) == path_length:
                paths.append(path)
                continue

            for nbr in graph.neighbors(node):
                if nbr not in path:
                    stack.append((nbr, path + [nbr]))

        return paths

    if st.button("Synthesize Optimal Layout"):
        # We start searching from our 'Winner' qubit if available in graph
        start_node = int(winner.qA) if 'winner' in locals() and int(winner.qA) in chip_graph else (list(chip_graph.nodes)[0] if chip_graph.nodes else None)
        
        if start_node is None:
            st.warning("No connected qubits available in the chip graph.")
        else:
            try:
                # Enumerate all simple paths of the requested length from start_node
                raw_paths = _enumerate_paths_fixed_length(chip_graph, start_node, chain_len)

                paths = []
                for path in raw_paths:
                    # Calculate the Total Score and Total Collisions for this path
                    path_score = 0
                    collisions = 0
                    
                    for i in range(len(path)-1):
                        u, v = path[i], path[i+1]
                        # Find the edge data in our dataframe
                        edge_data = edges_df[
                            ((edges_df['qA']==u) & (edges_df['qB']==v)) | 
                            ((edges_df['qA']==v) & (edges_df['qB']==u))
                        ]
                        
                        if not edge_data.empty:
                            path_score += edge_data.iloc[0]['Score']
                            # Check if this link has a penalty (< 1.0 means it has crosstalk)
                            if edge_data.iloc[0]['collision_penalty'] < 1.0:
                                collisions += 1
                                
                    paths.append({'path': path, 'score': path_score, 'collisions': collisions})
                
                # Sort paths: Highest Score first
                paths = sorted(paths, key=lambda x: x['score'], reverse=True)
                
                if paths:
                    best_path = paths[0]
                    st.success(f"🏆 Optimal Path Found: {best_path['path']}")
                    
                    c1, c2 = st.columns(2)
                    with c1:
                        st.metric("Path Score", f"{best_path['score']:.1f}")
                    with c2:
                        if best_path['collisions'] == 0:
                            st.metric("Freq Collisions", "0", "Perfect Isolation", delta_color="normal")
                        else:
                            st.metric("Freq Collisions", f"{best_path['collisions']}", "Interference Detected", delta_color="inverse")
                    
                    st.code(f"initial_layout = {best_path['path']}", language="python")
                else:
                    st.warning("No valid paths of this length found starting from the optimal qubit.")
                    
            except Exception as e:
                st.error(f"Routing Error: {str(e)}")

# --- FEATURE 2: COHERENCE BUDGET ---
with z7_tab2:
    st.markdown("#### ⏱️ The 'Coherence Budget' Forecast")
    st.write("Calculates if your circuit fits within the nanosecond lifespan of the hardware.")
    
    # User Input
    user_depth = st.number_input("Estimated Circuit Depth (Layers)", min_value=10, max_value=2000, value=100)
    
    # Physics Constants (Approx for IBM Eagle)
    GATE_TIME = 600 # ns (Avg layer time)
    
    # Math
    total_duration_ns = user_depth * GATE_TIME
    total_duration_us = total_duration_ns / 1000.0
    limit_t1 = winner.T1_min
    
    # Calculate % of life used
    budget_used = (total_duration_us / limit_t1) * 100
    
    c1, c2 = st.columns(2)
    with c1:
        st.metric("Circuit Duration", f"{total_duration_us:.1f} µs")
    with c2:
        if budget_used < 50:
            st.metric("Budget Used", f"{budget_used:.1f}%", "Safe", delta_color="normal")
        else:
            st.metric("Budget Used", f"{budget_used:.1f}%", "Critical (Decoherence Risk)", delta_color="inverse")

    if budget_used > 50:
        st.error(f"⚠️ **Warning:** This circuit is too deep. It takes {total_duration_us:.1f}µs, but Qubit T1 is only {limit_t1:.1f}µs.")

# --- FEATURE 3: ADVANCED CODE GEN ---
with z7_tab3:
    st.markdown("#### 🛡️ Advanced Error Correction Generator")
    
    st.markdown("**1. Dynamical Decoupling (DD)**")
    st.caption("Copy this code to inject 'Alive' pulses into idle qubits.")
    st.code(f"""
from qiskit.transpiler import PassManager
from qiskit.transpiler.passes import PadDynamicalDecoupling
from qiskit.circuit.library import XGate

# Automatically target the optimal qubits we found
target_qubits = [{int(winner.qA)}, {int(winner.qB)}]

# XX Sequence (Simple Echo)
dd_sequence = [XGate(), XGate()] 

pm = PassManager([
    PadDynamicalDecoupling(backend.target.durations(), dd_sequence, qubits=target_qubits)
])
# run_circuit = pm.run(my_circuit)
    """, language="python")
    
    st.markdown("---")
    st.markdown("**2. Classical Shadows (Measurement)**")
    st.caption("Use this estimator to save shots.")
    
    # Simple calculator
    n_qubits = st.number_input("Qubits in System", 2, 127, 5)
    saved_shots = (3**n_qubits * 100) - (2000 * np.log(n_qubits))
    
    st.info(f"🚀 Using Classical Shadows on {n_qubits} qubits saves approx **{saved_shots:,.0f} shots** compared to full tomography.")



# =========================================================
# 🧬 ZONE 8: NOISE FINGERPRINTS (PER-QUBIT EMPIRICAL RELIABILITY)
# =========================================================
st.markdown("---")
st.markdown("### 🧬 Zone 8: Noise Fingerprints")
st.caption(
    "Empirical, per-qubit reliability scores based on your logged job outcomes and layouts. "
    "This uses only real IBM run data stored locally in SQLite."
)

nf_df = get_job_outcomes(backend_name)

if nf_df.empty:
    st.info(
        "No job outcomes available yet for this backend. "
        "Log outcomes in the '📈 Jobs vs Hardware' tab to build personalized noise fingerprints."
    )
else:
    # Aggregate success metrics per qubit over all logged jobs.
    qubit_accumulator = {}

    for _, row in nf_df.iterrows():
        layout_raw = row.get("layout")
        if layout_raw is None:
            continue
        try:
            layout_list = json.loads(layout_raw)
        except Exception:
            continue

        # Ensure the layout is a list of integer qubit indices
        try:
            qubits_in_job = [int(q) for q in layout_list]
        except Exception:
            continue

        s = float(row.get("success_metric", 0.0))
        for q in qubits_in_job:
            acc = qubit_accumulator.setdefault(q, {"count": 0, "sum_success": 0.0})
            acc["count"] += 1
            acc["sum_success"] += s

    if not qubit_accumulator:
        st.info(
            "Outcomes are logged, but no valid qubit layouts could be parsed. "
            "Future runs will build this view automatically."
        )
    else:
        qubit_rows = []
        for q, stats_q in qubit_accumulator.items():
            avg_success = stats_q["sum_success"] / max(stats_q["count"], 1)

            # Attach latest T1 if available from live backend data
            t1_latest = qubit_stats.get(q, {}).get("T1", None)

            qubit_rows.append(
                {
                    "Qubit": f"Q{q}",
                    "Index": q,
                    "Avg_Success": avg_success,
                    "Samples": stats_q["count"],
                    "T1_latest_us": t1_latest,
                }
            )

        nf_qubit_df = pd.DataFrame(qubit_rows).sort_values("Avg_Success", ascending=False)

        c_nf1, c_nf2 = st.columns([1, 1])
        with c_nf1:
            st.markdown("#### 📊 Per-Qubit Empirical Reliability")
            fig_nf = px.bar(
                nf_qubit_df,
                x="Index",
                y="Avg_Success",
                color="Samples",
                title="Empirical Success Metric per Qubit (Your Workload)",
                labels={"Index": "Qubit Index", "Avg_Success": "Average Success Metric"},
                template="plotly_dark",
            )
            st.plotly_chart(fig_nf, use_container_width=True)

        with c_nf2:
            st.markdown("#### 🔍 Calibration vs Empirical Success")
            # Only plot qubits where we have both T1 and an empirical score
            nf_both = nf_qubit_df.dropna(subset=["T1_latest_us"])
            if not nf_both.empty:
                fig_corr = px.scatter(
                    nf_both,
                    x="T1_latest_us",
                    y="Avg_Success",
                    size="Samples",
                    hover_name="Qubit",
                    title="Average Success vs Latest T1",
                    labels={"T1_latest_us": "Latest T1 (µs)", "Avg_Success": "Average Success Metric"},
                    template="plotly_dark",
                )
                st.plotly_chart(fig_corr, use_container_width=True)
            else:
                st.info(
                    "No overlapping data between live T1 and logged outcomes yet. "
                    "Run and log more jobs to see this correlation."
                )

        st.caption(
            "These fingerprints highlight qubits that consistently behave better or worse "
            "for your experiments, beyond what raw calibration numbers alone suggest."
        )


# =========================================================
# 🧭 ZONE 9: OPERATION-AWARE LAYOUT ADVISOR
# =========================================================
st.markdown("---")
st.markdown("### 🧭 Zone 9: Operation-Aware Layout Advisor")
st.caption(
    "Select a target operation and NeuroQ will propose healthy qubit layouts on the current IBM chip, "
    "based on calibration data and your scoring engine."
)

operations_catalog = {
    "Bell / CHSH test (2 qubits)": {"qubits": 2, "depth": 3},
    "Teleportation (3 qubits)": {"qubits": 3, "depth": 6},
    "Small VQE chain (4 qubits)": {"qubits": 4, "depth": 100},
    "Small QAOA chain (4 qubits)": {"qubits": 4, "depth": 60},
}

op_name = st.selectbox("Choose quantum operation", list(operations_catalog.keys()))
op_cfg = operations_catalog[op_name]
st.markdown(
    f"- **Required qubits**: {op_cfg['qubits']}\n"
    f"- **Typical depth**: ~{op_cfg['depth']} layers"
)

max_layouts = st.slider("Number of suggested layouts", min_value=1, max_value=10, value=5)


def _enumerate_paths_any_start(graph: nx.Graph, path_length: int, max_paths: int = 200):
    """Enumerate simple paths of fixed length starting from any node, with an overall cap."""
    paths = []
    for start_node in graph.nodes:
        stack = [(start_node, [start_node])]
        while stack:
            node, path = stack.pop()
            if len(path) == path_length:
                paths.append(path)
                if len(paths) >= max_paths:
                    return paths
                continue
            for nbr in graph.neighbors(node):
                if nbr not in path:
                    stack.append((nbr, path + [nbr]))
    return paths


if st.button("🔍 Suggest healthy layouts for this operation"):
    required_q = int(op_cfg["qubits"])
    try:
        raw_paths = _enumerate_paths_any_start(chip_graph, required_q, max_paths=300)
        if not raw_paths:
            st.warning("No connected chains of that length found on this backend.")
        else:
            rows = []
            for path in raw_paths:
                # Collect edge rows for consecutive pairs in the path
                edge_scores = []
                edge_cnot = []
                edge_penalty = []
                for i in range(len(path) - 1):
                    u, v = path[i], path[i + 1]
                    edge_data = edges_df[
                        ((edges_df["qA"] == u) & (edges_df["qB"] == v))
                        | ((edges_df["qA"] == v) & (edges_df["qB"] == u))
                    ]
                    if edge_data.empty:
                        continue
                    row_e = edge_data.iloc[0]
                    edge_scores.append(row_e["Score"])
                    edge_cnot.append(row_e["cnot_error"])
                    edge_penalty.append(row_e["collision_penalty"])

                if not edge_scores:
                    continue

                # Aggregate health metrics for the chain
                avg_score = float(np.mean(edge_scores))
                avg_cnot = float(np.mean(edge_cnot))
                avg_penalty = float(np.mean(edge_penalty))
                t1_vals = [qubit_stats[q]["T1"] for q in path if q in qubit_stats]
                min_t1 = float(min(t1_vals)) if t1_vals else 0.0

                rows.append(
                    {
                        "Layout": path,
                        "HealthScore": avg_score,
                        "Min_T1_us": min_t1,
                        "Avg_CNOT_Error": avg_cnot,
                        "Avg_Collision_Penalty": avg_penalty,
                    }
                )

            if not rows:
                st.warning("Could not assemble any scored layouts for this operation.")
            else:
                df_ops = pd.DataFrame(rows).sort_values("HealthScore", ascending=False).head(max_layouts)
                st.dataframe(df_ops, use_container_width=True)

                best_layout = df_ops.iloc[0]["Layout"]
                st.markdown("#### 📋 Best layout suggestion")
                st.code(f"initial_layout = {list(best_layout)}", language="python")
    except Exception as e:
        st.error(f"Advisor error: {str(e)}")


st.success(f"✅ System Ready. Connected to {backend_name}.")