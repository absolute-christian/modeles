# ---------------------------------------------------------------------------------
# Name: ChatParser
# Description: Export chat members with usernames, admin titles and permanent links
# Author: @codrago_m
# ---------------------------------------------------------------------------------
# meta developer: @codrago_m
# scope: hikka_only
# ---------------------------------------------------------------------------------

__version__ = (1, 0, 0)

import io
import logging

from .. import loader, utils

logger = logging.getLogger(__name__)


@loader.tds
class ChatParserMod(loader.Module):
    """Parse chat members"""

    strings = {
        "name": "ChatParser",
        "loading": (
            "<emoji document_id=5780543148782522693>🕒</emoji> "
            "<b>Загружаю участников чата...</b>"
        ),
        "not_chat": (
            "<emoji document_id=5328145443106873128>✖️</emoji> "
            "<b>Эта команда работает только в чатах и каналах.</b>"
        ),
        "empty": (
            "<emoji document_id=5328145443106873128>✖️</emoji> "
            "<b>Не удалось найти участников.</b>"
        ),
        "failed": (
            "<emoji document_id=5328145443106873128>✖️</emoji> "
            "<b>Не удалось загрузить участников:</b> <code>{}</code>"
        ),
        "header": (
            "<emoji document_id=5323538339062628165>💬</emoji> "
            "<b>Участники чата:</b> <code>{chat}</code>\n"
            "<emoji document_id=5314260526803462610>😴</emoji> "
            "<b>Всего:</b> <code>{count}</code>\n"
            "<emoji document_id=5774022692642492953>✅</emoji> "
            "<b>Админов:</b> <code>{admins}</code>\n\n"
        ),
        "file_caption": (
            "<emoji document_id=5323538339062628165>💬</emoji> "
            "<b>Список участников чата:</b> <code>{chat}</code>\n"
            "<b>Всего:</b> <code>{count}</code>"
        ),
        "no_tags": "нет тегов",
        "no_admin": "нет",
        "creator": "создатель",
        "admin": "админ",
    }

    async def parsecmd(self, message):
        """| Получить список участников чата, теги, админки, ID и вечные ссылки"""
        if getattr(message, "is_private", False):
            await utils.answer(message, self.strings["not_chat"])
            return

        message = await utils.answer(message, self.strings["loading"])

        try:
            chat = await message.get_chat()
            members = [member async for member in self._client.iter_participants(chat)]
        except Exception as e:
            logger.exception("Unable to parse chat members")
            await utils.answer(
                message,
                self.strings["failed"].format(utils.escape_html(str(e))),
            )
            return

        if not members:
            await utils.answer(message, self.strings["empty"])
            return

        admins = sum(
            1
            for member in members
            if self._admin_title(member) != self.strings["no_admin"]
        )
        title = utils.escape_html(
            getattr(chat, "title", None) or str(getattr(chat, "id", "unknown"))
        )
        header = self.strings["header"].format(
            chat=title,
            count=len(members),
            admins=admins,
        )
        lines = [
            self._format_member(index, member)
            for index, member in enumerate(members, 1)
        ]
        pages = self._make_pages(header, lines)

        if len(pages) == 1:
            await utils.answer(message, pages[0])
            return

        if getattr(self.inline, "init_complete", False):
            await self.inline.list(message, pages)
            return

        file_data = io.BytesIO(
            utils.remove_html("\n\n".join(pages), keep_emojis=True).encode("utf-8")
        )
        file_data.name = "chat_members.txt"
        await utils.answer_file(
            message,
            file_data,
            self.strings["file_caption"].format(chat=title, count=len(members)),
        )

    def _format_member(self, index, user):
        name = utils.escape_html(self._display_name(user))
        user_id = getattr(user, "id", 0)
        tags = self._format_tags(user)
        admin = utils.escape_html(self._admin_title(user))

        return (
            f"<b>{index}.</b> <a href=\"tg://user?id={user_id}\">{name}</a>\n"
            f"   <b>ID:</b> <code>{user_id}</code>\n"
            f"   <b>Теги:</b> {tags}\n"
            f"   <b>Админка:</b> <code>{admin}</code>"
        )

    def _display_name(self, user):
        first_name = getattr(user, "first_name", None) or ""
        last_name = getattr(user, "last_name", None) or ""
        title = getattr(user, "title", None)
        name = " ".join(part for part in (first_name, last_name) if part).strip()
        return (
            name
            or title
            or getattr(user, "username", None)
            or f"User {getattr(user, 'id', 0)}"
        )

    def _format_tags(self, user):
        usernames = []

        username = getattr(user, "username", None)
        if username:
            usernames.append(username)

        for item in getattr(user, "usernames", None) or []:
            if getattr(item, "active", True) is False:
                continue

            username = getattr(item, "username", None)
            if username and username not in usernames:
                usernames.append(username)

        if not usernames:
            return f"<i>{self.strings['no_tags']}</i>"

        return ", ".join(
            f"<a href=\"https://t.me/{utils.escape_html(username)}\">"
            f"@{utils.escape_html(username)}</a>"
            for username in usernames
        )

    def _admin_title(self, user):
        participant = getattr(user, "participant", None)
        if not participant:
            return self.strings["no_admin"]

        rank = getattr(participant, "rank", None)
        if rank:
            return str(rank)

        class_name = participant.__class__.__name__.lower()
        if "creator" in class_name:
            return self.strings["creator"]

        if "admin" in class_name:
            return self.strings["admin"]

        return self.strings["no_admin"]

    def _make_pages(self, header, lines, limit=3600):
        pages = []
        current = header

        for line in lines:
            next_block = (
                f"{current}\n{line}" if current != header else f"{current}{line}"
            )
            if len(next_block) > limit and current != header:
                pages.append(current)
                current = f"{header}{line}"
            else:
                current = next_block

        if current.strip():
            pages.append(current)

        return pages
