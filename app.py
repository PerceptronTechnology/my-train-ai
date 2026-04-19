import streamlit as st
import sqlite3
import requests
import os
import pandas as pd
from datetime import datetime, timezone, timedelta
import google.generativeai as genai

# 設定とAPIキー
st.set_page_config(page_title="マイ乗換案内AI", page_icon="🚇", layout="centered")
ODPT_API_KEY = os.environ.get("ODPT_API_KEY")
genai.configure(api_key=os.environ.get("GEMINI_API_KEY"))
model = genai.GenerativeModel('gemini-2.5-flash')

# JR路線のキーワード
jr_keywords = ["JR", "総武", "山手", "中央", "京浜東北", "横須賀", "東海道", "常磐", "埼京", "京葉", "武蔵野", "南武", "横浜"]
def is_jr_line(title): return any(kw in title for kw in jr_keywords)

# データベース接続
DB_PATH = 'transport_data_v3.db'
def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.execute('CREATE TABLE IF NOT EXISTS manual_timetables (station_name TEXT, railway_name TEXT, direction_name TEXT, departure_time TEXT, destination TEXT, calendar TEXT)')
    return conn

# セッション状態の初期化
if "step" not in st.session_state: st.session_state.step = 1
if "station" not in st.session_state: st.session_state.station = ""
if "railways" not in st.session_state: st.session_state.railways = []
if "selected_railway" not in st.session_state: st.session_state.selected_railway = None
if "has_jr" not in st.session_state: st.session_state.has_jr = False

# --- サイドバー：管理メニューと運行情報 ---
with st.sidebar:
    st.header("⚙️ 管理・ツール")
    
    if st.button("🚨 JR運行情報を確認"):
        with st.spinner("情報取得中..."):
            try:
                res = requests.get("https://api.odpt.org/api/v4/odpt:TrainInformation", params={"acl:consumerKey": ODPT_API_KEY, "odpt:operator": "odpt.Operator:JR-East"})
                delayed = [f"・{info.get('odpt:railway', '').split('.')[-1]}: {info.get('odpt:trainInformationText', {}).get('ja', '')}" for info in res.json() if "平常" not in info.get('odpt:trainInformationText', {}).get('ja', '')]
                summary = "\n".join(delayed) if delayed else "全線平常運転です。"
                ai_res = model.generate_content(f"JR運行状況を駅員風に：{summary}").text
                st.success(ai_res)
            except:
                st.error("情報取得エラー")

    st.divider()
    st.subheader("📝 マイ時刻表 手動登録")
    with st.form("manual_add"):
        m_st = st.text_input("駅名 (例: 錦糸町)")
        m_rw = st.text_input("路線名 (例: 総武快速線)")
        m_dr = st.text_input("方面 (例: 東京)")
        m_tm = st.text_input("発車時刻 (例: 08:15)")
        m_ds = st.text_input("行き先 (例: 逗子)")
        m_cal = st.selectbox("区分", ["平日 (Weekday)", "土休日 (SaturdayHoliday)"])
        if st.form_submit_button("登録する"):
            if m_st and m_tm:
                cal_str = "Weekday" if "Weekday" in m_cal else "SaturdayHoliday"
                conn = get_db()
                conn.execute('INSERT INTO manual_timetables VALUES (?, ?, ?, ?, ?, ?)', (m_st.replace("駅",""), m_rw, m_dr.replace("方面",""), m_tm, m_ds, cal_str))
                conn.commit()
                conn.close()
                st.success("登録完了しました！")

# --- メイン画面：検索UI ---
st.title("🚇 マイ乗換案内 AI")

# ステップ1：駅名入力
if st.session_state.step == 1:
    station_input = st.text_input("駅名を入力してください", placeholder="例: 新橋, 錦糸町")
    if st.button("路線を検索", type="primary"):
        if station_input:
            st.session_state.station = station_input.replace("駅", "")
            conn = get_db()
            cur = conn.cursor()
            rw_list = []
            seen = set()
            st.session_state.has_jr = False
            
            cur.execute("SELECT DISTINCT railway_title, station_id FROM stations WHERE name = ?", (st.session_state.station,))
            for row in cur.fetchall():
                rw_list.append({"title": row[0], "id": row[1], "source": "api"})
                seen.add(row[0])
                if is_jr_line(row[0]): st.session_state.has_jr = True
                
            cur.execute("SELECT DISTINCT railway_name FROM manual_timetables WHERE station_name = ?", (st.session_state.station,))
            for row in cur.fetchall():
                if row[0] not in seen:
                    rw_list.append({"title": row[0], "id": None, "source": "manual"})
                    seen.add(row[0])
                    if is_jr_line(row[0]): st.session_state.has_jr = True
            conn.close()
            
            if rw_list:
                st.session_state.railways = rw_list
                st.session_state.step = 2
                st.rerun()
            else:
                st.error("駅が見つかりませんでした。")

