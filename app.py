import base64
import hashlib
import io
import json
import os
import re
import shutil
import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import requests
import streamlit as st
from dotenv import load_dotenv
from PIL import ExifTags, Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


load_dotenv()

APP_TITLE = "照片引语地图"
TEMP_DIR = Path("temp")
GENERATED_DIR = TEMP_DIR / "generated"
APP_DIR = Path(__file__).resolve().parent
WORLD_MAP_CANDIDATES = [
    APP_DIR / "世界地图.png",
    APP_DIR / "world_map.png",
    APP_DIR / "world-map.png",
    Path.cwd() / "世界地图.png",
    Path.cwd() / "world_map.png",
    Path.cwd() / "world-map.png",
]

TTL_MINUTES = int(os.getenv("TEMP_TTL_MINUTES", "30"))
PREVIEW_LONG_EDGE = int(os.getenv("PREVIEW_LONG_EDGE", "1200"))
HD_LONG_EDGE = int(os.getenv("HD_LONG_EDGE", "2400"))
VISION_LONG_EDGE = int(os.getenv("VISION_LONG_EDGE", "1200"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT_SECONDS", "20"))
OPENAI_RETRY_ATTEMPTS = max(1, int(os.getenv("OPENAI_RETRY_ATTEMPTS", "4")))
OPENAI_RETRY_BACKOFF_SECONDS = float(os.getenv("OPENAI_RETRY_BACKOFF_SECONDS", "0.8"))

FONT_CANDIDATES = [
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/msyhbd.ttc",
    "C:/Windows/Fonts/simhei.ttf",
    "C:/Windows/Fonts/simsun.ttc",
    "C:/Windows/Fonts/arial.ttf",
]

# Calibrated geo bounds for current world map background.
MAP_LEFT_RATIO = float(os.getenv("MAP_LEFT_RATIO", "0.035"))
MAP_RIGHT_RATIO = float(os.getenv("MAP_RIGHT_RATIO", "0.965"))
MAP_TOP_RATIO = float(os.getenv("MAP_TOP_RATIO", "0.14"))
MAP_BOTTOM_RATIO = float(os.getenv("MAP_BOTTOM_RATIO", "0.90"))
MAP_LAT_TOP = float(os.getenv("MAP_LAT_TOP", "83"))
MAP_LAT_BOTTOM = float(os.getenv("MAP_LAT_BOTTOM", "-60"))


@dataclass
class ApiConfig:
    base_url: str
    api_key: str
    model: str

    @property
    def enabled(self) -> bool:
        return bool(self.base_url.strip() and self.api_key.strip() and self.model.strip())


def cleanup_temp_files() -> None:
    GENERATED_DIR.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - TTL_MINUTES * 60
    for p in GENERATED_DIR.glob("*"):
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
            elif p.is_dir() and p.stat().st_mtime < cutoff:
                shutil.rmtree(p, ignore_errors=True)
        except OSError:
            continue


def apply_theme() -> None:
    st.markdown(
        """
<style>
:root{
  --bg:#f3ece2;
  --paper:#fbf6ee;
  --paper-strong:#fffaf4;
  --ink:#201a14;
  --muted:#6e6153;
  --line:#d6c2a7;
  --accent:#c46d43;
  --accent-deep:#944a2f;
  --ok:#325e45;
}
@import url('https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,500;6..72,600;6..72,700&family=Roboto:wght@300;400;500;700&display=swap');
:root{
  --font-ui:'Roboto','PingFang SC','Microsoft YaHei',sans-serif;
  --font-display:'Newsreader','Noto Serif SC','STSong',serif;
}
.stApp{
  background:
    radial-gradient(1300px 720px at 3% -10%, #fffaf2 0%, rgba(255,250,242,0) 56%),
    radial-gradient(920px 560px at 102% 8%, #e8d7c1 0%, rgba(232,215,193,0) 58%),
    var(--bg);
  color:var(--ink);
  font-family:var(--font-ui);
}
.block-container{padding-top:.7rem; padding-bottom:1.8rem; max-width:1280px;}
.hero{
  border:1px solid var(--line);
  border-radius:26px;
  padding:26px 28px;
  background:
    linear-gradient(140deg, rgba(255,251,244,.98), rgba(242,227,207,.90));
  box-shadow:0 18px 40px rgba(62,44,30,.11);
  position:relative;
  overflow:hidden;
}
.hero::after{
  content:"";
  position:absolute;
  right:-70px; top:-80px;
  width:250px; height:250px;
  border-radius:50%;
  background:radial-gradient(circle at center, rgba(196,109,67,.22), rgba(196,109,67,0));
  pointer-events:none;
}
.hero h1{
  margin:0 0 .42rem 0;
  color:var(--ink);
  font-family:var(--font-display);
  letter-spacing:.2px;
  font-size:clamp(2rem, 3.4vw, 3.1rem);
}
.hero p{
  margin:0;
  color:var(--muted);
  max-width:790px;
  font-size:1.03rem;
}
.kicker{
  margin:0 0 .45rem 0;
  color:var(--accent-deep);
  font-size:.83rem;
  text-transform:uppercase;
  letter-spacing:.12em;
  font-weight:700;
}
.workflow-grid{
  margin:.9rem 0 1.1rem 0;
  display:grid;
  grid-template-columns:repeat(3, minmax(0,1fr));
  gap:.75rem;
}
.workflow-chip{
  background:linear-gradient(155deg, #fffaf3, #f4e7d4);
  border:1px solid var(--line);
  border-radius:14px;
  padding:.58rem .7rem;
  color:#4d4034;
  font-size:.86rem;
  line-height:1.3;
}
.section-card{
  background:linear-gradient(160deg, var(--paper-strong), var(--paper));
  border:1px solid var(--line);
  border-radius:22px;
  padding:18px 18px 14px 18px;
  box-shadow:0 10px 24px rgba(54,38,24,.07);
  scroll-margin-top:10px;
}
.step-card{
  min-height:680px;
  display:flex;
  flex-direction:column;
}
div[data-testid="stHorizontalBlock"]:first-of-type{
  gap:1rem;
  align-items:stretch;
}
div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"]{
  display:flex;
  height:auto;
}
div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"] > div{
  flex:1 1 auto;
  height:100%;
  background:linear-gradient(160deg, var(--paper-strong), var(--paper));
  border:1px solid var(--line);
  border-radius:22px;
  padding:16px 16px 14px 16px;
  box-shadow:0 10px 24px rgba(54,38,24,.07);
}
div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"] > div[data-testid="stVerticalBlock"]{
  height:100% !important;
  min-height:680px;
}
div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"]:nth-child(2) > div[data-testid="stVerticalBlock"]{
  display:flex;
  flex-direction:column;
}
div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"]:nth-child(2) > div[data-testid="stVerticalBlock"] > div[data-testid="stHorizontalBlock"]{
  margin-top:auto;
  padding-top:.55rem;
}
.panel-kicker{
  margin:0;
  color:var(--accent-deep);
  font-size:.77rem;
  letter-spacing:.11em;
  text-transform:uppercase;
  font-weight:700;
}
.panel-title{
  margin:.24rem 0 .8rem 0;
  font-family:var(--font-display);
  color:var(--ink);
}
.soft-note{
  margin:.65rem 0 .2rem 0;
  color:#5e5245;
  font-size:.89rem;
}
.empty-stage{
  border:1px dashed #ccb59a;
  border-radius:16px;
  background:linear-gradient(140deg,#fffaf3,#f8ecde);
  padding:1.1rem .95rem;
  color:#675a4d;
  font-size:.93rem;
}
.meta-strip{
  margin-top:.6rem;
  padding:.55rem .66rem;
  border:1px solid #d9c7ae;
  border-radius:12px;
  background:rgba(255,248,238,.78);
  color:#5e5145;
  font-size:.85rem;
}
[data-testid="stImage"]{
  margin-top:.2rem;
}
[data-testid="stFileUploader"], .stDateInput, .stTextInput{
  background:var(--paper);
  border:1px solid var(--line);
  border-radius:14px;
  padding:6px;
}
[data-testid="stFileUploader"] section{
  border-radius:12px !important;
  border:1px dashed #cba884 !important;
  background:linear-gradient(155deg,#fffaf4,#f5e6d4) !important;
}
[data-testid="stFileUploader"] small{color:#6b5d50 !important;}
[data-testid="stFileUploaderDropzoneInstructions"]{
  font-size:0 !important;
  line-height:0 !important;
}
[data-testid="stFileUploaderDropzoneInstructions"]::before{
  content:"拖拽图片到此处";
  display:block;
  font-size:1.05rem;
  line-height:1.2;
  color:#5f5145;
  font-weight:600;
  margin-bottom:.28rem;
}
[data-testid="stFileUploaderDropzoneInstructions"]::after{
  content:"单文件不超过 200MB · JPG/JPEG/PNG";
  display:block;
  font-size:.88rem;
  line-height:1.2;
  color:#7a6a59;
}
[data-testid="stFileUploaderDropzone"] button{
  font-size:0 !important;
}
[data-testid="stFileUploaderDropzone"] button::after{
  content:"选择文件";
  font-size:.98rem;
}
.stButton > button, .stDownloadButton > button{
  border-radius:12px;
  border:1px solid var(--accent-deep);
  background:linear-gradient(180deg, var(--accent), var(--accent-deep));
  color:white;
  box-shadow:0 8px 18px rgba(148,74,47,.28);
  transition:transform .2s ease, box-shadow .2s ease, filter .2s ease;
  font-weight:600;
}
.stButton > button:hover, .stDownloadButton > button:hover{
  transform:translateY(-1px);
  box-shadow:0 12px 20px rgba(148,74,47,.34);
  filter:brightness(1.02);
}
[data-testid="stImage"] img{
  border-radius:15px;
}
h2, h3{
  font-family:var(--font-display);
  letter-spacing:.1px;
}
.meta-note{
  color:var(--muted);
  font-size:.92rem;
  margin:.55rem 0 .25rem 0;
}
.result-toolbar{
  display:grid;
  grid-template-columns:1fr 1fr;
  gap:.55rem;
  margin-top:.7rem;
}
.status-pill{
  margin:.35rem 0 .65rem 0;
  color:var(--ok);
  font-size:.85rem;
  font-weight:500;
}
.preview-hint{
  margin:.35rem 0 .6rem 0;
  color:#6a5c4f;
  font-size:.86rem;
}
.fade-in{
  animation:fadeUp .45s ease both;
}
@keyframes fadeUp{
  from{opacity:.15; transform:translateY(8px);}
  to{opacity:1; transform:translateY(0);}
}
@media (prefers-reduced-motion: reduce){
  .fade-in{animation:none;}
  .stButton > button, .stDownloadButton > button{transition:none;}
}
@media (max-width: 920px){
  .workflow-grid{grid-template-columns:1fr;}
  .step-card{min-height:auto;}
  div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"] > div{
    padding:12px 12px 10px 12px;
    border-radius:16px;
  }
  div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"] > div[data-testid="stVerticalBlock"]{
    min-height:auto;
    height:auto !important;
  }
  div[data-testid="stHorizontalBlock"]:first-of-type > [data-testid="column"]:nth-child(2) > div[data-testid="stVerticalBlock"]{
    display:block;
  }
}
</style>
""",
        unsafe_allow_html=True,
    )


def get_font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    for p in FONT_CANDIDATES:
        target = "C:/Windows/Fonts/msyhbd.ttc" if (bold and p.endswith("msyh.ttc")) else p
        try:
            return ImageFont.truetype(target, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def init_state() -> None:
    defaults = {
        "session_id": str(uuid.uuid4()),
        "regen_remaining": -1,
        "vision_result": None,
        "quote_text": "",
        "preview_bytes": None,
        "hd_bytes": None,
        "photo_hash": "",
        "source_image_bytes": None,
        "last_country": "",
        "last_date": "",
        "last_pin": None,
        "last_style": "冰心风",
        "regen_serial": 0,
        "quote_source": "unknown",
        "quote_error": "",
        "quote_model": "",
        "vision_source": "unknown",
        "vision_error": "",
        "vision_model": "",
        "autogen_done_hash": "",
        "autogen_attempted_hash": "",
        "autogen_done_sig": "",
        "autogen_attempted_sig": "",
        "last_upload_id": "",
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v


def parse_exif(img: Image.Image) -> Dict[str, Any]:
    exif_data = {"date": "", "gps": None}
    raw_exif = img.getexif()
    if not raw_exif:
        return exif_data

    exif_dict = {}
    for tag_id, value in raw_exif.items():
        tag = ExifTags.TAGS.get(tag_id, tag_id)
        exif_dict[tag] = value

    for key in ["DateTimeOriginal", "DateTimeDigitized", "DateTime"]:
        if key in exif_dict:
            exif_data["date"] = str(exif_dict[key]).replace(":", "-", 2).split(" ")[0]
            break

    gps_info = exif_dict.get("GPSInfo")
    if isinstance(gps_info, int) and hasattr(raw_exif, "get_ifd"):
        try:
            gps_info = raw_exif.get_ifd(ExifTags.IFD.GPSInfo)
        except Exception:
            gps_info = None

    if isinstance(gps_info, dict) and gps_info:
        decoded = {}
        for t, v in gps_info.items():
            sub_tag = ExifTags.GPSTAGS.get(t, t)
            decoded[sub_tag] = v
        lat = convert_gps_to_decimal(decoded.get("GPSLatitude"), decoded.get("GPSLatitudeRef"))
        lon = convert_gps_to_decimal(decoded.get("GPSLongitude"), decoded.get("GPSLongitudeRef"))
        if lat is not None and lon is not None:
            exif_data["gps"] = (lat, lon)
    return exif_data


def convert_gps_to_decimal(gps_coord, gps_ref) -> Optional[float]:
    if not gps_coord or not gps_ref:
        return None
    try:
        d = _ratio_to_float(gps_coord[0])
        m = _ratio_to_float(gps_coord[1])
        s = _ratio_to_float(gps_coord[2])
        value = d + (m / 60.0) + (s / 3600.0)
        if gps_ref in ["S", "W"]:
            value = -value
        return value
    except Exception:
        return None


def _ratio_to_float(r) -> float:
    if isinstance(r, tuple) and len(r) == 2 and r[1] != 0:
        return float(r[0]) / float(r[1])
    if hasattr(r, "numerator") and hasattr(r, "denominator") and r.denominator != 0:
        return float(r.numerator) / float(r.denominator)
    return float(r)


@st.cache_data(ttl=86400, show_spinner=False)
def reverse_location_from_gps(gps: Optional[Tuple[float, float]]) -> Tuple[str, str]:
    if not gps:
        return "", ""
    lat, lon = gps
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lon, "format": "jsonv2", "zoom": 10, "accept-language": "zh-CN"},
            headers={"User-Agent": "photoquote-map-local/1.0"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        address = resp.json().get("address", {})
        country = address.get("country", "") or ""
        city = (
            address.get("city")
            or address.get("town")
            or address.get("village")
            or address.get("municipality")
            or address.get("county")
            or address.get("state")
            or ""
        )
        if city or country:
            return city, country
    except Exception:
        pass
    try:
        resp = requests.get(
            "https://api.bigdatacloud.net/data/reverse-geocode-client",
            params={"latitude": lat, "longitude": lon, "localityLanguage": "zh"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        city = data.get("city") or data.get("locality") or data.get("principalSubdivision") or ""
        country = data.get("countryName", "") or ""
        return city, country
    except Exception:
        return "", ""


@st.cache_data(ttl=604800, show_spinner=False)
def geocode_country_center(country: str) -> Optional[Tuple[float, float]]:
    if not country:
        return None
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": country, "format": "jsonv2", "limit": 1, "accept-language": "en"},
            headers={"User-Agent": "photoquote-map-local/1.0"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if data:
            return (float(data[0]["lat"]), float(data[0]["lon"]))
    except Exception:
        pass
    try:
        resp = requests.get(
            f"https://restcountries.com/v3.1/name/{country}",
            params={"fullText": "true"},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        latlng = data[0].get("latlng")
        if latlng:
            return (float(latlng[0]), float(latlng[1]))
    except Exception:
        return None
    return None


def resize_long_edge(img: Image.Image, long_edge: int) -> Image.Image:
    w, h = img.size
    if max(w, h) <= long_edge:
        return img
    scale = long_edge / max(w, h)
    return img.resize((int(w * scale), int(h * scale)), Image.Resampling.LANCZOS)


def prepare_vision_data_url(uploaded_bytes: bytes, mime_type: str) -> str:
    img = Image.open(io.BytesIO(uploaded_bytes)).convert("RGB")
    img = resize_long_edge(img, VISION_LONG_EDGE)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    encoded = base64.b64encode(buf.getvalue()).decode("utf-8")
    return f"data:{mime_type};base64,{encoded}"


def _post_openai_chat(cfg: ApiConfig, payload: Dict[str, Any]) -> requests.Response:
    url = f"{cfg.base_url.rstrip('/')}/chat/completions"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    last_error: Optional[Exception] = None
    for attempt in range(1, OPENAI_RETRY_ATTEMPTS + 1):
        try:
            resp = requests.post(url, headers=headers, json=payload, timeout=HTTP_TIMEOUT)
            if resp.status_code >= 500 and attempt < OPENAI_RETRY_ATTEMPTS:
                time.sleep(OPENAI_RETRY_BACKOFF_SECONDS * attempt)
                continue
            return resp
        except (requests.exceptions.SSLError, requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            last_error = e
            if attempt >= OPENAI_RETRY_ATTEMPTS:
                raise
            time.sleep(OPENAI_RETRY_BACKOFF_SECONDS * attempt)
    if last_error:
        raise last_error
    raise RuntimeError("OpenAI request failed without response.")


def parse_json_or_fallback(raw: str, fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return json.loads(raw)
    except Exception:
        m = re.search(r"\{.*\}", str(raw), flags=re.S)
        if not m:
            return fallback
        try:
            return json.loads(m.group(0))
        except Exception:
            return fallback


def normalize_vision_result(data: Any) -> Dict[str, Any]:
    """Normalize heterogeneous vision outputs into a stable schema."""
    if not isinstance(data, dict):
        return {"scene_tags": [], "mood": [], "objects": [], "short_caption": ""}

    def _listify(v: Any) -> list[str]:
        if isinstance(v, list):
            return [str(x).strip() for x in v if str(x).strip()]
        if isinstance(v, str):
            s = v.strip()
            if not s:
                return []
            parts = re.split(r"[，,、;；|/\n]+", s)
            return [p.strip() for p in parts if p.strip()]
        return []

    def _first_str(*keys: str) -> str:
        for k in keys:
            v = data.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
        return ""

    scene_tags = _listify(data.get("scene_tags") or data.get("tags") or data.get("scene") or data.get("scenes"))
    mood = _listify(data.get("mood") or data.get("atmosphere") or data.get("tone"))
    objects = _listify(data.get("objects") or data.get("entities") or data.get("items"))
    short_caption = _first_str("short_caption", "caption", "description", "summary")

    # Handle common Chinese key variants.
    if not scene_tags:
        scene_tags = _listify(data.get("场景标签") or data.get("场景") or data.get("标签"))
    if not mood:
        mood = _listify(data.get("氛围") or data.get("情绪"))
    if not objects:
        objects = _listify(data.get("物体") or data.get("主体") or data.get("元素"))
    if not short_caption:
        short_caption = _first_str("画面描述", "描述", "一句话描述")

    return {
        "scene_tags": scene_tags[:8],
        "mood": mood[:4],
        "objects": objects[:8],
        "short_caption": short_caption[:80],
    }


def extract_message_text(content: Any) -> str:
    """Extract text from chat message content across string/list/dict variants."""
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        for k in ("text", "content", "output_text"):
            v = content.get(k)
            if isinstance(v, str) and v.strip():
                return v
        return ""
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, str) and item.strip():
                chunks.append(item.strip())
                continue
            if isinstance(item, dict):
                # Common shapes: {"type":"text","text":"..."} or {"text":"..."}
                for k in ("text", "content", "output_text"):
                    v = item.get(k)
                    if isinstance(v, str) and v.strip():
                        chunks.append(v.strip())
                        break
        return "\n".join(chunks).strip()
    return ""


def call_vision_api(cfg: ApiConfig, image_data_url: str) -> Dict[str, Any]:
    if not cfg.enabled:
        st.session_state.vision_source = "fallback"
        st.session_state.vision_error = "Vision API config incomplete (base_url/api_key/model)."
        st.session_state.vision_model = cfg.model or ""
        return {
            "scene_tags": ["street", "night"],
            "mood": ["quiet", "cinematic"],
            "objects": ["people", "building"],
            "short_caption": "夜色里，步伐与光影擦肩而过。",
        }
    payload = {
        "model": cfg.model,
        "messages": [
            {"role": "system", "content": "You are a precise JSON-only vision analyzer."},
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "Return strict JSON with keys: scene_tags, mood, objects, short_caption. "
                            "scene_tags: 4-8 concrete scene keywords; "
                            "mood: 2-4 atmosphere words; "
                            "objects: 4-8 visible nouns; "
                            "short_caption: one Chinese sentence (18-40 chars) describing visible details only."
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": image_data_url}},
                ],
            },
        ],
        "temperature": 0.2,
        "max_completion_tokens": 260,
    }
    try:
        resp = _post_openai_chat(cfg, payload)
        # Backward compatibility for older chat-completions models.
        if resp.status_code == 400 and "max_completion_tokens" in payload:
            try:
                body = (resp.text or "").lower()
            except Exception:
                body = ""
            if "max_completion_tokens" in body and "unsupported" in body:
                legacy_payload = dict(payload)
                legacy_payload.pop("max_completion_tokens", None)
                legacy_payload["max_tokens"] = 260
                resp = _post_openai_chat(cfg, legacy_payload)
        # Some models only support default temperature and reject custom values.
        if resp.status_code == 400 and "temperature" in payload:
            try:
                body = (resp.text or "").lower()
            except Exception:
                body = ""
            if "temperature" in body and ("only the default" in body or "unsupported value" in body):
                no_temp_payload = dict(payload)
                no_temp_payload.pop("temperature", None)
                resp = _post_openai_chat(cfg, no_temp_payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"].get("content")
        text = extract_message_text(content)
        if not text.strip():
            st.session_state.vision_source = "fallback"
            st.session_state.vision_error = (
                "Vision response is empty. The selected model may not support image input on this endpoint; "
                "try a vision-capable model for VISION_MODEL (e.g. gpt-4.1-mini)."
            )
            st.session_state.vision_model = cfg.model
            return {
                "scene_tags": ["street", "night"],
                "mood": ["quiet", "cinematic"],
                "objects": ["people", "building"],
                "short_caption": "夜色里，步伐与光影擦肩而过。",
            }
        parsed_raw = parse_json_or_fallback(text, {"scene_tags": [], "mood": [], "objects": [], "short_caption": ""})
        parsed = normalize_vision_result(parsed_raw)
        has_detail = bool(parsed.get("scene_tags") or parsed.get("objects") or str(parsed.get("short_caption", "")).strip())
        if not has_detail:
            st.session_state.vision_source = "fallback"
            st.session_state.vision_error = f"Vision returned empty details. Raw: {str(text)[:180]}"
            st.session_state.vision_model = cfg.model
            return {
                "scene_tags": ["street", "night"],
                "mood": ["quiet", "cinematic"],
                "objects": ["people", "building"],
                "short_caption": "夜色里，步伐与光影擦肩而过。",
            }
        st.session_state.vision_source = "api"
        st.session_state.vision_error = ""
        st.session_state.vision_model = cfg.model
        return parsed
    except Exception as e:
        err = str(e)
        try:
            if "resp" in locals() and getattr(resp, "status_code", None):
                body = (resp.text or "").strip().replace("\n", " ")
                err = f"HTTP {resp.status_code}: {body[:220]}"
        except Exception:
            pass
        st.session_state.vision_source = "fallback"
        st.session_state.vision_error = err[:260]
        st.session_state.vision_model = cfg.model
        return {
            "scene_tags": ["street", "night"],
            "mood": ["quiet", "cinematic"],
            "objects": ["people", "building"],
            "short_caption": "夜色里，步伐与光影擦肩而过。",
        }


def auto_pick_style(vision: Dict[str, Any]) -> str:
    tags = " ".join(vision.get("scene_tags", []) + vision.get("objects", [])).lower()
    mood = " ".join(vision.get("mood", [])).lower()

    food_keys = ["food", "restaurant", "meal", "kitchen", "drink", "coffee", "hotpot", "bbq", "noodle"]
    work_keys = ["office", "computer", "meeting", "desk", "coworker", "company", "work"]
    game_keys = ["game", "controller", "keyboard", "monitor", "esports", "arcade"]
    night_keys = ["night", "late", "moon", "dark", "streetlight"]
    single_keys = ["solo", "alone", "single", "portrait"]

    joined = f"{tags} {mood}"
    calm_hint = any(k in joined for k in ["calm", "quiet", "peaceful", "gentle", "soft", "warm"])
    energetic_hint = any(k in joined for k in ["busy", "crowded", "party", "dynamic", "fast", "sport", "fun"])

    def hit_count(keys: list[str]) -> int:
        return sum(1 for k in keys if k in joined)

    food_hits = hit_count(food_keys)
    work_hits = hit_count(work_keys)
    game_hits = hit_count(game_keys)
    night_hits = hit_count(night_keys)
    single_hits = hit_count(single_keys)

    # Priority rule: prefer cinematic/healing first; switch to meme styles only with strong cues.
    if calm_hint:
        return "治愈慢调"
    if food_hits >= 2:
        return "干饭人专享版"
    if work_hits >= 2 and energetic_hint:
        return "职场阴阳怪气版"
    if game_hits >= 2:
        return "游戏人生篇"
    if night_hits >= 2 and energetic_hint:
        return "熬夜冠军宣言"
    if single_hits >= 2:
        return "单身贵族凡尔赛"
    return "电影感"


def _style_prompt(style: str) -> str:
    prompts = {
        "冰心风": "风格要求：以冰心散文气质写作，语言清澈、温柔含蓄，意象自然，情感真挚克制。",
        "干饭人专享版": "风格要求：嘴馋、香气感、快乐，带轻幽默，不油腻。",
        "职场阴阳怪气版": "风格要求：机灵轻讽刺，好笑但不攻击个人，不刻薄。",
        "单身贵族凡尔赛": "风格要求：自嘲中带骄傲，像开玩笑地炫耀单身自由。",
        "熬夜冠军宣言": "风格要求：夜猫子口吻，略夸张，有热血感。",
        "游戏人生篇": "风格要求：游戏梗+现实反差，节奏利落，燃一点。",
        "治愈慢调": "风格要求：温柔克制，留白感，读起来能慢下来。",
        "电影感": "风格要求：画面感强，镜头感明确，节奏干净。",
    }
    return prompts.get(style, prompts["冰心风"])


def _vision_brief(vision: Dict[str, Any]) -> str:
    if not isinstance(vision, dict):
        return "无"
    parts = []
    # Primary schema from call_vision_api: scene_tags, mood, objects, short_caption.
    scene_tags = vision.get("scene_tags")
    if isinstance(scene_tags, list) and scene_tags:
        compact_tags = [str(t).strip() for t in scene_tags[:6] if str(t).strip()]
        if compact_tags:
            parts.append("scene_tags:" + ",".join(compact_tags))

    mood = vision.get("mood")
    if isinstance(mood, list) and mood:
        compact_mood = [str(t).strip() for t in mood[:4] if str(t).strip()]
        if compact_mood:
            parts.append("mood:" + ",".join(compact_mood))

    objects = vision.get("objects")
    if isinstance(objects, list) and objects:
        compact_objs = [str(t).strip() for t in objects[:6] if str(t).strip()]
        if compact_objs:
            parts.append("objects:" + ",".join(compact_objs))

    short_caption = vision.get("short_caption")
    if isinstance(short_caption, str) and short_caption.strip():
        parts.append("caption:" + short_caption.strip())

    # Backward compatible with any legacy keys.
    for k in ("main_subject", "scene", "lighting", "weather", "action", "color_tone"):
        v = vision.get(k)
        if isinstance(v, str) and v.strip():
            parts.append(f"{k}:{v.strip()}")
    if not parts:
        return "无"
    return "；".join(parts)[:220]


def _build_quote_prompt(
    style: str,
    location: str,
    date_text: str,
    vision: Dict[str, Any],
    previous_quote: str = "",
    variation_seed: int = 0,
) -> str:
    style_req = _style_prompt(style)
    scene = _vision_brief(vision)
    extra_rule = ""
    if previous_quote.strip():
        extra_rule = (
            f"8) 必须与上一句明显不同，避免同词重复。上一句：{sanitize_quote(previous_quote)}\n"
            f"9) 变体序号：{variation_seed}\n"
        )
    return (
        "任务：生成一条中文海报短句。\n"
        "硬性规则：\n"
        "1) 只输出一句中文，不要换行，不要解释。\n"
        "2) 句长14-34字。\n"
        "3) 禁止英文、emoji、#话题、引号、书名号、括号。\n"
        "4) 要有可感知画面，不空泛，不鸡汤，不模板化。\n"
        "5) 不编造具体人名、品牌、地点细节。\n"
        "6) 必须紧扣视觉摘要，至少体现其中两项元素（场景/物体/氛围/动作）。\n"
        "7) 不要只写抽象心情，优先写看得见的细节。\n"
        f"{extra_rule}"
        f"风格：{style}\n"
        f"{style_req}\n"
        f"输入信息：地点={location or '未知'}；日期={date_text or '未知'}；视觉摘要={scene}\n"
        "输出：只给最终短句。"
    )


def _style_fallback_quote(
    style: str,
    location: str,
    date_text: str,
    vision: Dict[str, Any],
    previous_quote: str = "",
    variation_seed: int = 0,
) -> str:
    samples = {
        "冰心风": [
            f"{date_text or '这一天'}在{location or '远方'}，风从树梢轻轻落下，心也在光里慢慢明亮。",
            f"在{location or '远方'}的{date_text or '这一天'}，云影贴着街面缓缓走过，日子忽然温柔起来。",
            f"{date_text or '这一天'}，我在{location or '远方'}看见晚光落进树叶，沉默也有了暖意。",
            f"{location or '远方'}的{date_text or '这一天'}，风很轻，天很净，心事在暮色里慢慢放下。",
            f"{date_text or '这一天'}经过{location or '远方'}，一束光掠过肩头，疲惫像潮水一样退远。",
        ],
        "干饭人专享版": [
            f"{date_text or '今天'}在{location or '这座城'}，这一口香到上头，快乐值直接加满一整格。",
            "别人秋天第一杯奶茶，我是本周第八杯，主打一个稳定发挥。",
        ],
        "职场阴阳怪气版": [
            "领导说年轻人要多吃苦，我当场点了苦瓜炒蛋表示积极响应。",
            "上班摸鱼多年，切屏快捷键的手速已经快到只剩残影。",
        ],
        "单身贵族凡尔赛": [
            "朋友问我为何还单身，我说档期太满，先把自由过成限量版。",
            "月老大概把我的红线拿去织秋裤了，我先穿暖再说。",
        ],
        "熬夜冠军宣言": [
            "月亮不睡我不睡，凌晨三点我和灵感开了一场加时赛。",
            "发现早睡秘诀是把手机扔客厅，而我半夜还是去把它捡回来。",
        ],
        "游戏人生篇": [
            "现实里我唯唯诺诺，进了游戏我就是全队最硬的前排。",
            "白天打工攒能量，晚上开黑上分，把压力全变成击杀播报。",
        ],
        "治愈慢调": [f"在{location or '远方'}的{date_text or '这一天'}，晚风很轻，心也慢慢安静下来。"],
        "电影感": [f"{date_text or '这一天'}在{location or '远方'}，光影掠过街角，沉默也有了对白。"],
    }
    arr = samples.get(style, samples["冰心风"])
    normalized = [sanitize_quote(x) for x in arr]
    prev = sanitize_quote(previous_quote) if previous_quote else ""
    candidates = [x for x in normalized if x != prev] if prev else normalized
    if not candidates:
        # Force a different fallback when all samples collapse to the previous sentence.
        forced = [
            f"在{location or '远方'}的{date_text or '这一天'}，风声贴着黄昏走，心里忽然亮了一盏灯。",
            f"{date_text or '这一天'}路过{location or '远方'}，云很低，光很软，旧事也变得轻了。",
            f"{location or '远方'}的天色在{date_text or '这一天'}慢慢暗下去，我却在微风里安静下来。",
        ]
        candidates = [sanitize_quote(x) for x in forced if sanitize_quote(x) != prev] or normalized
    seed = abs(hash((location, date_text, style, json.dumps(vision, ensure_ascii=False), variation_seed, time.time_ns())))
    return candidates[seed % len(candidates)]


def sanitize_quote(text: str) -> str:
    text = (text or "").strip()
    text = text.translate(str.maketrans({",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}))
    text = re.sub(r"[\"'“”‘’`《》「」『』()（）【】\[\]<>#]", "", text)
    text = re.sub(r"[A-Za-z]", "", text)
    text = re.sub(r"\s+", "", text).replace("...", "。").replace("..", "。").replace("…", "。")
    # Keep only Chinese chars, digits and core Chinese punctuation.
    text = re.sub(r"[^\u4e00-\u9fff0-9，。、！？；：]", "", text)
    # Normalize punctuation runs and mixed punctuation collisions.
    text = re.sub(r"[，、；：]{2,}", "，", text)
    text = re.sub(r"[。！？]{2,}", "。", text)
    text = re.sub(r"([。！？])[，、；：]+", r"\1", text)
    text = re.sub(r"[，、；：]+([。！？])", r"\1", text)
    text = re.sub(r"^[，。！？；：、]+", "", text)
    text = re.sub(r"[，、；：]+$", "", text)
    # Make sanitization idempotent for the short-text fallback suffix.
    text = re.sub(r"(，?故事才刚刚开始[。！？]?){2,}", "，故事才刚刚开始", text)
    if not text:
        text = "风从眼前经过，心里慢慢亮起来"
    if len(text) < 12:
        if "故事才刚刚开始" not in text:
            text = f"{text}，故事才刚刚开始"
        else:
            text = re.sub(r"^，+", "", text)
            text = re.sub(r"(，?故事才刚刚开始[。！？]?)+", "故事才刚刚开始", text)
    if text and text[-1] not in "。！？":
        text += "。"
    if len(text) > 36:
        text = re.sub(r"[，、；：]+$", "", text[:35]) + "。"
    # Final pass: avoid weird tail like "，。"
    text = re.sub(r"[，、；：]+([。！？])$", r"\1", text)
    return text


def format_date_for_display(date_text: str) -> str:
    try:
        dt = datetime.strptime(date_text, "%Y-%m-%d")
        return f"{dt.year}年{dt.month}月{dt.day}日"
    except Exception:
        try:
            dt = datetime.fromisoformat(date_text)
            return f"{dt.year}年{dt.month}月{dt.day}日"
        except Exception:
            return date_text


def call_quote_api(
    cfg: ApiConfig,
    location: str,
    date_text: str,
    vision: Dict[str, Any],
    style: str,
    previous_quote: str = "",
    variation_seed: int = 0,
) -> str:
    if not cfg.enabled:
        st.session_state.quote_source = "fallback"
        st.session_state.quote_error = "Text API config incomplete (base_url/api_key/model)."
        st.session_state.quote_model = cfg.model or ""
        return _style_fallback_quote(style, location, date_text, vision, previous_quote=previous_quote, variation_seed=variation_seed)
    payload = {
        "model": cfg.model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是资深中文短句文案编辑。"
                    "必须严格遵守用户给定规则，只输出一条可直接上图的中文句子。"
                ),
            },
            {
                "role": "user",
                "content": _build_quote_prompt(
                    style,
                    location,
                    date_text,
                    vision,
                    previous_quote=previous_quote,
                    variation_seed=variation_seed,
                ),
            },
        ],
        "temperature": 0.65,
        "max_completion_tokens": 96,
    }
    try:
        resp = _post_openai_chat(cfg, payload)
        # Backward compatibility for older chat-completions models.
        if resp.status_code == 400 and "max_completion_tokens" in payload:
            try:
                body = (resp.text or "").lower()
            except Exception:
                body = ""
            if "max_completion_tokens" in body and "unsupported" in body:
                legacy_payload = dict(payload)
                legacy_payload.pop("max_completion_tokens", None)
                legacy_payload["max_tokens"] = 96
                resp = _post_openai_chat(cfg, legacy_payload)
        # Some models only support default temperature and reject custom values.
        if resp.status_code == 400 and "temperature" in payload:
            try:
                body = (resp.text or "").lower()
            except Exception:
                body = ""
            if "temperature" in body and ("only the default" in body or "unsupported value" in body):
                no_temp_payload = dict(payload)
                no_temp_payload.pop("temperature", None)
                resp = _post_openai_chat(cfg, no_temp_payload)
        resp.raise_for_status()
        st.session_state.quote_source = "api"
        st.session_state.quote_error = ""
        st.session_state.quote_model = cfg.model
        return sanitize_quote(resp.json()["choices"][0]["message"]["content"])
    except Exception as e:
        err = str(e)
        try:
            if "resp" in locals() and getattr(resp, "status_code", None):
                body = (resp.text or "").strip().replace("\n", " ")
                err = f"HTTP {resp.status_code}: {body[:220]}"
        except Exception:
            pass
        st.session_state.quote_source = "fallback"
        st.session_state.quote_error = err[:260]
        st.session_state.quote_model = cfg.model
        return _style_fallback_quote(style, location, date_text, vision, previous_quote=previous_quote, variation_seed=variation_seed)


def generate_qr_image(session_id: str) -> Image.Image:
    data = f"https://example.local/upload?src=qr&tpl=default&sid={session_id}"
    size, cell, border = 29, 8, 2
    img = Image.new("RGB", ((size + border * 2) * cell, (size + border * 2) * cell), "white")
    draw = ImageDraw.Draw(img)
    bits = "".join(f"{b:08b}" for b in data.encode("utf-8")) or "0"
    idx = 0
    for y in range(size):
        for x in range(size):
            if (x < 7 and y < 7) or (x >= size - 7 and y < 7) or (x < 7 and y >= size - 7):
                continue
            if bits[idx % len(bits)] == "1":
                ox, oy = (x + border) * cell, (y + border) * cell
                draw.rectangle((ox, oy, ox + cell - 1, oy + cell - 1), fill="black")
            idx += 1
    return img


def image_fit(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    src_w, src_h = img.size
    dst_w, dst_h = size
    src_ratio = src_w / src_h
    dst_ratio = dst_w / dst_h
    if src_ratio > dst_ratio:
        new_h, new_w = dst_h, int(dst_h * src_ratio)
    else:
        new_w, new_h = dst_w, int(dst_w / src_ratio)
    resized = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
    left, top = (new_w - dst_w) // 2, (new_h - dst_h) // 2
    return resized.crop((left, top, left + dst_w, top + dst_h))


def image_contain_with_backdrop(img: Image.Image, size: Tuple[int, int]) -> Image.Image:
    src_w, src_h = img.size
    dst_w, dst_h = size
    # Backdrop keeps full-bleed style while preserving the full source image in foreground.
    bg = image_fit(img, size).filter(ImageFilter.GaussianBlur(radius=max(10, dst_w // 70)))
    shade = Image.new("RGBA", (dst_w, dst_h), (24, 18, 12, 46))
    bg = Image.alpha_composite(bg.convert("RGBA"), shade).convert("RGB")

    scale = min(dst_w / src_w, dst_h / src_h)
    fg_w = max(1, int(src_w * scale))
    fg_h = max(1, int(src_h * scale))
    fg = img.resize((fg_w, fg_h), Image.Resampling.LANCZOS)
    x = (dst_w - fg_w) // 2
    y = (dst_h - fg_h) // 2
    bg.paste(fg, (x, y))
    return bg


def wrap_text_by_pixels(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int, max_lines: int) -> str:
    lines = []
    current = ""
    for ch in text:
        test = current + ch
        width = draw.textbbox((0, 0), test, font=font)[2]
        if width <= max_width:
            current = test
        else:
            lines.append(current)
            current = ch
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len("".join(lines)) < len(text):
        lines[-1] = lines[-1][:-1] + "…"
    return "\n".join(lines)


@st.cache_data(show_spinner=False)
def load_world_map() -> Optional[Image.Image]:
    world_map_path = resolve_world_map_path()
    if world_map_path is None:
        return None
    try:
        return Image.open(world_map_path).convert("RGB")
    except Exception:
        return None


def resolve_world_map_path() -> Optional[Path]:
    return next((p for p in WORLD_MAP_CANDIDATES if p.exists()), None)


def latlon_to_map_xy(lat: float, lon: float, map_w: int, map_h: int) -> Tuple[int, int]:
    # Clamp to calibrated geo bounds for the supplied world-map artwork.
    lon = max(-180.0, min(180.0, lon))
    lat = max(MAP_LAT_BOTTOM, min(MAP_LAT_TOP, lat))

    x01 = MAP_LEFT_RATIO + ((lon + 180.0) / 360.0) * (MAP_RIGHT_RATIO - MAP_LEFT_RATIO)
    y01 = MAP_TOP_RATIO + ((MAP_LAT_TOP - lat) / (MAP_LAT_TOP - MAP_LAT_BOTTOM)) * (MAP_BOTTOM_RATIO - MAP_TOP_RATIO)
    x = int(max(0.0, min(1.0, x01)) * map_w)
    y = int(max(0.0, min(1.0, y01)) * map_h)
    return x, y


def render_poster(
    original_img: Image.Image,
    country: str,
    date_text: str,
    quote_text: str,
    session_id: str,
    long_edge: int,
    with_watermark: bool,
    pin_latlon: Optional[Tuple[float, float]],
) -> bytes:
    photo = resize_long_edge(original_img.convert("RGB"), long_edge)
    w = long_edge if photo.width >= photo.height else int(long_edge / (photo.height / photo.width))
    h = int(w * 1.36)
    canvas = Image.new("RGB", (w, h), color=(241, 234, 222))
    draw = ImageDraw.Draw(canvas)

    # Keep the full source photo visible for unusual aspect ratios.
    top_h = int(h * 0.64)
    canvas.paste(image_contain_with_backdrop(photo, (w, top_h)), (0, 0))
    draw.rectangle((0, top_h - 10, w, top_h + 10), fill=(199, 185, 160))
    draw.rectangle((0, top_h, w, h), fill=(247, 241, 230))
    draw.line((0, top_h, w, top_h), fill=(203, 188, 159), width=4)

    # Poster content area (bottom): world map fills the whole panel.
    gutter = max(24, w // 44)
    panel_x, panel_y = gutter, top_h + gutter
    panel_w, panel_h = w - gutter * 2, h - top_h - gutter * 2
    draw.rounded_rectangle(
        (panel_x, panel_y, panel_x + panel_w, panel_y + panel_h),
        radius=max(16, w // 90),
        fill=(250, 245, 235),
        outline=(188, 171, 141),
        width=3,
    )

    map_pad = max(8, w // 180)
    geo_x, geo_y = panel_x + map_pad, panel_y + map_pad
    geo_w, geo_h = max(1, panel_w - map_pad * 2), max(1, panel_h - map_pad * 2)
    map_base = load_world_map()
    if map_base is not None:
        mw, mh = map_base.size
        scale = min(geo_w / mw, geo_h / mh)
        disp_w = max(1, int(mw * scale))
        disp_h = max(1, int(mh * scale))
        map_x = geo_x + (geo_w - disp_w) // 2
        map_y = geo_y + (geo_h - disp_h) // 2
        map_img = map_base.resize((disp_w, disp_h), Image.Resampling.LANCZOS)
        canvas.paste(map_img, (map_x, map_y))
        geo_x, geo_y, geo_w, geo_h = map_x, map_y, disp_w, disp_h

    # Light wash only over content area for readability.
    wash = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    wdraw = ImageDraw.Draw(wash)
    wdraw.rounded_rectangle(
        (panel_x + 1, panel_y + 1, panel_x + panel_w - 1, panel_y + panel_h - 1),
        radius=max(15, w // 95),
        fill=(28, 21, 12, 30),
    )
    canvas = Image.alpha_composite(canvas.convert("RGBA"), wash).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    pin = pin_latlon if pin_latlon else (0.0, 0.0)
    pxi, pyi = latlon_to_map_xy(pin[0], pin[1], geo_w, geo_h)
    px, py = geo_x + pxi, geo_y + pyi
    r = max(8, w // 160)
    draw.ellipse((px - r - 4, py - r - 4, px + r + 4, py + r + 4), fill=(255, 232, 214))
    draw.ellipse((px - r, py - r, px + r, py + r), fill=(224, 76, 40), outline=(120, 26, 16), width=2)
    draw.ellipse((px - 2, py - 2, px + 2, py + 2), fill=(255, 246, 232))

    title_size = max(30, w // 33)
    meta_size = max(21, w // 54)
    quote_size = max(28, w // 32)
    title_font = get_font(title_size, bold=True)
    meta_font = get_font(meta_size)
    quote_font = get_font(quote_size, bold=True)

    content_margin = max(18, w // 64)
    text_w = int(panel_w * 0.56)
    text_h = int(panel_h * 0.64)
    qr_size = max(110, min(int(panel_h * 0.34), int(panel_w * 0.22)))
    qr_pad = max(12, w // 96)
    qr_w, qr_h = qr_size + qr_pad * 2, qr_size + qr_pad * 2

    def rect_hits_pin(rect: Tuple[int, int, int, int], pad: int) -> bool:
        x1, y1, x2, y2 = rect
        return (x1 - pad) <= px <= (x2 + pad) and (y1 - pad) <= py <= (y2 + pad)

    def rect_intersects(a: Tuple[int, int, int, int], b: Tuple[int, int, int, int]) -> bool:
        ax1, ay1, ax2, ay2 = a
        bx1, by1, bx2, by2 = b
        return not (ax2 <= bx1 or bx2 <= ax1 or ay2 <= by1 or by2 <= ay1)

    def farthest_rect(rects: list[Tuple[int, int, int, int]]) -> Tuple[int, int, int, int]:
        return max(rects, key=lambda rc: ((rc[0] + rc[2]) // 2 - px) ** 2 + ((rc[1] + rc[3]) // 2 - py) ** 2)

    text_candidates = [
        (panel_x + content_margin, panel_y + content_margin, panel_x + content_margin + text_w, panel_y + content_margin + text_h),
        (panel_x + panel_w - content_margin - text_w, panel_y + content_margin, panel_x + panel_w - content_margin, panel_y + content_margin + text_h),
        (panel_x + content_margin, panel_y + panel_h - content_margin - text_h, panel_x + content_margin + text_w, panel_y + panel_h - content_margin),
        (panel_x + panel_w - content_margin - text_w, panel_y + panel_h - content_margin - text_h, panel_x + panel_w - content_margin, panel_y + panel_h - content_margin),
    ]
    text_pin_pad = max(44, w // 17)
    valid_text = [r for r in text_candidates if not rect_hits_pin(r, pad=text_pin_pad)]
    text_card = valid_text[0] if valid_text else farthest_rect(text_candidates)

    qr_candidates = [
        (panel_x + content_margin, panel_y + content_margin, panel_x + content_margin + qr_w, panel_y + content_margin + qr_h),
        (panel_x + panel_w - content_margin - qr_w, panel_y + content_margin, panel_x + panel_w - content_margin, panel_y + content_margin + qr_h),
        (panel_x + content_margin, panel_y + panel_h - content_margin - qr_h, panel_x + content_margin + qr_w, panel_y + panel_h - content_margin),
        (panel_x + panel_w - content_margin - qr_w, panel_y + panel_h - content_margin - qr_h, panel_x + panel_w - content_margin, panel_y + panel_h - content_margin),
    ]
    qr_pin_pad = max(38, w // 20)
    valid_qr = [r for r in qr_candidates if not rect_hits_pin(r, pad=qr_pin_pad) and not rect_intersects(r, text_card)]
    qr_card = valid_qr[0] if valid_qr else farthest_rect([r for r in qr_candidates if not rect_intersects(r, text_card)] or qr_candidates)

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    odraw.rounded_rectangle(text_card, radius=max(16, w // 70), fill=(250, 245, 236, 172), outline=(255, 251, 245, 186), width=2)
    odraw.rounded_rectangle(qr_card, radius=max(14, w // 90), fill=(250, 245, 236, 176), outline=(255, 251, 245, 188), width=2)
    canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    text_inset = max(18, w // 72)
    tx1, ty1, tx2, ty2 = text_card
    text_x, text_y = tx1 + text_inset, ty1 + text_inset - 2
    draw.text((text_x, text_y), country or "未知地点", fill=(32, 27, 20), font=title_font)
    draw.text(
        (text_x, text_y + int(title_size * 1.2)),
        format_date_for_display(date_text) if date_text else "日期未知",
        fill=(74, 63, 49),
        font=meta_font,
    )
    quote_top = text_y + int(title_size * 2.08)
    quote_max_w = max(220, (tx2 - tx1) - text_inset * 2)
    wrapped = wrap_text_by_pixels(draw, quote_text, quote_font, quote_max_w, max_lines=4)
    draw.multiline_text((text_x, quote_top), wrapped, fill=(24, 20, 15), font=quote_font, spacing=max(8, w // 130))

    qr = generate_qr_image(session_id).resize((qr_size, qr_size), Image.Resampling.NEAREST)
    qx1, qy1, qx2, qy2 = qr_card
    qx = qx1 + (qx2 - qx1 - qr_size) // 2
    qy = qy1 + (qy2 - qy1 - qr_size) // 2
    canvas.paste(qr, (qx, qy))

    if with_watermark:
        overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        wm_font = get_font(max(18, w // 58), bold=True)
        for i in range(0, w, 240):
            for j in range(0, h, 190):
                odraw.text((i, j), "PREVIEW", fill=(255, 255, 255, 72), font=wm_font)
        canvas = Image.alpha_composite(canvas.convert("RGBA"), overlay).convert("RGB")
        canvas = ImageEnhance.Sharpness(canvas).enhance(0.85)

    buf = io.BytesIO()
    canvas.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def show_api_sidebar() -> Tuple[ApiConfig, ApiConfig]:
    def cfg_value(key: str, default: str = "") -> str:
        # Prefer Streamlit secrets, then fallback to environment variables.
        try:
            if key in st.secrets:
                v = st.secrets[key]
                return v if isinstance(v, str) else str(v)
        except Exception:
            pass
        return os.getenv(key, default)

    default_base = cfg_value("OPENAI_BASE_URL", "https://api.openai.com/v1")
    default_key = cfg_value("OPENAI_API_KEY", "")
    default_model = cfg_value("OPENAI_MODEL", "gpt-4.1-mini")
    default_vision_model = cfg_value("VISION_MODEL", "gpt-4.1-mini")
    default_text_model = cfg_value("TEXT_MODEL", default_model)

    v_base = cfg_value("VISION_BASE_URL", default_base)
    v_key = cfg_value("VISION_API_KEY", default_key)
    v_model = default_vision_model
    vision_cfg = ApiConfig(v_base, v_key, v_model)

    t_base = cfg_value("TEXT_BASE_URL", v_base)
    t_key = cfg_value("TEXT_API_KEY", v_key)
    t_model = cfg_value("TEXT_MODEL", default_text_model)
    text_cfg = ApiConfig(t_base, t_key, t_model)
    return vision_cfg, text_cfg


def main() -> None:
    st.set_page_config(page_title=APP_TITLE, layout="wide")
    cleanup_temp_files()
    init_state()
    apply_theme()
    st.markdown(
        """
<div class="hero fade-in">
  <p class="kicker">暖调海报工作台</p>
  <h1>照片引语地图</h1>
  <p>上传一张照片，自动生成带地图定位、地点标签和一句文案的海报。</p>
</div>
""",
        unsafe_allow_html=True,
    )
    st.markdown(
        """
<div class="workflow-grid fade-in">
  <div class="workflow-chip"><strong>1) 上传</strong><br/>支持 JPG/PNG，可读取 EXIF GPS。</div>
  <div class="workflow-chip"><strong>2) 生成</strong><br/>自动识别图片内容并生成文案与海报。</div>
  <div class="workflow-chip"><strong>3) 导出</strong><br/>先预览，再导出高清 PNG。</div>
</div>
""",
        unsafe_allow_html=True,
    )

    vision_cfg, text_cfg = show_api_sidebar()
    uploaded_bytes: Optional[bytes] = None
    img: Optional[Image.Image] = None
    exif: Dict[str, Any] = {"date": "", "gps": None}
    auto_city, auto_country = "", ""
    source_bytes: Optional[bytes] = None
    effective_location = ""
    effective_date = ""
    ready_to_generate = False

    left, right = st.columns([1, 1], gap="large")
    with left:
        st.markdown('<p class="panel-kicker">步骤 1</p><h3 class="panel-title">上传原图</h3>', unsafe_allow_html=True)
        uploaded = st.file_uploader(
            "上传 JPG/PNG 图片",
            type=["jpg", "jpeg", "png"],
            label_visibility="collapsed",
            key="photo_uploader",
        )
        if uploaded:
            uploaded_bytes = uploaded.getvalue()
            photo_hash = hashlib.md5(uploaded_bytes).hexdigest()
            upload_id = str(getattr(uploaded, "file_id", "") or f"{uploaded.name}:{uploaded.size}:{photo_hash}")
            is_new_pick = st.session_state.last_upload_id != upload_id
            if is_new_pick:
                st.session_state.last_upload_id = upload_id
                st.session_state.preview_bytes = None
                st.session_state.hd_bytes = None
                st.session_state.vision_result = None
                st.session_state.quote_text = ""
                st.session_state.regen_remaining = -1
                st.session_state.regen_serial = 0
                st.session_state.autogen_done_hash = ""
                st.session_state.autogen_attempted_hash = ""
                st.session_state.autogen_done_sig = ""
                st.session_state.autogen_attempted_sig = ""
                if "manual_location_input" in st.session_state:
                    del st.session_state["manual_location_input"]
                if "manual_date_input" in st.session_state:
                    del st.session_state["manual_date_input"]

            if st.session_state.photo_hash != photo_hash:
                st.session_state.photo_hash = photo_hash
            elif is_new_pick:
                # Same file selected again: keep hash, but allow fresh auto-generation.
                st.session_state.autogen_done_hash = ""
                st.session_state.autogen_attempted_hash = ""
                st.session_state.autogen_done_sig = ""
                st.session_state.autogen_attempted_sig = ""
            st.session_state.source_image_bytes = uploaded_bytes
            img = Image.open(io.BytesIO(uploaded_bytes)).convert("RGB")
            exif = parse_exif(img)
            auto_city, auto_country = reverse_location_from_gps(exif["gps"]) if exif["gps"] else ("", "")
            st.image(img, use_container_width=True)
        elif st.session_state.source_image_bytes:
            uploaded_bytes = st.session_state.source_image_bytes
            if not st.session_state.photo_hash and uploaded_bytes:
                st.session_state.photo_hash = hashlib.md5(uploaded_bytes).hexdigest()
            img = Image.open(io.BytesIO(uploaded_bytes)).convert("RGB")
            exif = parse_exif(img)
            auto_city, auto_country = reverse_location_from_gps(exif["gps"]) if exif["gps"] else ("", "")
            st.image(img, use_container_width=True)
        else:
            st.markdown(
                '<div class="empty-stage">尚未上传图片。<br/>建议使用主体清晰的街景/旅行照片，识别与文案效果更好。</div>',
                unsafe_allow_html=True,
            )

    with right:
        if not st.session_state.preview_bytes:
            st.markdown('<p class="panel-kicker">步骤 2</p><h3 class="panel-title">识别拍摄信息</h3>', unsafe_allow_html=True)
            source_bytes = uploaded_bytes or st.session_state.source_image_bytes
            manual_mode = False
            manual_triggered = False
            if source_bytes:
                auto_date_text = (exif.get("date") or "").strip()
                auto_location_text = (f"{auto_city}, {auto_country}" if auto_city and auto_country else (auto_city or auto_country or "")).strip()
                st.markdown(
                    f'<div class="meta-strip">自动识别地点：{auto_location_text or "未识别"}<br/>自动识别时间：{auto_date_text or "未识别"}</div>',
                    unsafe_allow_html=True,
                )

                needs_manual_location = not auto_location_text
                needs_manual_date = not auto_date_text
                manual_mode = needs_manual_location or needs_manual_date
                if manual_mode:
                    st.warning("未完整识别地点或拍摄时间，请手动填写后再继续。")

                manual_location = ""
                if needs_manual_location:
                    manual_location = st.text_input("地点（必填）", value=st.session_state.get("manual_location_input", ""), key="manual_location_input")

                manual_date_value = datetime.now().date()
                if auto_date_text:
                    try:
                        manual_date_value = datetime.strptime(auto_date_text, "%Y-%m-%d").date()
                    except Exception:
                        manual_date_value = datetime.now().date()
                manual_date = None
                if needs_manual_date:
                    manual_date = st.date_input("拍摄日期（必填）", value=manual_date_value, key="manual_date_input")

                effective_location = auto_location_text or manual_location.strip()
                effective_date = auto_date_text or (str(manual_date) if manual_date else "")
                ready_to_generate = bool(effective_location and effective_date)

                if manual_mode:
                    manual_triggered = st.button("开始生成预览", type="primary", use_container_width=True, disabled=not ready_to_generate)
                    if not ready_to_generate:
                        st.info("请先补全地点和拍摄时间，再点击按钮开始生成。")
                else:
                    st.caption("地点与拍摄时间已自动识别，正在自动生成预览。")

            current_hash = st.session_state.photo_hash
            current_sig = f"{current_hash}|{effective_location}|{effective_date}"
            auto_should_run = bool(
                source_bytes
                and not manual_mode
                and current_hash
                and st.session_state.autogen_done_sig != current_sig
                and st.session_state.autogen_attempted_sig != current_sig
                and ready_to_generate
            )
            manual_should_run = bool(source_bytes and manual_mode and manual_triggered and ready_to_generate)

            if auto_should_run or manual_should_run:
                st.session_state.autogen_attempted_hash = current_hash
                st.session_state.autogen_attempted_sig = current_sig
                source_img = Image.open(io.BytesIO(source_bytes)).convert("RGB")
                source_exif = parse_exif(source_img)
                progress_holder = st.empty()
                progress_text = st.empty()
                bar = progress_holder.progress(0)
                progress_text.caption("进度 0%")
                try:
                    bar.progress(10)
                    progress_text.caption("进度 10%：准备图片数据")
                    vision_data_url = prepare_vision_data_url(source_bytes, "image/jpeg")
                    bar.progress(35)
                    progress_text.caption("进度 35%：识别图片内容")
                    vision = call_vision_api(vision_cfg, vision_data_url)
                    if st.session_state.get("vision_source") != "api":
                        st.session_state.preview_bytes = None
                        st.error(f"视觉识别失败，已跳过文案生成：{st.session_state.get('vision_error', '未知错误')}")
                        st.stop()
                    style = "冰心风"
                    display_date = format_date_for_display(effective_date)
                    bar.progress(55)
                    progress_text.caption("进度 55%：解析地点信息")
                    pin = source_exif["gps"] or geocode_country_center(effective_location)
                    city_from_pin, country_from_pin = reverse_location_from_gps(pin) if pin else ("", "")
                    if city_from_pin and (country_from_pin or effective_location):
                        use_country = country_from_pin or effective_location
                        location_label = f"{city_from_pin}, {use_country}"
                    else:
                        location_label = city_from_pin or auto_city or effective_location
                    bar.progress(75)
                    progress_text.caption("进度 75%：生成文案")
                    quote = call_quote_api(
                        text_cfg,
                        location_label,
                        display_date,
                        vision,
                        style,
                        previous_quote="",
                        variation_seed=0,
                    )

                    st.session_state.last_style = style
                    st.session_state.vision_result = vision
                    st.session_state.quote_text = quote
                    st.session_state.last_country = location_label
                    st.session_state.last_date = display_date
                    st.session_state.last_pin = pin
                    st.session_state.hd_bytes = None
                    bar.progress(92)
                    progress_text.caption("进度 92%：渲染预览海报")
                    st.session_state.preview_bytes = render_poster(
                        source_img,
                        location_label,
                        display_date,
                        quote,
                        st.session_state.session_id,
                        PREVIEW_LONG_EDGE,
                        with_watermark=True,
                        pin_latlon=pin,
                    )
                    st.session_state.autogen_done_hash = current_hash
                    st.session_state.autogen_done_sig = current_sig
                    bar.progress(100)
                    progress_text.caption("进度 100%：完成")
                finally:
                    progress_holder.empty()
                    progress_text.empty()
                st.rerun()

        if st.session_state.preview_bytes:
            st.markdown('<p class="panel-kicker">步骤 3</p><h3 class="panel-title">预览与导出</h3>', unsafe_allow_html=True)
            st.image(st.session_state.preview_bytes, use_container_width=True)
            st.markdown('<p class="preview-hint">先预览版式与文案，再导出高清图。</p>', unsafe_allow_html=True)
            if st.session_state.get("vision_source") == "fallback":
                st.warning(f'视觉识别回退：{st.session_state.get("vision_error", "未知错误")}')
            if st.session_state.get("quote_source") == "fallback":
                st.warning(f'文案接口回退：{st.session_state.get("quote_error", "未知错误")}')
            action_left, action_right = st.columns(2, gap="small")
            with action_left:
                regen_clicked = st.button("重新生成文案", use_container_width=True)
            with action_right:
                hd_clicked = st.button("生成高清图", use_container_width=True)

            if regen_clicked:
                source_bytes = st.session_state.source_image_bytes
                if not source_bytes:
                    st.error("原图不存在，请重新上传。")
                    st.stop()
                source_img = Image.open(io.BytesIO(source_bytes)).convert("RGB")
                progress_holder = st.empty()
                progress_text = st.empty()
                bar = progress_holder.progress(0)
                progress_text.caption("进度 0%")
                try:
                    bar.progress(15)
                    progress_text.caption("进度 15%：准备重生成参数")
                    prev_quote = st.session_state.quote_text
                    prev_clean = sanitize_quote(prev_quote)
                    st.session_state.regen_serial += 1
                    new_quote = prev_quote
                    # Hard guarantee: retry multiple times with different seeds until text changes.
                    for i in range(5):
                        bar.progress(min(80, 25 + i * 12))
                        progress_text.caption(f"进度 {min(80, 25 + i * 12)}%：生成候选文案 {i + 1}/5")
                        attempt_seed = st.session_state.regen_serial + i * 131
                        candidate = call_quote_api(
                            text_cfg,
                            st.session_state.last_country,
                            st.session_state.last_date,
                            st.session_state.vision_result or {},
                            st.session_state.last_style,
                            previous_quote=prev_quote,
                            variation_seed=attempt_seed,
                        )
                        if sanitize_quote(candidate) != prev_clean:
                            new_quote = candidate
                            break

                    # Final fallback if API keeps returning the same output.
                    if sanitize_quote(new_quote) == prev_clean:
                        bar.progress(84)
                        progress_text.caption("进度 84%：启用差异化回退")
                        forced = _style_fallback_quote(
                            st.session_state.last_style,
                            st.session_state.last_country,
                            st.session_state.last_date,
                            st.session_state.vision_result or {},
                            previous_quote=prev_quote,
                            variation_seed=st.session_state.regen_serial + 997,
                        )
                        if sanitize_quote(forced) == prev_clean:
                            tails = ["此刻风过树梢", "晚光正落在肩上", "街角有很轻的风", "云影慢慢向前走"]
                            extra = tails[st.session_state.regen_serial % len(tails)]
                            forced = sanitize_quote(re.sub(r"[。！？]$", "", prev_clean) + "，" + extra)
                        new_quote = forced
                    st.session_state.quote_text = new_quote
                    bar.progress(92)
                    progress_text.caption("进度 92%：重绘预览海报")
                    st.session_state.preview_bytes = render_poster(
                        source_img,
                        st.session_state.last_country,
                        st.session_state.last_date,
                        new_quote,
                        st.session_state.session_id,
                        PREVIEW_LONG_EDGE,
                        with_watermark=True,
                        pin_latlon=st.session_state.last_pin,
                    )
                    st.session_state.hd_bytes = None
                    bar.progress(100)
                    progress_text.caption("进度 100%：完成")
                finally:
                    progress_holder.empty()
                    progress_text.empty()
                st.rerun()

            if hd_clicked:
                source_bytes = st.session_state.source_image_bytes
                if not source_bytes:
                    st.error("原图不存在，请重新上传。")
                    st.stop()
                source_img = Image.open(io.BytesIO(source_bytes)).convert("RGB")
                progress_holder = st.empty()
                progress_text = st.empty()
                bar = progress_holder.progress(0)
                progress_text.caption("进度 0%")
                try:
                    bar.progress(20)
                    progress_text.caption("进度 20%：准备高清渲染")
                    st.session_state.hd_bytes = render_poster(
                        source_img,
                        st.session_state.last_country,
                        st.session_state.last_date,
                        st.session_state.quote_text,
                        st.session_state.session_id,
                        HD_LONG_EDGE,
                        with_watermark=False,
                        pin_latlon=st.session_state.last_pin,
                    )
                    bar.progress(100)
                    progress_text.caption("进度 100%：完成")
                finally:
                    progress_holder.empty()
                    progress_text.empty()
                st.success("高清图已生成。")

            if st.session_state.hd_bytes:
                st.download_button(
                    label="下载高清 PNG",
                    data=st.session_state.hd_bytes,
                    file_name=f"photoquote_{st.session_state.session_id[:8]}.png",
                    mime="image/png",
                    use_container_width=True,
                )


if __name__ == "__main__":
    main()
