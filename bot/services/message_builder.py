from html import escape

from aiogram import Bot
from aiogram.types import InputMediaPhoto, InputMediaVideo


def parse_buttons(raw: str) -> list[list[dict]]:
    """Каждая строка = ряд; кнопки в ряду через ' | '; формат кнопки: 'Текст - https://url'."""
    rows: list[list[dict]] = []
    for line in raw.strip().splitlines():
        row = []
        for part in line.split("|"):
            text, sep, url = part.rpartition(" - ")
            text, url = text.strip(), url.strip()
            if not sep or not text or not url.startswith(("http://", "https://", "tg://")):
                raise ValueError(f"Неверная ссылка: «{part.strip()}»")
            row.append({"text": text, "url": url})
        if row:
            rows.append(row)
    return rows


def compose(text: str, buttons: list[list[dict]]) -> str:
    """Личные аккаунты не умеют инлайн-кнопки — «кнопки» превращаются в ссылки в конце текста."""
    lines = [" · ".join(f'<a href="{escape(b["url"])}">{escape(b["text"])}</a>' for b in row)
             for row in buttons]
    return "\n\n".join(p for p in (text, "\n".join(lines)) if p)


async def preview(bot: Bot, chat_id: int, text: str, media: list[dict], buttons: list[list[dict]]) -> None:
    """Предпросмотр от имени бота: близкая копия того, что уйдёт с аккаунта."""
    body = compose(text, buttons)
    if not media:
        await bot.send_message(chat_id, body, parse_mode="HTML", disable_web_page_preview=True)
    elif len(media) == 1:
        m = media[0]
        if m["type"] == "photo":
            await bot.send_photo(chat_id, m["file_id"], caption=body or None, parse_mode="HTML")
        elif m["type"] == "video":
            await bot.send_video(chat_id, m["file_id"], caption=body or None, parse_mode="HTML")
        else:
            await bot.send_video_note(chat_id, m["file_id"])
            if body:
                await bot.send_message(chat_id, body, parse_mode="HTML", disable_web_page_preview=True)
    else:
        group = [(InputMediaPhoto if m["type"] == "photo" else InputMediaVideo)(
            media=m["file_id"], caption=body if i == 0 and body else None, parse_mode="HTML")
            for i, m in enumerate(media)]
        await bot.send_media_group(chat_id, group)