# ステップ2：路線選択
elif st.session_state.step == 2:
    st.subheader(f"📍 {st.session_state.station}駅 - 路線を選択")
    if st.session_state.has_jr: st.info("👈 左のサイドバーからJR遅延情報が確認できます！")
    
    for rw in st.session_state.railways:
        mark = "🌐(API)" if rw["source"] == "api" else "📝(マイ時刻表)"
        if st.button(f"{rw['title']} {mark}", use_container_width=True):
            st.session_state.selected_railway = rw
            st.session_state.step = 3
            st.rerun()
            
    if st.button("⬅️ 駅を検索し直す"):
        st.session_state.step = 1
        st.rerun()

# ステップ3：方面選択と時刻取得
elif st.session_state.step == 3:
    rw = st.session_state.selected_railway
    st.subheader(f"📍 {st.session_state.station}駅 - {rw['title']} 方面を選択")
    
    JST = timezone(timedelta(hours=+9), 'JST')
    now = datetime.now(JST)
    is_weekend = now.weekday() >= 5
    cal_type = 'SaturdayHoliday' if is_weekend else 'Weekday'
    
    conn = get_db()
    dirs = []
    
    if rw["source"] == "api":
        res = requests.get("https://api.odpt.org/api/v4/odpt:StationTimetable", params={"acl:consumerKey": ODPT_API_KEY, "odpt:station": rw["id"]}).json()
        seen_dirs = set()
        for tt in res:
            cal = tt.get("odpt:calendar", "")
            if (is_weekend and "Weekday" in cal) or (not is_weekend and "SaturdayHoliday" in cal): continue
            dir_id = tt.get("odpt:railDirection")
            if dir_id in seen_dirs: continue
            conn.execute("SELECT name FROM directions WHERE direction_id = ?", (dir_id,))
            dir_n = conn.execute("SELECT name FROM directions WHERE direction_id = ?", (dir_id,)).fetchone()
            dirs.append({"name": dir_n[0] if dir_n else dir_id.split(":")[-1], "data": tt})
            seen_dirs.add(dir_id)
    else:
        for row in conn.execute("SELECT DISTINCT direction_name FROM manual_timetables WHERE station_name=? AND railway_name=? AND calendar=?", (st.session_state.station, rw["title"], cal_type)).fetchall():
            dirs.append({"name": row[0], "data": None})
    conn.close()

    if not dirs: st.warning("この曜日の時刻表データがありません。")

    for d in dirs:
        if st.button(f"🚋 {d['name']} 方面", use_container_width=True):
            now_str = now.strftime("%H:%M")
            next_trains = []
            if rw["source"] == "api":
                for t in d["data"].get("odpt:stationTimetableObject", []):
                    dep = t.get("odpt:departureTime")
                    if dep and dep >= now_str:
                        dest = t.get("odpt:destinationStation", [""])[0].split(".")[-1]
                        next_trains.append({"time": dep, "destination": dest})
                        if len(next_trains) >= 3: break
            else:
                conn = get_db()
                for row in conn.execute("SELECT departure_time, destination FROM manual_timetables WHERE station_name=? AND railway_name=? AND direction_name=? AND calendar=? AND departure_time>=? ORDER BY departure_time ASC LIMIT 3", (st.session_state.station, rw["title"], d["name"], cal_type, now_str)).fetchall():
                    next_trains.append({"time": row[0], "destination": row[1]})
                conn.close()
                
            with st.spinner("AIが案内を作成中..."):
                ans = model.generate_content(f"{st.session_state.station}駅 {rw['title']} {d['name']}方面。現在{now_str}。データ:{next_trains}。駅員風に案内して。").text
                st.success(ans)

    if st.button("⬅️ 路線を選び直す"):
        st.session_state.step = 2
        st.rerun()
