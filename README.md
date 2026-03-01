# PhotoQuote Map MVP (Streamlit)

基于 `PRD.md` 的本地 MVP，已打通：
- 上传单张照片（JPG/PNG）
- EXIF 读取（日期/GPS）+ 手动国家/日期兜底
- Vision 分析 + 金句生成（支持单独配置 API）
- 低清预览（带水印）+ 高清导出（当前不接支付）
- 二维码嵌入海报
- 换一句（最多 3 次，仅重跑文案）
- 临时文件 TTL 清理

## 1) 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## 2) 配置 API（可选）

复制环境变量模板：

```bash
copy .env.example .env
```

可选两种方式：
- 统一配置：`OPENAI_BASE_URL` + `OPENAI_API_KEY` + `OPENAI_MODEL`
- 分开配置：`VISION_*` 和 `TEXT_*`

如果不填 API，应用会使用本地兜底逻辑，便于先跑通流程。

## 3) 启动

```bash
streamlit run app.py
```

浏览器打开显示的本地地址（默认 `http://localhost:8501`）。

## 4) 当前范围说明

- 已按你的要求暂不接入付费逻辑。
- 海报地图为本地手绘风格示意，位置只显示国家级别。
- HEIC 暂未接入（MVP 先支持 JPG/PNG）。
