# meta developer: @NeoKirilX
# scope: hikka

import io
import os
import json
import shutil
import asyncio
import tempfile
import textwrap
import logging
import subprocess
import urllib.request
from .. import loader, utils

# meta dependencies: Pillow, pilmoji
from telethon.tl.types import (
    DocumentAttributeAnimated,
    DocumentAttributeVideo,
    DocumentAttributeFilename,
)
from PIL import Image, ImageFont
from pilmoji import Pilmoji

logger = logging.getLogger(__name__)

_UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

_ASSETS_BASE = "https://raw.githubusercontent.com/NeoKirilX/Heroku-Modules/main"
_FRAME_URL = _ASSETS_BASE + "/assets/frames/icon.png"
_ROBOTO_URL = _ASSETS_BASE + "/assets/fonts/Roboto-Regular.ttf"
_MOVEMENT_URL = _ASSETS_BASE + "/assets/fonts/font.ttf"

CANVAS = 1080
BOX_X, BOX_Y, BOX_W, BOX_H = 162, 192, 756, 691

_VIDEO_EXT = {
    ".mp4", ".webm", ".mkv", ".mov", ".avi", ".m4v", ".flv", ".ts",
    ".mpeg", ".mpg", ".3gp", ".wmv",
}
_IMAGE_EXT = {
    ".jpg", ".jpeg", ".png", ".bmp", ".webp", ".tif", ".tiff", ".heic",
    ".jfif", ".ico",
}
_MIME_EXT = {
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-matroska": ".mkv",
    "video/x-msvideo": ".avi",
    "video/x-flv": ".flv",
    "video/mpeg": ".mpeg",
    "video/x-m4v": ".m4v",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/png": ".png",
    "image/jpeg": ".jpg",
}

