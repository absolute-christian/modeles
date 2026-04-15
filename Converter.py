# meta developer: @absolute_christian
# scope: heroku_only
# requires: mutagen

import asyncio
import os
import tempfile
import logging
import re
import shutil
import sys
import importlib

from mutagen.id3 import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
from mutagen.mp3 import MP3

from .. import loader, utils

logger = logging.getLogger(__name__)

EMOJI_LOADING  = '<tg-emoji emoji-id="5379916721194805395">❤️</tg-emoji>'
EMOJI_DONE     = '<tg-emoji emoji-id="5350369351948081060">🤩</tg-emoji>'
EMOJI_ERROR    = '<tg-emoji emoji-id="5204350290769229964">❤️</tg-emoji>'
EMOJI_SMEX     = '<tg-emoji emoji-id="5267032121523863056">😂</tg-emoji>'
EMOJI_TAG      = '<tg-emoji emoji-id="5269764708566599413">🤔</tg-emoji>'
EMOJI_COVER    = '<tg-emoji emoji-id="5422814644093868925">👨‍💻</tg-emoji>'
EMOJI_COMMANDS = '<tg-emoji emoji-id="5377835775180155940">❤️</tg-emoji>'
EMOJI_MUSIC    = '<tg-emoji emoji-id="5312445916005826522">📋</tg-emoji>'
COOKIE_HELP_VIDEO_URL = "https://your-video-link-here"


