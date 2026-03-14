# app.py
# Streamlit: YouTube watch-history.json / URL入力 から
# 3x3 タイル画像を生成するアプリ
#
# 機能:
# - 全体タブ:
#     視聴履歴全体から累積 Top9 動画を 3x3 表示
# - チャンネル別タブ:
#     指定チャンネル内だけで累積 Top9 動画を 3x3 表示
# - URL入力タブ:
#     YouTube URLを改行区切りで入力し、サムネだけの 3x3 を生成
#
# 修正版ポイント:
# - Streamlit キャッシュは使わない
# - URL入力タブの生成結果は session_state で保持
# - ダウンロード用ファイル名を安全化
# - 文言を少し調整
#
# 起動:
#   streamlit run app.py

from __future__ import annotations

import io
import json
import re
from datetime import date
from typing import Optional

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageDraw, ImageFont


# =========================
# 基本設定
# =========================
st.set_page_config(
    page_title="YouTube視聴履歴 タイル画像メーカー",
    page_icon="📺",
    layout="wide",
)

TITLE_SUFFIX = " を視聴しました"
THUMB_TIMEOUT = 8

# デフォルトテーマ
DEFAULT_THEME = {
    "bg_color": (248, 248, 248),
    "text_color": (40, 40, 40),
    "subtext_color": (110, 110, 110),
    "card_bg": (255, 255, 255),
    "card_border": (225, 225, 225),
    "text_bg": (255, 255, 255),
    "placeholder_bg": (236, 236, 236),
}

# チャンネル別テーマ例
CHANNEL_THEMES = {
    "HikakinTV": {
        "bg_color": (241, 247, 255),
        "text_color": (28, 45, 72),
        "subtext_color": (88, 110, 145),
        "card_bg": (255, 255, 255),
        "card_border": (205, 223, 245),
        "text_bg": (255, 255, 255),
        "placeholder_bg": (228, 238, 250),
    },
    "Ado": {
        "bg_color": (24, 24, 34),
        "text_color": (242, 242, 248),
        "subtext_color": (176, 176, 196),
        "card_bg": (37, 37, 50),
        "card_border": (66, 66, 88),
        "text_bg": (37, 37, 50),
        "placeholder_bg": (54, 54, 70),
    },
    "SixTONES": {
        "bg_color": (245, 242, 238),
        "text_color": (54, 44, 40),
        "subtext_color": (126, 106, 98),
        "card_bg": (255, 252, 249),
        "card_border": (228, 214, 206),
        "text_bg": (255, 252, 249),
        "placeholder_bg": (239, 231, 225),
    },
}

# 画像サイズ
IMAGE_W = 1200
HEADER_H = 150
FOOTER_H = 36
OUTER_PAD_X = 36
OUTER_PAD_Y = 28
GRID_GAP = 22

COLS = 3
ROWS = 3

TILE_W = (IMAGE_W - OUTER_PAD_X * 2 - GRID_GAP * (COLS - 1)) // COLS
THUMB_RATIO = 16 / 9
THUMB_H = int(TILE_W / THUMB_RATIO)
TEXT_AREA_H = 96
TILE_H = THUMB_H + TEXT_AREA_H

# URL入力タブ用（サムネだけ）
THUMB_ONLY_HEADER_H = 130
THUMB_ONLY_FOOTER_H = 30
THUMB_ONLY_TILE_H = THUMB_H
THUMB_ONLY_IMAGE_H = (
    OUTER_PAD_Y * 2
    + THUMB_ONLY_HEADER_H
    + ROWS * THUMB_ONLY_TILE_H
    + (ROWS - 1) * GRID_GAP
    + THUMB_ONLY_FOOTER_H
)


# =========================
# フォント
# =========================
def load_font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        "/usr/share/fonts/opentype/ipaexfont-gothic/ipaexg.ttf",
        "/usr/share/fonts/truetype/ipaexg.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJKJP-Regular.otf",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/System/Library/Fonts/ヒラギノ角ゴシック W3.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "C:/Windows/Fonts/msgothic.ttc",
        "C:/Windows/Fonts/meiryo.ttc",
        "C:/Windows/Fonts/YuGothM.ttc",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


FONT_HEADER = load_font(34)
FONT_SUB = load_font(18)
FONT_TITLE = load_font(18)
FONT_CHANNEL = load_font(15)
FONT_FOOTER = load_font(12)
FONT_PH = load_font(18)
FONT_PH_SMALL = load_font(12)
FONT_URL_HEADER = load_font(30)