class GoldFrameMod(loader.Module):
    """Модуль для наложения золотой рамки на фото, картинки (png/webp/bmp/...), гифки и видео (mp4/webm/mov/mkv/...), а также анимированные стикеры, с авто-скачиванием рамки и шрифтов"""
    strings = {"name": "GoldFrame"}

    async def client_ready(self, client, db):
        self._assets_checked = False

    @staticmethod
    def _download(url: str, dest: str) -> bool:
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        req = urllib.request.Request(url, headers=_UA)
        with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
            shutil.copyfileobj(resp, f)
        return True

    async def _ensure_assets(self) -> dict:
        root = os.getcwd()
        targets = {
            "frame": (os.path.join(root, "assets", "frames", "icon.png"), _FRAME_URL),
            "roboto": (os.path.join(root, "assets", "fonts", "Roboto-Regular.ttf"), _ROBOTO_URL),
            "movement": (os.path.join(root, "assets", "fonts", "font.ttf"), _MOVEMENT_URL),
        }
        result = {k: v[0] for k, v in targets.items()}
        if self._assets_checked:
            return result
        for key, (path, url) in targets.items():
            if not os.path.exists(path):
                try:
                    await asyncio.to_thread(self._download, url, path)
                    logger.info("Скачал %s -> %s", url, path)
                except Exception as e:
                    logger.warning("Не удалось скачать %s: %s", url, e)
        self._assets_checked = True
        return result

    @staticmethod
    def _file_name(doc) -> str:
        for attr in getattr(doc, "attributes", None) or []:
            if isinstance(attr, DocumentAttributeFilename):
                return attr.file_name or ""
        return ""

    def _media_info(self, reply):
        if reply is None:
            return None
        if reply.photo:
            return {"kind": "still"}
        doc = (
            reply.sticker
            or reply.gif
            or reply.video
            or reply.video_note
            or reply.document
        )
        if doc is None and reply.web_preview is not None:
            wp = reply.web_preview
            doc = wp.get("document") if isinstance(wp, dict) else getattr(wp, "document", None)
        if doc is None:
            return None
        mime = getattr(doc, "mime_type", "") or ""
        attrs = list(getattr(doc, "attributes", None) or [])
        ext = os.path.splitext(self._file_name(doc))[1].lower()
        is_animated_attr = any(isinstance(a, DocumentAttributeAnimated) for a in attrs)

        if (mime == "application/x-tgsticker") or ext == ".tgs":
            return {"kind": "tgs"}
        if mime.startswith("video/") or ext in _VIDEO_EXT:
            return {"kind": "video", "animate": bool(is_animated_attr or mime == "video/webm")}
        if mime in ("image/gif",) or ext == ".gif":
            return {"kind": "animated"}
        if mime == "image/webp" or ext == ".webp":
            return {"kind": "webp"}
        if mime.startswith("image/") or ext in _IMAGE_EXT:
            return {"kind": "still"}
        if mime == "application/octet-stream":
            if ext in _VIDEO_EXT:
                return {"kind": "video"}
            if ext in _IMAGE_EXT:
                return {"kind": "still"}
        return {"kind": "unknown"}

    def _src_ext(self, doc) -> str:
        name = self._file_name(doc)
        ext = os.path.splitext(name)[1].lower()
        if ext in _VIDEO_EXT or ext in _IMAGE_EXT:
            return ext
        mime = getattr(doc, "mime_type", "") or ""
        return _MIME_EXT.get(mime, ".mp4")

    def _pick_font(self, paths: dict, text: str, size: int):
        printable = [c for c in (text or "") if not c.isspace()]
        ascii_only = all(ord(c) < 128 for c in printable)
        order = [paths["roboto"], paths["movement"]]
        if ascii_only:
            order = [paths["movement"], paths["roboto"]]
        for p in order:
            if os.path.exists(p):
                try:
                    return ImageFont.truetype(p, size)
                except Exception:
                    continue
        return ImageFont.load_default()

    def _draw_text(self, img, text, paths: dict):
        font_size, spacing, padding, width = 40, 10, 50, 42
        font = self._pick_font(paths, text, font_size)
        lines = textwrap.wrap(text, width=width) or [text]

        heights = []
        for line in lines:
            try:
                bbox = font.getbbox(line)
                h = bbox[3] - bbox[1] if (bbox and len(bbox) >= 4) else font_size
            except Exception:
                h = font_size
            heights.append(h if h > 0 else font_size)

        total = sum(heights) + spacing * (len(lines) - 1) + padding * 2
        extended = Image.new("RGB", (CANVAS, CANVAS + total), (255, 255, 255))
        extended.paste(img, (0, 0))

        y = CANVAS + padding
        with Pilmoji(extended) as pilmoji_draw:
            for i, line in enumerate(lines):
                try:
                    bbox = font.getbbox(line)
                    w = bbox[2] - bbox[0] if (bbox and len(bbox) >= 3) else 0
                except Exception:
                    w = len(line) * (font_size // 2)
                x = (CANVAS - w) // 2
                if x < 0:
                    x = padding
                pilmoji_draw.text((x, y), line, font=font, fill=(0, 0, 0, 255))
                y += heights[i] + spacing
        return extended

    def _frame_layer(self, path: str):
        frame = Image.open(path).convert("RGBA")
        return frame.resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)

    def _render_still(self, data: bytes, paths: dict, text: str) -> io.BytesIO:
        user = Image.open(io.BytesIO(data)).convert("RGBA")
        frame = self._frame_layer(paths["frame"])
        base = Image.new("RGB", (CANVAS, CANVAS), (255, 255, 255))
        base.paste(
            user.convert("RGB").resize((BOX_W, BOX_H), Image.Resampling.LANCZOS),
            (BOX_X, BOX_Y),
        )
        base.paste(frame, (0, 0), frame)
        if text:
            base = self._draw_text(base, text, paths)
        out = io.BytesIO()
        base.save(out, "JPEG", quality=95)
        out.seek(0)
        return out

    def _build_video_bg(self, paths: dict, text: str):
        frame = self._frame_layer(paths["frame"])
        base = Image.new("RGBA", (CANVAS, CANVAS), (255, 255, 255, 255))
        base.paste(frame, (0, 0), frame)
        if text:
            base = self._draw_text(base.convert("RGB"), text, paths).convert("RGBA")
        if base.size[1] % 2:
            taller = Image.new("RGBA", (CANVAS, base.size[1] + 1), (255, 255, 255, 255))
            taller.paste(base, (0, 0))
            base = taller
        return base

    @staticmethod
    def _ffprobe(path: str) -> dict:
        try:
            proc = subprocess.run(
                ["ffprobe", "-v", "error", "-count_frames", "-print_format", "json",
                 "-show_streams", "-show_format", path],
                capture_output=True, text=True,
            )
            data = json.loads(proc.stdout or "{}")
        except Exception:
            return {}

        w = h = 0
        has_audio = False
        duration = 0.0
        best_stream_dur = 0.0
        for s in data.get("streams", []):
            if s.get("codec_type") == "video":
                w = s.get("width") or w
                h = s.get("height") or h
                rate = s.get("avg_frame_rate", "0/1").split("/")
                frames = s.get("nb_read_frames")
                try:
                    num, den = float(rate[0]), float(rate[1])
                    if num > 0 and den > 0 and frames:
                        best_stream_dur = max(best_stream_dur, int(frames) * den / num)
                except Exception:
                    pass
            if s.get("codec_type") == "audio":
                has_audio = True
        try:
            duration = float(data.get("format", {}).get("duration") or 0)
        except Exception:
            duration = 0.0
        if duration <= 0:
            duration = best_stream_dur
        return {"w": w, "h": h, "duration": duration, "has_audio": has_audio}

    async def _render_video(self, src_path: str, paths: dict, text: str) -> tuple:
        bg = self._build_video_bg(paths, text)
        bg_path = os.path.join(os.path.dirname(src_path), "_bg.png")
        bg.save(bg_path)

        info = await asyncio.to_thread(self._ffprobe, src_path)
        dur = info.get("duration") or 0.0
        bound = dur if dur > 0 else 30.0

        bg_h = bg.size[1]
        filter_ = (
            f"[0:v]scale={BOX_W}:{BOX_H}:force_original_aspect_ratio=increase,"
            f"crop={BOX_W}:{BOX_H},setsar=1,"
            f"pad={CANVAS}:{bg_h}:{BOX_X}:{BOX_Y}[b];"
            f"[b][1:v]overlay=0:0,format=yuv420p"
        )
        out_path = os.path.join(os.path.dirname(src_path), "_framed.mp4")

        cmd = [
            "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
            "-i", src_path, "-loop", "1", "-i", bg_path,
            "-filter_complex", filter_,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        ]
        cmd += ["-t", f"{bound + 0.5:.2f}"]
        if info.get("has_audio"):
            cmd += ["-c:a", "aac", "-b:a", "128k"]
        cmd += [out_path]

        proc = await asyncio.create_subprocess_exec(
            *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE
        )
        _, err = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError("ffmpeg: " + (err.decode("utf-8", "ignore") or "unknown error")[-500:])

        with open(out_path, "rb") as f:
            data = f.read()
        out_info = await asyncio.to_thread(self._ffprobe, out_path)
        return data, out_info

    @loader.command(ru_doc="Накладывает рамку на фото, картинку, гифку или видео. Работает с .mp4/.webm/.mov/.mkv/.png/.webp/.gif и т.д. Использование: .рамка [т текст]")
    async def рамка(self, message):
        reply = await message.get_reply_message()
        info = self._media_info(reply)
        if not reply or not info:
            await utils.answer(message, "<b>Ответь на сообщение с медиа командой .рамка</b>")
            return
        if info["kind"] == "tgs":
            await utils.answer(
                message,
                "❌ <b>TGS-стикеры пока не поддерживаются.</b> Пришли гифку, видео или обычный стикер.",
            )
            return
        if info["kind"] == "unknown":
            await utils.answer(message, "❌ <b>Не удалось определить тип медиа.</b> Пришли фото, картинку, гифку или видео.")
            return

        paths = await self._ensure_assets()
        if not os.path.exists(paths["frame"]):
            await utils.answer(
                message,
                "❌ <b>Файл рамки не найден и не смог скачаться.</b> Путь:\n"
                f"<code>{paths['frame']}</code>\n\n"
                f"Скачай вручную: <code>{_FRAME_URL}</code> и положи в эту папку.",
            )
            return

        text_to_draw = None
        args = utils.get_args_raw(message)
        if args:
            parts = args.split(maxsplit=1)
            if parts[0].lower() == "т" and len(parts) > 1:
                text_to_draw = parts[1].strip()
            elif parts[0].lower() == "т" and len(parts) == 1:
                if reply.text:
                    text_to_draw = reply.text
            else:
                text_to_draw = args.strip()

        await message.edit("<code>Рендеринг...</code>")

        try:
            kind = info["kind"]
            doc = (
                reply.sticker
                or reply.gif
                or reply.video
                or reply.video_note
                or reply.document
            )
            animate = bool(info.get("animate"))

            if kind in ("video", "animated", "webp"):
                ext = self._src_ext(doc)
                with tempfile.TemporaryDirectory() as td:
                    src_path = os.path.join(td, "input" + ext)
                    await message.client.download_file(reply.media, file=src_path)

                    if kind == "webp":
                        loop = asyncio.get_running_loop()
                        img_is_anim = await loop.run_in_executor(
                            None, self._is_animated_image, src_path
                        )
                        if not img_is_anim:
                            with open(src_path, "rb") as f:
                                still_bytes = f.read()
                            out = await loop.run_in_executor(
                                None, self._render_still, still_bytes, paths, text_to_draw
                            )
                            out.name = "framed.jpg"
                            await self._send_still(message, reply, out)
                            return

                    data, out_info = await self._render_video(src_path, paths, text_to_draw)

                animate = animate or kind in ("animated", "webp")
                buf = io.BytesIO(data)
                if animate:
                    buf.name = "framed.gif"
                    attributes = [
                        DocumentAttributeAnimated(),
                        DocumentAttributeFilename("framed.gif"),
                    ]
                else:
                    buf.name = "framed.mp4"
                    duration = int(out_info.get("duration") or 1)
                    attributes = [
                        DocumentAttributeFilename("framed.mp4"),
                        DocumentAttributeVideo(
                            w=out_info.get("w") or 1,
                            h=out_info.get("h") or 1,
                            duration=duration,
                            supports_streaming=True,
                        ),
                    ]
                await message.delete()
                await message.client.send_file(
                    message.chat_id, buf, attributes=attributes, reply_to=reply.id
                )

            else:
                media_bytes = await message.client.download_file(reply.media, bytes)
                loop = asyncio.get_running_loop()
                out = await loop.run_in_executor(
                    None, self._render_still, media_bytes, paths, text_to_draw
                )
                out.name = "framed.jpg"
                await self._send_still(message, reply, out)

        except Exception as e:
            logger.exception("Рамка: ошибка")
            await utils.answer(message, f"❌ <b>Ошибка при создании рамки:</b> <code>{e}</code>")

    async def _send_still(self, message, reply, buf: io.BytesIO):
        await message.delete()
        await message.client.send_file(message.chat_id, buf, reply_to=reply.id)

    @staticmethod
    def _is_animated_image(path: str) -> bool:
        try:
            img = Image.open(path)
            return bool(getattr(img, "is_animated", False))
        except Exception:
            return False