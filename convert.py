# meta developer: @absolute_christian
# scope: heroku_only
# requires: mutagen

import asyncio
import os
import tempfile
import logging
import re
import shutil

from mutagen.id3 import ID3, TIT2, TPE1, APIC, ID3NoHeaderError
from mutagen.mp3 import MP3

from .. import loader, utils

logger = logging.getLogger(__name__)

EMOJI_LOADING = '<tg-emoji emoji-id="5379916721194805395">❤️</tg-emoji>'
EMOJI_DONE    = '<tg-emoji emoji-id="5350369351948081060">🤩</tg-emoji>'
EMOJI_ERROR   = '<tg-emoji emoji-id="5204350290769229964">❤️</tg-emoji>'


@loader.tds
class ConverterMod(loader.Module):
    """Converter"""

    strings = {"name": "Converter"}

    def __init__(self):
        self._edit_sessions: dict[int, dict] = {}  # chat_id -> session

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
        try:
            rc, _, _ = await self._run("yt-dlp", "--version")
            return rc == 0
        except (FileNotFoundError, asyncio.TimeoutError):
            return False

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
        reply = await message.get_reply_message()

        if not reply or not reply.video and not (reply.document and reply.document.mime_type == "video/mp4"):
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Ответь на видео-сообщение командой .convert</b>",
            )
            return

        await utils.answer(message, f"{EMOJI_LOADING} <b>Скачиваю видео…</b>")

        if not await self._check_ffmpeg():
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>ffmpeg не найден.</b>\nУстанови его: <code>apt install ffmpeg</code>",
            )
            return

        tmp_dir = tempfile.mkdtemp()
        video_path = os.path.join(tmp_dir, "input.mp4")
        mp3_path   = os.path.join(tmp_dir, "output.mp3")
        thumb_path = os.path.join(tmp_dir, "thumb.jpg")

        try:
            await reply.download_media(video_path)

            await utils.answer(message, f"{EMOJI_LOADING} <b>Конвертирую…</b>")

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

            audio.tags.add(TIT2(encoding=3, text="Без названия"))
            audio.tags.add(TPE1(encoding=3, text="Неизвестный"))

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

            await utils.answer(message, f"{EMOJI_LOADING} <b>Отправляю…</b>")

            thumb_arg = open(thumb_path, "rb") if has_thumb else None
            try:
                await message.client.send_file(
                    message.chat_id,
                    mp3_path,
                    voice_note=False,
                    attributes=[],
                    caption=f"{EMOJI_DONE} <b>Готово!</b> Используй <code>.settag</code> чтобы задать название/исполнителя.",
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
                f"{EMOJI_ERROR} <b>Таймаут.</b> Видео слишком большое или сервер занят.",
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

    # .ytmp3 - скачивает MP3 из YouTube
    @loader.command()
    async def ytmp3cmd(self, message):
        reply = await message.get_reply_message()
        raw = utils.get_args_raw(message) or ""
        reply_text = getattr(reply, "raw_text", None) if reply else None
        url = self._extract_yt_url(raw, reply_text)

        if not url:
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Укажи YouTube ссылку:</b>\n"
                f"<code>.ytmp3 https://youtu.be/...</code>\n"
                f"<i>или ответь на сообщение с ссылкой.</i>",
            )
            return

        if not await self._check_ffmpeg():
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>ffmpeg не найден.</b>\nУстанови: <code>apt install ffmpeg</code>",
            )
            return

        if not await self._check_ytdlp():
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>yt-dlp не найден.</b>\n"
                f"Установи: <code>pip install -U yt-dlp</code>",
            )
            return

        await utils.answer(message, f"{EMOJI_LOADING} <b>Скачиваю и конвертирую…</b>")

        tmp_dir = tempfile.mkdtemp(prefix="ytmp3_")
        out_tpl = os.path.join(tmp_dir, "%(title).200s [%(id)s].%(ext)s")
        out_mp3 = None

        try:
            rc, stdout, stderr = await self._run(
                "yt-dlp",
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
                "-o",
                out_tpl,
                url,
            )
            if rc != 0:
                err_tail = (stderr or stdout or "unknown error")[-900:]
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
                    f"{EMOJI_ERROR} <b>Не удалось найти итоговый MP3-файл.</b>",
                )
                return

            await utils.answer(message, f"{EMOJI_LOADING} <b>Отправляю MP3…</b>")
            await message.client.send_file(
                message.chat_id,
                out_mp3,
                voice_note=False,
                caption=f"{EMOJI_DONE} <b>Готово! YouTube → MP3</b>",
                parse_mode="html",
            )
            await message.delete()

        except asyncio.TimeoutError:
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Таймаут.</b> Видео слишком длинное или сервер занят.",
            )
        except Exception as e:
            logger.exception("ytmp3 error")
            await utils.answer(
                message,
                f"{EMOJI_ERROR} <b>Неожиданная ошибка:</b> <code>{type(e).__name__}: {e}</code>",
            )
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                pass

    # .settag - редактирует теги MP3
    @loader.command()
    async def settagcmd(self, message):
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
                    f"{EMOJI_ERROR} <b>Ответь на MP3-аудио командой .settag</b>",
                )
                return

            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id

            await utils.answer(message, f"{EMOJI_LOADING} <b>Читаю теги…</b>")
            tmp = tempfile.mktemp(suffix=".mp3")
            try:
                await target_audio.download_media(tmp)
                audio = MP3(tmp, ID3=ID3)
                title = str(audio.tags.get("TIT2", "—")) if audio.tags else "—"
                artist = str(audio.tags.get("TPE1", "—")) if audio.tags else "—"
                has_cover = bool(audio.tags and any(str(k).startswith("APIC") for k in audio.tags.keys()))

                await utils.answer(
                    message,
                    f"🎵 <b>Текущие теги:</b>\n"
                    f"• <b>Название:</b> {title}\n"
                    f"• <b>Исполнитель:</b> {artist}\n"
                    f"• <b>Обложка:</b> {'есть' if has_cover else 'нет'}\n\n"
                    f"<b>Команды:</b>\n"
                    f"<code>.settag title Название</code>\n"
                    f"<code>.settag artist Исполнитель</code>\n"
                    f"<code>.settag cover</code> — ответь на фото\n"
                    f"<code>.settag apply</code> — применить",
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
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала ответь на MP3 командой .settag title ...</b>")
                return
            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id
            session["title"] = value
            await utils.answer(message, f"{EMOJI_DONE} <b>Название сохранено:</b> {value}\nПрименить: <code>.settag apply</code>")
            return

        if cmd == "artist":
            if not value:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Укажи исполнителя:</b> <code>.settag artist Исполнитель</code>")
                return
            if not target_audio:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала ответь на MP3 командой .settag artist ...</b>")
                return
            session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
            session["reply_id"] = target_audio.id
            session["artist"] = value
            await utils.answer(message, f"{EMOJI_DONE} <b>Исполнитель сохранён:</b> {value}\nПрименить: <code>.settag apply</code>")
            return

        if cmd == "cover":
            if not target_audio:
                await utils.answer(message, f"{EMOJI_ERROR} <b>Сначала привяжи MP3: .settag title ... (ответом на аудио)</b>")
                return

            photo_msg = message if getattr(message, "photo", None) else (reply if reply and getattr(reply, "photo", None) else None)
            if not photo_msg or not getattr(photo_msg, "photo", None):
                await utils.answer(message, f"{EMOJI_ERROR} <b>Ответь командой .settag cover на фото</b>")
                return

            await utils.answer(message, f"{EMOJI_LOADING} <b>Скачиваю обложку…</b>")
            tmp_cover = tempfile.mktemp(suffix=".jpg")
            try:
                await photo_msg.download_media(tmp_cover)
                with open(tmp_cover, "rb") as f:
                    cover_data = f.read()
                session = self._edit_sessions.setdefault(chat_id, {"reply_id": target_audio.id})
                session["reply_id"] = target_audio.id
                session["cover"] = cover_data
                await utils.answer(message, f"{EMOJI_DONE} <b>Обложка сохранена.</b>\nПрименить: <code>.settag apply</code>")
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
                    f"{EMOJI_ERROR} <b>Нет сохранённых изменений для этого аудио.</b>\n"
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
            f"{EMOJI_ERROR} <b>Неизвестная команда.</b>\n"
            f"Доступно: <code>title</code>, <code>artist</code>, <code>cover</code>, <code>apply</code>",
        )