@loader.tds
class ConverterMod(loader.Module):
    """Конвертит мп4 в мп3. Умеет скачивать звук видоса ютуба (по ссылке)."""

    strings = {"name": "Converter"}

    def __init__(self):
        self._edit_sessions: dict[int, dict] = {}  # chat_id -> session
        self.config = loader.ModuleConfig(
            loader.ConfigValue(
                "yt_cookies_path",
                "",
                lambda: "Путь к cookies.txt для YouTube (опционально)",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "yt_js_runtimes",
                "node,deno",
                lambda: "JS runtime для yt-dlp (--js-runtimes), например: node,deno",
                validator=loader.validators.String(),
            ),
            loader.ConfigValue(
                "yt_extractor_args",
                "youtube:player_client=android,web",
                lambda: "yt-dlp --extractor-args для YouTube",
                validator=loader.validators.String(),
            ),
        )

    async def _run(self, *args) -> tuple[int, str, str]:
        proc = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=300)
        return proc.returncode, stdout.decode(), stderr.decode()

    async def _check_ffmpeg(self) -> bool:
        try:
            rc, _, _ = await self._run("ffmpeg", "-version")
            return rc == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

    async def _check_ytdlp(self) -> bool:
        return (await self._resolve_ytdlp_cmd()) is not None

    async def _resolve_ytdlp_cmd(self) -> list[str] | None:
        candidates: list[list[str]] = []
        bin_path = shutil.which("yt-dlp")
        if bin_path:
            candidates.append([bin_path])
        candidates.append([sys.executable, "-m", "yt_dlp"])

        for cmd in candidates:
            try:
                rc, _, _ = await self._run(*cmd, "--version")
                if rc == 0:
                    return cmd
            except (FileNotFoundError, asyncio.TimeoutError):
                continue
        return None

    def _build_ytdlp_cmd(self, ytdlp_cmd: list[str], out_tpl: str, url: str) -> list[str]:
        cmd = [
            *ytdlp_cmd,
            "--no-playlist",
            "-x",
            "--audio-format",
            "mp3",
            "--audio-quality",
            "192K",
            "--embed-thumbnail",
            "--add-metadata",
            "--print",
            "after_move:filepath",
        ]

        js_runtimes = str(self.config["yt_js_runtimes"] or "").strip()
        if js_runtimes:
            cmd.extend(["--js-runtimes", js_runtimes])

        extractor_args = str(self.config["yt_extractor_args"] or "").strip()
        if extractor_args:
            cmd.extend(["--extractor-args", extractor_args])

        cookies_path = str(self.config["yt_cookies_path"] or "").strip()
        if cookies_path and os.path.isfile(cookies_path):
            cmd.extend(["--cookies", cookies_path])

        cmd.extend(["-o", out_tpl, url])
        return cmd

    def _build_ytdlp_opts(self, out_tpl: str) -> dict:
        opts: dict = {
            "format": "bestaudio",
            "addmetadata": True,
            "prefer_ffmpeg": True,
            "geo_bypass": True,
            "nocheckcertificate": True,
            "noplaylist": True,
            "quiet": True,
            "outtmpl": out_tpl,
            "extractor_args": {
                "youtube": {
                    "player_client": ["android", "web"],
                }
            },
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                },
                {"key": "EmbedThumbnail"},
                {"key": "FFmpegMetadata"},
            ],
        }
        cookies_path = str(self.config["yt_cookies_path"] or "").strip()
        if cookies_path and os.path.isfile(cookies_path):
            opts["cookiefile"] = cookies_path
        return opts

    async def _download_with_ytdlp_lib(
        self,
        url: str,
        tmp_dir: str,
        out_tpl: str,
    ) -> tuple[bool, str]:
        def _job() -> tuple[bool, str]:
            try:
                yt_dlp = importlib.import_module("yt_dlp")
            except Exception as e:
                return False, f"yt_dlp import error: {e}"

            opts = self._build_ytdlp_opts(out_tpl)
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    ydl.extract_info(url, download=True)
            except Exception as e:
                return False, str(e)

            for fname in os.listdir(tmp_dir):
                if fname.lower().endswith(".mp3"):
                    return True, os.path.join(tmp_dir, fname)
            return False, "no mp3 produced"

        return await utils.run_sync(_job)

    def _extract_yt_url(self, raw: str, reply_text: str | None = None) -> str | None:
        blob = (raw or "").strip()
        if not blob and reply_text:
            blob = reply_text.strip()
        if not blob:
            return None

        m = re.search(r"(https?://[^\s]+)", blob, re.IGNORECASE)
        if not m:
            return None
        url = m.group(1).strip("()[]<>.,;\"'")
        low = url.lower()
        if "youtube.com/" in low or "youtu.be/" in low or "music.youtube.com/" in low:
            return url
        return None

    # .convert - конвертирует MP4 в MP3
    @loader.command()
    async def convertcmd(self, message):
        """Конвертит мп4 в мп3"""
        reply = await message.get_reply_message()

        if not reply or not reply.video and not (reply.document and reply.document.mime_type == "video/mp4"):
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Ответь на видео командой .convert</b>",
            )
            return

        await utils.answer(message, f"{EMOJI_LOADING} <b>Пока скачивается, попей молоко. Шучу... {EMOJI_SMEX}</b>")

        if not await self._check_ffmpeg():
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Библиотека ffmpeg не найден.</b>",
            )
            return

        tmp_dir = tempfile.mkdtemp()
        video_path = os.path.join(tmp_dir, "input.mp4")
        mp3_path   = os.path.join(tmp_dir, "output.mp3")
        thumb_path = os.path.join(tmp_dir, "thumb.jpg")

        try:
            await reply.download_media(video_path)

            await utils.answer(message, f"{EMOJI_LOADING} <b>Пока грузится, попей чай. Шучу... {EMOJI_SMEX}</b>")

            rc, _, err = await self._run(
                "ffmpeg", "-y", "-i", video_path,
                "-vframes", "1", "-q:v", "2",
                thumb_path,
            )
            has_thumb = rc == 0 and os.path.exists(thumb_path)

            rc, _, err = await self._run(
                "ffmpeg", "-y", "-i", video_path,
                "-vn", "-ar", "44100", "-ac", "2", "-b:a", "192k",
                mp3_path,
            )
            if rc != 0:
                logger.error("ffmpeg error: %s", err)
                await utils.answer(
                    message,
                    f"{EMOJI_ERROR} <b>Ошибка конвертации.</b>\n<code>{err[-300:]}</code>",
                )
                return

            try:
                audio = MP3(mp3_path, ID3=ID3)
            except ID3NoHeaderError:
                audio = MP3(mp3_path)
                audio.add_tags()

            audio.tags.add(TIT2(encoding=3, text="Ноунейм"))
            audio.tags.add(TPE1(encoding=3, text="Хз кто"))

            if has_thumb:
                with open(thumb_path, "rb") as f:
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=f.read(),
                        )
                    )

            audio.save()

            await utils.answer(message, f"{EMOJI_LOADING} <b>Отправляется...</b>")

            thumb_arg = open(thumb_path, "rb") if has_thumb else None
            try:
                await message.client.send_file(
                    message.chat_id,
                    mp3_path,
                    voice_note=False,
                    attributes=[],
                    caption=f"{EMOJI_DONE} <b>Вот файл.</b> Используй <code>.settag</code> чтобы редактнуть название/исполнителя.",
                    parse_mode="html",
                    thumb=thumb_arg,
                )
            finally:
                if thumb_arg:
                    thumb_arg.close()

            await message.delete()

        except asyncio.TimeoutError:
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Пизда.</b> Видео слишком большое или сервер занят.",
            )
        except Exception as e:
            logger.exception("convert error")
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Неожиданная ошибка:</b> <code>{type(e).__name__}: {e}</code>",
            )
        finally:
            for f in (video_path, mp3_path, thumb_path):
                try:
                    os.remove(f)
                except OSError:
                    pass
            try:
                os.rmdir(tmp_dir)
            except OSError:
                pass

    # .ytd - скачивает MP3 из YouTube
    @loader.command()
    async def ytdcmd(self, message):
        """Скачивает звук видоса с ютуба"""
        reply = await message.get_reply_message()
        raw = utils.get_args_raw(message) or ""
        reply_text = getattr(reply, "raw_text", None) if reply else None
        url = self._extract_yt_url(raw, reply_text)

        if not url:
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Укажи ссылку YouTube:</b>\n"
                f"<code>.ytd https://youtu.be/...</code>\n"
                f"<i>или ответь командой на сообщение с ссылкой.</i>",
            )
            return

        if not await self._check_ffmpeg():
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Библиотека ffmpeg не найден.</b>",
            )
            return

        await utils.answer(message, f"{EMOJI_LOADING} <b>Пока работаю, попей кофе. Шучу... {EMOJI_SMEX}</b>")

        tmp_dir = tempfile.mkdtemp(prefix="ytmp3_")
        out_tpl = os.path.join(tmp_dir, "%(title).200s [%(id)s].%(ext)s")
        out_mp3 = None

        try:
            # 1) Основной путь: python API yt_dlp (как в классическом ytdl модуле)
            ok_lib, payload = await self._download_with_ytdlp_lib(url, tmp_dir, out_tpl)
            if ok_lib:
                out_mp3 = payload
            else:
                # 2) Резерв: CLI yt-dlp
                ytdlp_cmd = await self._resolve_ytdlp_cmd()
                if not ytdlp_cmd:
                    await utils.answer(
                        message,
                        f"{EMOJI_ERROR} <b>yt-dlp не найден.</b>\n"
                        f"Установи в окружение юзербота: <code>python -m pip install -U yt-dlp</code>",
                    )
                    return
                cmd = self._build_ytdlp_cmd(ytdlp_cmd, out_tpl, url)
                rc, stdout, stderr = await self._run(*cmd)
                if rc != 0:
                    err_tail = (stderr or stdout or payload or "unknown error")[-900:]
                    if "Sign in to confirm you" in err_tail or "not a bot" in err_tail:
                        err_tail += (
                            "\n\nПодсказка: YouTube требует cookies.\n"
                            "Экспортируй cookies.txt и укажи путь в .config Converter -> yt_cookies_path\n"
                            "И открой .chelp"
                        )
                    await utils.answer(
                        message,
                        f"{EMOJI_ERROR} <b>Ошибка скачивания YouTube.</b>\n<code>{err_tail}</code>",
                    )
                    return

                lines = [x.strip() for x in (stdout or "").splitlines() if x.strip()]
                for ln in reversed(lines):
                    if ln.lower().endswith(".mp3") and os.path.exists(ln):
                        out_mp3 = ln
                        break

                if not out_mp3:
                    for fname in os.listdir(tmp_dir):
                        if fname.lower().endswith(".mp3"):
                            out_mp3 = os.path.join(tmp_dir, fname)
                            break

            if not out_mp3 or not os.path.exists(out_mp3):
                await utils.answer(
                    message,
                    f"{EMOJI_ERROR} <b>Не удалось найти итоговый мп3-файл.</b>",
                )
                return

            await utils.answer(message, f"{EMOJI_LOADING} <b>Пока отправляю мп3, попей воды. Шучу... {EMOJI_SMEX}</b>")
            await message.client.send_file(
                message.chat_id,
                out_mp3,
                voice_note=False,
                caption=f"{EMOJI_DONE} <b>Готово. Скачал видео из ютуба и конвертировал в мп3</b>",
                parse_mode="html",
            )
            await message.delete()

        except asyncio.TimeoutError:
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Бляя...</b> Видео слишком длинное или сервер занят.",
            )
        except Exception as e:
            logger.exception("ytd error")
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Неожиданная ошибка:</b> <code>{type(e).__name__}: {e}</code>",
            )
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # .chelp - показывает ссылку на гайд по cookies
    @loader.command()
    async def chelpcmd(self, message):
        """Показывает видео-гайд по получению cookies."""
        await utils.answer(
            message,
            f"{EMOJI_COMMANDS} <b>Гайд по cookies для YouTube:</b>\n"
            f"<a href=\"{COOKIE_HELP_VIDEO_URL}\">{COOKIE_HELP_VIDEO_URL}</a>",
        )

    # .settag - редактирует теги MP3
    @loader.command()
    async def settagcmd(self, message):
        """Редактирует название, исполнителя, обложку музыки."""
        args = (utils.get_args_raw(message) or "").strip()
        reply = await message.get_reply_message()
        chat_id = message.chat_id

        def is_mp3(msg) -> bool:
            if not msg:
                return False
            if getattr(msg, "audio", None):
                return True
            doc = getattr(msg, "document", None)
            if not doc:
                return False
            mime = str(getattr(doc, "mime_type", "") or "").lower()
            return mime in {"audio/mpeg", "audio/mp3"}

        session = self._edit_sessions.get(chat_id)

        target_audio = reply if is_mp3(reply) else None
        if not target_audio and session and session.get("reply_id"):
            try:
                saved = await message.client.get_messages(chat_id, ids=session["reply_id"])
                if is_mp3(saved):
                    target_audio = saved
            except Exception:
                target_audio = None

        if not args:
            if not target_audio:
                await utils.answer(
                    message,
                    f"{EMOJI_ERROR} <b>Ответь на мп3 командой .settag</b>",
                )
                return

            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id

            await utils.answer(message, f"{EMOJI_TAG} <b>Смотрю че там в музыке...</b>")
            tmp = tempfile.mktemp(suffix=".mp3")
            try:
                await target_audio.download_media(tmp)
                audio = MP3(tmp, ID3=ID3)
                title = str(audio.tags.get("TIT2", "—")) if audio.tags else "—"
                artist = str(audio.tags.get("TPE1", "—")) if audio.tags else "—"
                has_cover = bool(audio.tags and any(str(k).startswith("APIC") for k in audio.tags.keys()))

                await utils.answer(
                    message,
                    f" {EMOJI_MUSIC} <b>Параметры музыки:</b>\n"
                    f"• <b>Название:</b> {title}\n"
                    f"• <b>Исполнитель:</b> {artist}\n"
                    f"• <b>Обложка:</b> {'есть' if has_cover else 'нет'}\n\n"
                    f"<b>Команды:</b>\n"
                    f"<code>.settag title Название</code>\n"
                    f"<code>.settag artist Исполнитель</code>\n"
                    f"<code>.settag cover</code> - ответь на фото чтоб поменять обложку\n"
                    f"<code>.settag apply</code> - получить готовый мп3",
                )
            except Exception as e:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Ошибка чтения тегов:</b> <code>{e}</code>")
            finally:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return

        parts = args.split(None, 1)
        cmd = parts[0].lower()
        value = parts[1] if len(parts) > 1 else ""

        if cmd == "title":
            if not value:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Укажи название:</b> <code>.settag title Название</code>")
                return
            if not target_audio:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала ответь на мп3 командой .settag title ...</b>")
                return
            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id
            session["title"] = value
            await utils.answer(message, f"{EMOJI_DONE} <b>Название сохранено:</b> {value}\nЧтобы получить готовый мп3, введи <code>.settag apply</code>")
            return

        if cmd == "artist":
            if not value:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Укажи исполнителя:</b> <code>.settag artist Исполнитель</code>")
                return
            if not target_audio:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала ответь на мп3 командой .settag artist ...</b>")
                return
            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id
            session["artist"] = value
            await utils.answer(message, f"{EMOJI_DONE} <b>Исполнитель сохранён:</b> {value}\nЧтобы получить готовый мп3, введи <code>.settag apply</code>")
            return

        if cmd == "cover":
            if not target_audio:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала открой настройки мп3: .settag title ... (ответом на музыку)</b>")
                return

            photo_msg = message if getattr(message, "photo", None) else (reply if reply and getattr(reply, "photo", None) else None)
            if not photo_msg or not getattr(photo_msg, "photo", None):
                await utils.answer(message, f"{EMOJI_ERROR} <b>Ответь командой .settag cover на фото</b>")
                return

            await utils.answer(message, f"{EMOJI_COVER} <b>Смотрю на фотку и меняю иво.</b>")
            tmp_cover = tempfile.mktemp(suffix=".jpg")
            try:
                await photo_msg.download_media(tmp_cover)
                with open(tmp_cover, "rb") as f:
                    cover_data = f.read()
                session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
                session["reply_id"] = target_audio.id
                session["cover"] = cover_data
                await utils.answer(message, f"{EMOJI_DONE} <b>Обложка сохранена.</b>\nЧтобы получить готовый мп3, введи <code>.settag apply</code>")
            except Exception as e:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Ошибка:</b> <code>{e}</code>")
            finally:
                try:
                    os.remove(tmp_cover)
                except OSError:
                    pass
            return

        if cmd == "apply":
            session = self._edit_sessions.get(chat_id)
            if not target_audio or not session or session.get("reply_id") != target_audio.id:
                await utils.answer(
                    message,
                    f"{EMOJI_ERROR} <b>Нету никаких изминений для музыки, балбес.</b>\n"
                    f"Сначала задай <code>.settag title</code> / <code>.settag artist</code> / <code>.settag cover</code>",
                )
                return

            await utils.answer(message, f"{EMOJI_LOADING} <b>Применяю теги…</b>")
            tmp_mp3 = tempfile.mktemp(suffix=".mp3")
            thumb_tmp = None
            try:
                await target_audio.download_media(tmp_mp3)
                try:
                    audio = MP3(tmp_mp3, ID3=ID3)
                except ID3NoHeaderError:
                    audio = MP3(tmp_mp3)
                    audio.add_tags()

                if audio.tags is None:
                    audio.add_tags()

                if "title" in session:
                    audio.tags["TIT2"] = TIT2(encoding=3, text=session["title"])
                if "artist" in session:
                    audio.tags["TPE1"] = TPE1(encoding=3, text=session["artist"])
                if "cover" in session:
                    audio.tags.delall("APIC")
                    audio.tags.add(
                        APIC(
                            encoding=3,
                            mime="image/jpeg",
                            type=3,
                            desc="Cover",
                            data=session["cover"],
                        )
                    )

                audio.save()

                if "cover" in session:
                    thumb_tmp = tempfile.mktemp(suffix=".jpg")
                    with open(thumb_tmp, "wb") as f:
                        f.write(session["cover"])
                else:
                    apic_frame = None
                    if audio.tags:
                        for key, frame in audio.tags.items():
                            if str(key).startswith("APIC"):
                                apic_frame = frame
                                break
                    if apic_frame and getattr(apic_frame, "data", None):
                        thumb_tmp = tempfile.mktemp(suffix=".jpg")
                        with open(thumb_tmp, "wb") as f:
                            f.write(apic_frame.data)

                thumb_arg = open(thumb_tmp, "rb") if thumb_tmp else None
                try:
                    await message.client.send_file(
                        chat_id,
                        tmp_mp3,
                        voice_note=False,
                        caption=f"{EMOJI_DONE} <b>Теги обновлены!</b>",
                        parse_mode="html",
                        thumb=thumb_arg,
                    )
                finally:
                    if thumb_arg:
                        thumb_arg.close()

                self._edit_sessions.pop(chat_id, None)
                await message.delete()

            except Exception as e:
                logger.exception("settag apply error")
                await utils.answer(message, f"{EMOJI_ERROR} <b>Ошибка применения тегов:</b> <code>{type(e).__name__}: {e}</code>")
            finally:
                try:
                    os.remove(tmp_mp3)
                except OSError:
                    pass
                if thumb_tmp:
                    try:
                        os.remove(thumb_tmp)
                    except OSError:
                        pass
            return

        await utils.answer(
            message,
            f"{EMOJI_COMMANDS} <b>Каво нахуй?</b>\n"
            f"Доступны только эти команды - <code>title</code>, <code>artist</code>, <code>cover</code>, <code>apply</code>",
        )