# =========================
# ユーティリティ
# =========================
def safe_filename(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "_", text).strip()


# =========================
# データ処理
# =========================
def extract_video_id(url: Optional[str]) -> Optional[str]:
    if not url or not isinstance(url, str):
        return None

    patterns = [
        r"[?&]v=([A-Za-z0-9_-]{11})",
        r"youtu\.be/([A-Za-z0-9_-]{11})",
        r"/shorts/([A-Za-z0-9_-]{11})",
        r"/live/([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        m = re.search(pattern, url)
        if m:
            return m.group(1)
    return None


def extract_channel_name(subtitles) -> Optional[str]:
    if isinstance(subtitles, list) and len(subtitles) > 0 and isinstance(subtitles[0], dict):
        return subtitles[0].get("name")
    return None


def clean_title(title: Optional[str]) -> str:
    if not title:
        return "タイトル不明"
    return str(title).replace(TITLE_SUFFIX, "").strip()


def load_watch_history(uploaded_file) -> pd.DataFrame:
    raw = json.load(uploaded_file)
    df = pd.DataFrame(raw)

    if df.empty:
        return df

    if "titleUrl" not in df.columns:
        df["titleUrl"] = None
    if "title" not in df.columns:
        df["title"] = None
    if "time" not in df.columns:
        df["time"] = None
    if "subtitles" not in df.columns:
        df["subtitles"] = None

    df["video_id"] = df["titleUrl"].apply(extract_video_id)
    df["title_clean"] = df["title"].apply(clean_title)
    df["channel_name"] = df["subtitles"].apply(extract_channel_name)
    df["time_utc"] = pd.to_datetime(df["time"], utc=True, errors="coerce")
    df["time_jst"] = df["time_utc"].dt.tz_convert("Asia/Tokyo")

    df = df.dropna(subset=["video_id", "time_jst"]).copy()
    return df


def apply_date_filter(df: pd.DataFrame, start_date: date, end_date: date) -> pd.DataFrame:
    dates = df["time_jst"].dt.date
    return df[(dates >= start_date) & (dates <= end_date)].copy()


def build_top_videos(df: pd.DataFrame, top_n: int = 9) -> pd.DataFrame:
    """
    「自分を構成している動画」なので累積回数ベース。
    同率時は latest_seen_jst が新しい方を上位。
    """
    if df.empty:
        return pd.DataFrame(columns=[
            "video_id", "watch_count", "latest_seen_jst",
            "title", "channel_name", "thumbnail_url"
        ])

    agg = (
        df.groupby("video_id")
        .agg(
            watch_count=("video_id", "size"),
            latest_seen_jst=("time_jst", "max"),
        )
        .reset_index()
    )

    latest_meta_idx = df.groupby("video_id")["time_jst"].idxmax()
    meta = df.loc[latest_meta_idx, ["video_id", "title_clean", "channel_name"]].copy()
    meta = meta.rename(columns={"title_clean": "title"})

    out = agg.merge(meta, on="video_id", how="left")
    out["channel_name"] = out["channel_name"].fillna("チャンネル不明")
    out["thumbnail_url"] = out["video_id"].apply(
        lambda vid: f"https://i.ytimg.com/vi/{vid}/hqdefault.jpg"
    )

    out = out.sort_values(
        ["watch_count", "latest_seen_jst"],
        ascending=[False, False]
    ).reset_index(drop=True)

    return out.head(top_n).copy()


def build_manual_thumbnail_df(text: str, max_n: int = 9) -> pd.DataFrame:
    """
    URL入力タブ用。
    改行区切りのYouTube URLから video_id を抽出し、
    サムネだけ表示するための DataFrame を作る。
    """
    rows = []
    seen = set()

    for line in text.splitlines():
        url = line.strip()
        if not url:
            continue

        video_id = extract_video_id(url)
        if not video_id:
            continue
        if video_id in seen:
            continue

        seen.add(video_id)
        rows.append({
            "video_id": video_id,
            "thumbnail_url": f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg",
        })

        if len(rows) >= max_n:
            break

    return pd.DataFrame(rows)


# =========================
# テーマ
# =========================
def resolve_theme(mode: str, selected_channel: Optional[str] = None) -> dict:
    if mode == "デフォルト":
        return DEFAULT_THEME

    if mode == "自動":
        if selected_channel:
            return CHANNEL_THEMES.get(selected_channel, DEFAULT_THEME)
        return DEFAULT_THEME

    return DEFAULT_THEME


# =========================
# サムネイル取得
# =========================
def fetch_thumbnail(url: str) -> Optional[Image.Image]:
    try:
        resp = requests.get(url, timeout=THUMB_TIMEOUT)
        resp.raise_for_status()
        img = Image.open(io.BytesIO(resp.content)).convert("RGB")
        return img
    except Exception:
        return None


def fit_and_crop(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    src_w, src_h = img.size
    src_ratio = src_w / src_h
    dst_ratio = target_w / target_h

    if src_ratio > dst_ratio:
        new_h = target_h
        new_w = int(new_h * src_ratio)
    else:
        new_w = target_w
        new_h = int(new_w / src_ratio)

    img = img.resize((new_w, new_h), Image.LANCZOS)

    left = max((new_w - target_w) // 2, 0)
    top = max((new_h - target_h) // 2, 0)

    return img.crop((left, top, left + target_w, top + target_h))


def make_placeholder_thumb(video_id: str, width: int, height: int, theme: dict) -> Image.Image:
    img = Image.new("RGB", (width, height), theme["placeholder_bg"])
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, width - 1, height - 1), outline=theme["card_border"], width=1)
    draw.text((20, height // 2 - 22), "No Thumbnail", fill=theme["subtext_color"], font=FONT_PH)
    draw.text((20, height // 2 + 10), video_id, fill=theme["subtext_color"], font=FONT_PH_SMALL)
    return img


# =========================
# テキスト描画
# =========================
def wrap_text(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    chars = list(text)
    lines = []
    current = ""

    for ch in chars:
        trial = current + ch
        bbox = draw.textbbox((0, 0), trial, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = ch
            if len(lines) >= max_lines - 1:
                break

    if current and len(lines) < max_lines:
        lines.append(current)

    if len(lines) == max_lines and "".join(lines) != text:
        last = lines[-1]
        while last:
            trial = last + "…"
            bbox = draw.textbbox((0, 0), trial, font=font)
            width = bbox[2] - bbox[0]
            if width <= max_width:
                lines[-1] = trial
                break
            last = last[:-1]
        if not last:
            lines[-1] = "…"

    return lines


# =========================
# 履歴ベース画像生成
# =========================
def draw_tile(base_img: Image.Image, row: pd.Series, x: int, y: int, theme: dict):
    draw = ImageDraw.Draw(base_img)

    draw.rounded_rectangle(
        (x, y, x + TILE_W, y + TILE_H),
        radius=18,
        fill=theme["card_bg"],
        outline=theme["card_border"],
        width=1,
    )

    thumb = fetch_thumbnail(row["thumbnail_url"])
    if thumb is None:
        thumb = make_placeholder_thumb(row["video_id"], TILE_W, THUMB_H, theme)
    else:
        thumb = fit_and_crop(thumb, TILE_W, THUMB_H)

    base_img.paste(thumb, (x, y))

    text_y = y + THUMB_H
    draw.rounded_rectangle(
        (x, text_y, x + TILE_W, y + TILE_H),
        radius=18,
        fill=theme["text_bg"],
        outline=None,
    )
    draw.rectangle((x, text_y, x + TILE_W, text_y + 20), fill=theme["text_bg"])

    pad_x = 14
    title_max_w = TILE_W - pad_x * 2

    title = str(row["title"])
    channel_name = str(row["channel_name"])

    title_lines = wrap_text(draw, title, FONT_TITLE, title_max_w, max_lines=2)

    current_y = text_y + 12
    for line in title_lines:
        draw.text((x + pad_x, current_y), line, fill=theme["text_color"], font=FONT_TITLE)
        current_y += 24

    channel_text = f"by {channel_name}"
    channel_lines = wrap_text(draw, channel_text, FONT_CHANNEL, title_max_w, max_lines=1)
    draw.text((x + pad_x, y + TILE_H - 28), channel_lines[0], fill=theme["subtext_color"], font=FONT_CHANNEL)


def generate_tile_image(top_df: pd.DataFrame, image_title: str, subtitle: str, theme: dict) -> Image.Image:
    body_h = ROWS * TILE_H + (ROWS - 1) * GRID_GAP
    image_h = OUTER_PAD_Y * 2 + HEADER_H + body_h + FOOTER_H

    img = Image.new("RGB", (IMAGE_W, image_h), theme["bg_color"])
    draw = ImageDraw.Draw(img)

    draw.text((OUTER_PAD_X, 28), image_title, fill=theme["text_color"], font=FONT_HEADER)
    draw.text((OUTER_PAD_X, 74), subtitle, fill=theme["subtext_color"], font=FONT_SUB)
    draw.line((OUTER_PAD_X, 112, IMAGE_W - OUTER_PAD_X, 112), fill=theme["card_border"], width=2)

    start_y = OUTER_PAD_Y + HEADER_H
    for idx, (_, row) in enumerate(top_df.iterrows()):
        r = idx // COLS
        c = idx % COLS
        x = OUTER_PAD_X + c * (TILE_W + GRID_GAP)
        y = start_y + r * (TILE_H + GRID_GAP)
        draw_tile(img, row, x, y, theme)

    total_slots = ROWS * COLS
    for idx in range(len(top_df), total_slots):
        r = idx // COLS
        c = idx % COLS
        x = OUTER_PAD_X + c * (TILE_W + GRID_GAP)
        y = start_y + r * (TILE_H + GRID_GAP)

        draw.rounded_rectangle(
            (x, y, x + TILE_W, y + TILE_H),
            radius=18,
            fill=theme["bg_color"],
            outline=theme["card_border"],
            width=1,
        )

    footer = "Generated from Google Takeout watch-history.json / No external YouTube API used"
    draw.text((OUTER_PAD_X, image_h - 22), footer, fill=theme["subtext_color"], font=FONT_FOOTER)

    return img


# =========================
# URL入力用 画像生成（サムネだけ）
# =========================
def draw_thumbnail_only_tile(base_img: Image.Image, row: pd.Series, x: int, y: int, theme: dict):
    draw = ImageDraw.Draw(base_img)

    draw.rounded_rectangle(
        (x, y, x + TILE_W, y + THUMB_ONLY_TILE_H),
        radius=18,
        fill=theme["card_bg"],
        outline=theme["card_border"],
        width=1,
    )

    thumb = fetch_thumbnail(row["thumbnail_url"])
    if thumb is None:
        thumb = make_placeholder_thumb(row["video_id"], TILE_W, THUMB_ONLY_TILE_H, theme)
    else:
        thumb = fit_and_crop(thumb, TILE_W, THUMB_ONLY_TILE_H)

    base_img.paste(thumb, (x, y))


def generate_thumbnail_only_image(thumbnail_df: pd.DataFrame, image_title: str, subtitle: str, theme: dict) -> Image.Image:
    img = Image.new("RGB", (IMAGE_W, THUMB_ONLY_IMAGE_H), theme["bg_color"])
    draw = ImageDraw.Draw(img)

    draw.text((OUTER_PAD_X, 28), image_title, fill=theme["text_color"], font=FONT_URL_HEADER)
    draw.text((OUTER_PAD_X, 68), subtitle, fill=theme["subtext_color"], font=FONT_SUB)
    draw.line((OUTER_PAD_X, 102, IMAGE_W - OUTER_PAD_X, 102), fill=theme["card_border"], width=2)

    start_y = OUTER_PAD_Y + THUMB_ONLY_HEADER_H
    for idx, (_, row) in enumerate(thumbnail_df.iterrows()):
        r = idx // COLS
        c = idx % COLS
        x = OUTER_PAD_X + c * (TILE_W + GRID_GAP)
        y = start_y + r * (THUMB_ONLY_TILE_H + GRID_GAP)
        draw_thumbnail_only_tile(img, row, x, y, theme)

    total_slots = ROWS * COLS
    for idx in range(len(thumbnail_df), total_slots):
        r = idx // COLS
        c = idx % COLS
        x = OUTER_PAD_X + c * (TILE_W + GRID_GAP)
        y = start_y + r * (THUMB_ONLY_TILE_H + GRID_GAP)

        draw.rounded_rectangle(
            (x, y, x + TILE_W, y + THUMB_ONLY_TILE_H),
            radius=18,
            fill=theme["bg_color"],
            outline=theme["card_border"],
            width=1,
        )

    footer = "Generated from manually entered YouTube URLs / Thumbnail-only mode"
    draw.text((OUTER_PAD_X, THUMB_ONLY_IMAGE_H - 18), footer, fill=theme["subtext_color"], font=FONT_FOOTER)

    return img


def pil_image_to_png_bytes(img: Image.Image) -> bytes:
    bio = io.BytesIO()
    img.save(bio, format="PNG")
    return bio.getvalue()


# =========================
# 共通描画
# =========================
def render_result_block(
    df_source: pd.DataFrame,
    image_title: str,
    subtitle: str,
    theme: dict,
    download_filename: str,
    empty_message: str,
):
    if df_source.empty:
        st.warning(empty_message)
        return

    top_df = build_top_videos(df_source, top_n=9)

    with st.spinner("画像生成中..."):
        summary_image = generate_tile_image(
            top_df=top_df,
            image_title=image_title,
            subtitle=subtitle,
            theme=theme,
        )
        png_bytes = pil_image_to_png_bytes(summary_image)

    col1, col2, col3 = st.columns(3)
    col1.metric("総視聴履歴数", f"{len(df_source):,}")
    col2.metric("ユニーク動画数", f"{df_source['video_id'].nunique():,}")
    col3.metric("画像タイル数", f"{len(top_df):,}/9")

    st.markdown("---")
    st.subheader("生成画像プレビュー")
    st.image(summary_image, use_container_width=True)

    st.download_button(
        label="PNGをダウンロード",
        data=png_bytes,
        file_name=download_filename,
        mime="image/png",
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("選ばれた9本")

    preview_cols = st.columns(3)
    for idx, row in enumerate(top_df.itertuples(index=False)):
        with preview_cols[idx % 3]:
            st.image(row.thumbnail_url, use_container_width=True)
            st.markdown(f"**{row.title}**")
            st.caption(f"{row.channel_name}")


def render_thumbnail_only_block(
    thumbnail_df: pd.DataFrame,
    image_title: str,
    subtitle: str,
    theme: dict,
    download_filename: str,
):
    if thumbnail_df.empty:
        st.warning("有効なYouTube URLが見つかりませんでした。")
        return

    with st.spinner("画像生成中..."):
        summary_image = generate_thumbnail_only_image(
            thumbnail_df=thumbnail_df,
            image_title=image_title,
            subtitle=subtitle,
            theme=theme,
        )
        png_bytes = pil_image_to_png_bytes(summary_image)

    col1, col2 = st.columns(2)
    col1.metric("採用件数", f"{len(thumbnail_df):,}/9")
    col2.metric("表示形式", "サムネのみ")

    st.markdown("---")
    st.subheader("生成画像プレビュー")
    st.image(summary_image, use_container_width=True)

    st.download_button(
        label="PNGをダウンロード",
        data=png_bytes,
        file_name=download_filename,
        mime="image/png",
        use_container_width=True,
    )

    st.markdown("---")
    st.subheader("採用された動画ID")
    st.dataframe(thumbnail_df[["video_id"]], use_container_width=True, hide_index=True)


# =========================
# UI
# =========================
if "manual_df" not in st.session_state:
    st.session_state["manual_df"] = None

st.title("📺 YouTube視聴履歴 タイル画像メーカー")
st.caption("watch-history.json から累積 Top9 を作る方法と、YouTube URL を直接入力してサムネだけの 3×3 を作る方法に対応しています。")

with st.sidebar:
    st.header("設定")
    uploaded_file = st.file_uploader(
        "watch-history.json をアップロード",
        type=["json"],
        accept_multiple_files=False,
    )

    date_filter_enabled = st.checkbox("期間で絞り込む", value=False)
    theme_mode = st.selectbox("背景テーマ", ["デフォルト", "自動"])

# 履歴データがなくても URL入力タブは使えるようにする
df = None
df_filtered = None
all_channels = []
start_date = None
end_date = None

if uploaded_file is not None:
    try:
        df = load_watch_history(uploaded_file)
    except Exception as e:
        st.error(f"JSONの読み込みに失敗しました: {e}")
        st.stop()

    if not df.empty:
        min_date = df["time_jst"].dt.date.min()
        max_date = df["time_jst"].dt.date.max()

        with st.sidebar:
            if date_filter_enabled:
                start_date = st.date_input("開始日", value=min_date, min_value=min_date, max_value=max_date)
                end_date = st.date_input("終了日", value=max_date, min_value=min_date, max_value=max_date)
                if start_date > end_date:
                    st.error("開始日は終了日以前にしてください。")
                    st.stop()
            else:
                start_date = min_date
                end_date = max_date

        df_filtered = apply_date_filter(df, start_date, end_date)
        if not df_filtered.empty:
            all_channels = sorted(df_filtered["channel_name"].dropna().unique().tolist())

tab_all, tab_channel, tab_url = st.tabs(["全体", "チャンネル別", "URL入力"])

with tab_all:
    if uploaded_file is None:
        st.info("このタブを使うには watch-history.json をアップロードしてください。")
    elif df is None or df.empty:
        st.warning("動画として扱える履歴が見つかりませんでした。")
    elif df_filtered is None or df_filtered.empty:
        st.warning("指定期間にデータがありません。")
    else:
        theme_all = resolve_theme("デフォルト", None)
        title_all = "#私を構成するYouTube動画"
        subtitle_all = f"対象期間：{start_date} ～ {end_date}"
        render_result_block(
            df_source=df_filtered,
            image_title=title_all,
            subtitle=subtitle_all,
            theme=theme_all,
            download_filename="my_core_videos_tile_all.png",
            empty_message="表示できる動画がありません。",
        )

with tab_channel:
    if uploaded_file is None:
        st.info("このタブを使うには watch-history.json をアップロードしてください。")
    elif df is None or df.empty:
        st.warning("動画として扱える履歴が見つかりませんでした。")
    elif df_filtered is None or df_filtered.empty:
        st.warning("指定期間にデータがありません。")
    elif not all_channels:
        st.warning("チャンネル情報がある履歴が見つかりませんでした。")
    else:
        st.subheader("チャンネルを選択")
        selected_channel = st.selectbox(
            "対象チャンネル",
            options=all_channels,
            index=0,
            key="selected_channel_tab",
        )

        df_channel = df_filtered[df_filtered["channel_name"] == selected_channel].copy()
        theme_channel = resolve_theme(theme_mode, selected_channel)
        title_channel = f"#私を構成するYouTube動画（チャンネル：{selected_channel}）"
        subtitle_channel = f"対象期間：{start_date} ～ {end_date}"
        channel_filename = safe_filename(selected_channel)

        render_result_block(
            df_source=df_channel,
            image_title=title_channel,
            subtitle=subtitle_channel,
            theme=theme_channel,
            download_filename=f"my_core_videos_tile_{channel_filename}.png",
            empty_message="このチャンネルでは表示できる動画がありません。",
        )

with tab_url:
    st.subheader("YouTube URLからサムネだけの3×3を作る")
    st.caption("視聴履歴アップロードなしでも使えます。YouTube URL を1行ずつ入力してください。先頭から最大9件を採用し、重複URLは除外します。")

    url_text = st.text_area(
        "YouTube URL を改行区切りで入力",
        height=240,
        placeholder=(
            "https://www.youtube.com/watch?v=xxxxxxxxxxx\n"
            "https://youtu.be/yyyyyyyyyyy\n"
            "https://www.youtube.com/shorts/zzzzzzzzzzz"
        ),
        key="manual_url_input",
    )

    if st.button("URLから画像を生成", use_container_width=True):
        st.session_state["manual_df"] = build_manual_thumbnail_df(url_text, max_n=9)

    manual_df = st.session_state["manual_df"]

    if manual_df is not None:
        manual_theme = DEFAULT_THEME
        manual_title = "#私を構成するYouTube動画（手動選択）"
        subtitle_manual = f"採用件数：{len(manual_df)} / 9"

        render_thumbnail_only_block(
            thumbnail_df=manual_df,
            image_title=manual_title,
            subtitle=subtitle_manual,
            theme=manual_theme,
            download_filename="my_thumbnail_board.png",
        )
    else:
        st.info("URLを入力して「URLから画像を生成」を押すと、サムネだけの3×3画像を作れます。")

with st.expander("このアプリの扱いについて"):
    st.markdown(
        """
- 入力は Google Takeout の `watch-history.json` を想定しています。
- YouTube Data API は使っていません。
- Streamlit のキャッシュ機能は使っていません。
- アップロードファイルはこの実行中のメモリ上で処理し、アプリ側で保存する実装にはしていません。
- サムネイルは `i.ytimg.com` からベストエフォート取得しており、取得できない場合は代替表示になります。
- チャンネル別タブでは、選択したチャンネル内だけで動画 Top9 を作成します。
- URL入力タブでは、URLから取得できるサムネイルのみを使って画像を生成します。
        """
    )
