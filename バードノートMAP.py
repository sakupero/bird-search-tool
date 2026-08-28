import subprocess
import sys
import os

# --- 起動時に自動でStreamlitを立ち上げるラッパー処理 ---
if __name__ == "__main__":
    if "streamlit" not in sys.modules and os.environ.get("STREAMLIT_RUN") != "1":
        os.environ["STREAMLIT_RUN"] = "1"
        print("Streamlitサーバーを自動起動しています...")
        subprocess.run([sys.executable, "-m", "streamlit", "run", sys.argv[0]])
        sys.exit(0)

import asyncio
import aiohttp
import pandas as pd
import folium
from folium.plugins import HeatMap
import streamlit as st
from streamlit_folium import st_folium
import time

@st.cache_data
def load_mesh_codes(filename="mesh1.csv"):
    df = pd.read_csv(filename)
    return df["1次メッシュ"].astype(str).tolist()

@st.cache_data
def load_bird_list(filename="bird_note_all_list.csv"):
    return pd.read_csv(filename)

async def fetch_mesh_bird(session, url, mesh_code, bird_name, headers, semaphore, progress_callback):
    params = {"meshCode": mesh_code, "birdName": bird_name}
    async with semaphore:
        try:
            await asyncio.sleep(0.02)
            async with session.get(url, params=params, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get("result", [])
                    points_data = []
                    if results:
                        for item in results:
                            if item.get("lat") and item.get("lng"):
                                points_data.append({
                                    "lat": float(item["lat"]),
                                    "lng": float(item["lng"]),
                                    "type": item.get("type", "area"),
                                    "target": str(item.get("target", ""))
                                })
                    progress_callback()
                    return mesh_code, len(points_data), points_data
        except Exception:
            pass
        progress_callback()
        return mesh_code, 0, []

async def scan_bird_distribution(bird_names, mesh_codes, progress_bar, status_text):
    url = "https://api.bird-research.jp/points.json"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Referer": "https://bird-research.jp/"
    }

    semaphore = asyncio.Semaphore(15)
    all_points = []
    hit_count = 0
    
    total_steps = len(bird_names) * len(mesh_codes)
    completed_steps = 0

    def update_progress():
        nonlocal completed_steps
        completed_steps += 1
        progress = min(completed_steps / total_steps, 1.0)
        progress_bar.progress(progress)
        status_text.text(f"分布スキャン中: {completed_steps} / {total_steps} メッシュ完了")

    async with aiohttp.ClientSession() as session:
        for bird in bird_names:
            tasks = [fetch_mesh_bird(session, url, code, bird, headers, semaphore, update_progress) for code in mesh_codes]
            results = await asyncio.gather(*tasks)

            for code, count, points in results:
                if count > 0:
                    hit_count += 1
                    all_points.extend(points)

    return all_points, hit_count

async def fetch_site_seasons(session, site_type, target_id, target_species, headers, semaphore):
    if not target_id:
        return []
    
    if site_type == "common_site":
        url = f"https://api.bird-research.jp/commonsites/{target_id}/annualoccurrences.json"
    else:
        url = f"https://api.bird-research.jp/areas/{target_id}/observinglist.json"

    async with semaphore:
        try:
            await asyncio.sleep(0.03)
            async with session.get(url, headers=headers, timeout=10) as response:
                if response.status == 200:
                    data = await response.json()
                    results = data.get("result", [])
                    months = set()
                    
                    if site_type == "common_site":
                        month_mapping = {
                            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
                            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12
                        }
                        for row in results:
                            b_name = str(row.get("birdName", "")).strip()
                            if any(sp == b_name for sp in target_species):
                                for m_key, m_num in month_mapping.items():
                                    val = row.get(m_key)
                                    if val == "1" or val == 1:
                                        months.add(m_num)
                    else:
                        for row in results:
                            b_name = str(row.get("birdName", "")).strip()
                            date_str = str(row.get("date", "")).strip()
                            if any(sp == b_name for sp in target_species) and len(date_str) >= 7:
                                try:
                                    month_part = date_str.split("-")[1]
                                    month = int(month_part)
                                    if 1 <= month <= 12:
                                        months.add(month)
                                except (ValueError, IndexError):
                                    pass
                    return list(months)
        except Exception:
            pass
        return []

async def fetch_all_seasons(unique_sites, target_species, progress_bar, status_text):
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
        "Referer": "https://bird-research.jp/"
    }
    semaphore = asyncio.Semaphore(10)
    total = len(unique_sites)
    completed = 0
    site_months_map = {}

    async with aiohttp.ClientSession() as session:
        tasks = []
        for site in unique_sites:
            site_type = site["type"]
            target_id = site["target"]

            async def bound_fetch(stype=site_type, tid=target_id):
                nonlocal completed
                months = await fetch_site_seasons(session, stype, tid, target_species, headers, semaphore)
                completed += 1
                progress_bar.progress(min(completed / total, 1.0))
                status_text.text(f"季節情報取得中: {completed} / {total} 地点完了")
                return (stype, tid), months

            tasks.append(bound_fetch())
        
        results = await asyncio.gather(*tasks)
        for (stype, tid), months in results:
            site_months_map[(stype, tid)] = months

    return site_months_map

def main():
    st.set_page_config(page_title="鳥類分布・季節検索ツール", layout="centered")
    st.title("鳥類分布・季節ヒートマップ検索ツール")

    try:
        bird_df = load_bird_list("bird_note_all_list.csv")
        mesh_codes = load_mesh_codes("mesh1.csv")
    except Exception as e:
        st.error(f"CSVファイルの読み込みに失敗しました: {e}")
        return

    query = st.text_input("検索文字（種名・属・科・目）を入力:", "")

    matched_items = []
    if query:
        q = query.strip()
        mask = (
            bird_df["order"].str.contains(q, na=False) |
            bird_df["family"].str.contains(q, na=False) |
            bird_df["genus"].str.contains(q, na=False) |
            bird_df["species_name"].str.contains(q, na=False)
        )
        filtered_df = bird_df[mask]

        for _, row in filtered_df.iterrows():
            s_name = str(row["species_name"])
            g_name = str(row["genus"])
            f_name = str(row["family"])
            o_name = str(row["order"])

            if q in s_name:
                matched_items.append(f"[種名] {s_name} (属: {g_name} / 科: {f_name})")
            elif q in g_name:
                matched_items.append(f"[属名] {g_name} (科: {f_name} / 目: {o_name})")
            elif q in f_name:
                matched_items.append(f"[科名] {f_name} (目: {o_name})")
            elif q in o_name:
                matched_items.append(f"[目名] {o_name}")

        matched_items = sorted(list(set(matched_items)))

    selected_item = st.selectbox("候補選択:", options=matched_items if matched_items else ["検索結果なし"])

    if st.button("実行してマップ生成"):
        if not query or not matched_items or selected_item == "検索結果なし":
            st.warning("有効な項目を選択してください。")
            return

        category_type = selected_item.split("]")[0].replace("[", "")
        target_text = selected_item.split("] ")[1].split(" (")[0]

        target_species = []
        if category_type == "種名":
            target_species = [target_text]
        elif category_type == "属名":
            target_species = bird_df[bird_df["genus"] == target_text]["species_name"].dropna().unique().tolist()
        elif category_type == "科名":
            target_species = bird_df[bird_df["family"] == target_text]["species_name"].dropna().unique().tolist()
        elif category_type == "目名":
            target_species = bird_df[bird_df["order"] == target_text]["species_name"].dropna().unique().tolist()

        st.info(f"検索開始: {category_type} '{target_text}' (対象種数: {len(target_species)}件)")

        progress_bar = st.progress(0)
        status_text = st.empty()

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        all_points, hit_count = loop.run_until_complete(
            scan_bird_distribution(target_species, mesh_codes, progress_bar, status_text)
        )

        status_text.text(f"スキャン完了！ ヒットメッシュ数: {hit_count} / 総データポイント数: {len(all_points)} 件")

        if all_points:
            st.session_state["all_points"] = all_points
            st.session_state["target_species"] = target_species
            st.session_state["site_months_map"] = None
        else:
            st.warning("該当するデータが見つかりませんでした。")
            return

    if "all_points" in st.session_state and st.session_state["all_points"]:
        all_points = st.session_state["all_points"]
        target_species = st.session_state["target_species"]

        st.divider()
        st.subheader("🗺️ 分布ヒートマップ")
        
        m = folium.Map(location=[38.0, 137.0], zoom_start=5)
        coords = [[p["lat"], p["lng"]] for p in all_points]
        HeatMap(coords, radius=10, blur=6, max_zoom=1).add_to(m)
        
        st_folium(m, use_container_width=True, height=600)

        st.divider()
        st.subheader("🌸 季節情報（月別フィルター）の取得")
        
        unique_sites = list({(p["type"], p["target"]): p for p in all_points if p["target"]}.values())
        est_seconds = int(len(unique_sites) * 0.04)
        st.info(f"💡 検出されたユニーク地点数: **{len(unique_sites)} 地点**\n\n各地点の季節情報APIにアクセスします。予想所要時間: **約 {est_seconds} 秒**")

        if st.button("季節情報を取得する"):
            progress_bar_season = st.progress(0)
            status_text_season = st.empty()

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            site_months_map = loop.run_until_complete(
                fetch_all_seasons(unique_sites, target_species, progress_bar_season, status_text_season)
            )
            st.session_state["site_months_map"] = site_months_map
            status_text_season.text("すべての季節情報の取得が完了しました！")
            time.sleep(1)
            st.rerun()

        if st.session_state.get("site_months_map"):
            st.success("✨ 季節情報がロードされました。表示する月の範囲を選択してください。")
            
            # 開始と終了のスライダーの色を個別に変えるCSS
            st.markdown("""
                <style>
                    /* 開始スライダー（1本目）を青系に */
                    div[data-testid="stSlider"]:nth-of-type(1) div[data-baseweb="slider"] div[role="slider"] {
                        background-color: #2b5c8f !important;
                        border-color: #2b5c8f !important;
                    }
                    div[data-testid="stSlider"]:nth-of-type(1) div[data-baseweb="slider"] div.st-bp {
                        background-color: #2b5c8f !important;
                    }
                    /* 終了スライダー（2本目）を赤・オレンジ系に */
                    div[data-testid="stSlider"]:nth-of-type(2) div[data-baseweb="slider"] div[role="slider"] {
                        background-color: #d9534f !important;
                        border-color: #d9534f !important;
                    }
                    div[data-testid="stSlider"]:nth-of-type(2) div[data-baseweb="slider"] div.st-bp {
                        background-color: #d9534f !important;
                    }
                </style>
            """, unsafe_allow_html=True)

            col1, col2 = st.columns(2)
            with col1:
                start_month = st.slider("🔵 開始月", min_value=1, max_value=12, value=10, format="%d月")
            with col2:
                end_month = st.slider("🔴 終了月", min_value=1, max_value=12, value=3, format="%d月")

            # 左右交差（年またぎ）の判定：終了が開始より左（または数値が小さい）なら12月をまたぐ
            if start_month <= end_month:
                selected_months = list(range(start_month, end_month + 1))
            else:
                selected_months = list(range(start_month, 13)) + list(range(1, end_month + 1))

            st.write(f"選択中の月: **{', '.join(map(str, selected_months))} 月**")

            filtered_coords = []
            all_points = st.session_state.get("all_points", [])
            site_months_map = st.session_state["site_months_map"]
            for p in all_points:
                key = (p["type"], p["target"])
                p_months = site_months_map.get(key, [])
                if any(m in selected_months for m in p_months):
                    filtered_coords.append([p["lat"], p["lng"]])

            st.write(f"条件に一致したポイント数: **{len(filtered_coords)} / {len(all_points)} 件**")

            m = folium.Map(location=[38.0, 137.0], zoom_start=5)
            if filtered_coords:
                HeatMap(filtered_coords, radius=10, blur=6, max_zoom=1).add_to(m)
            
            st_folium(m, use_container_width=True, height=600)


if __name__ == "__main__":
    main()