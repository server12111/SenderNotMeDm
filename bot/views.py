"""Тексты карточек — общие для хендлеров и live-обновлений."""
from datetime import date, datetime, timezone

from bot.database.models import Account, Campaign
from bot.ui import SEP, aware, bar, esc, fmt_dt

CAMP_STATUS = {
    "draft": "📝 Черновик", "running": "🚀 Идёт", "paused": "⏸ На паузе",
    "stopped": "🛑 Остановлена", "done": "✅ Завершена",
}
CAMP_ICON = {"draft": "📝", "running": "🚀", "paused": "⏸", "stopped": "🛑", "done": "✅"}
ACC_STATUS = {"active": "🟢 Активен", "off": "⚫ Выключен",
              "limited": "🟠 Ограничен Telegram", "dead": "🔴 Разлогинен"}
ACC_ICON = {"active": "🟢", "off": "⚫", "limited": "🟠", "dead": "🔴"}


def sent_today(a: Account) -> int:
    return a.sent_today if a.sent_date == date.today() else 0


def acc_state(a: Account) -> str:
    cd = aware(a.cooldown_until)
    if a.status == "active" and cd and cd > datetime.now(timezone.utc):
        return f"🟡 Отдыхает до {cd.astimezone().strftime('%H:%M')}"
    return ACC_STATUS.get(a.status, a.status)


def acc_icon(a: Account) -> str:
    return "🟡" if acc_state(a).startswith("🟡") else ACC_ICON.get(a.status, "⚪")


def account_card_text(a: Account) -> str:
    who = f" · @{esc(a.username)}" if a.username else ""
    return (f"👤 <b>{esc(a.phone)}</b>{who}\n{SEP}\n"
            f"Статус: {acc_state(a)}\n"
            f"📊 Сегодня: {bar(sent_today(a), a.daily_limit)} ({sent_today(a)}/{a.daily_limit})\n"
            f"📅 Подключён: {fmt_dt(a.created_at)}\n{SEP}\n"
            "<b>Лимит сообщений в сутки.</b> Чем выше, тем выше риск ограничений Telegram:")


def campaign_card_text(c: Campaign, accounts: int | None = None) -> str:
    done = c.sent + c.blocked + c.failed
    lines = [f"📦 <b>{esc(c.name)}</b>", CAMP_STATUS.get(c.status, c.status), SEP,
             f"🎯 База: <code>{esc(c.tag)}</code> · получателей: <b>{c.total}</b>"]
    if accounts is not None:
        lines.append(f"📱 Аккаунтов в работе: <b>{accounts}</b>")
    lines += [f"⏱ Темп: {c.delay_min:g}–{c.delay_max:g} с, до {c.per_minute}/мин на аккаунт", SEP,
              bar(done, c.total) + f"  ({done}/{c.total})",
              f"✅ Доставлено: <b>{c.sent}</b>",
              f"🚫 Недоступны: <b>{c.blocked}</b>",
              f"⚠️ Ошибки: <b>{c.failed}</b>"]
    if c.status == "running" and c.started_at and done:
        elapsed = (datetime.now(timezone.utc) - aware(c.started_at)).total_seconds()
        if elapsed > 30:
            eta = (c.total - done) / (done / elapsed) / 60
            lines.append(f"⏳ Осталось ≈ {eta:.0f} мин" if eta >= 1 else "⏳ Осталось меньше минуты")
    return "\n".join(lines)


# ---------- тарифы, заявки, рассылки админа ----------
SEGMENTS = {"all": "👥 Все пользователи", "paid": "💎 С тарифом", "free": "🆓 Без тарифа"}
NOTICE_STATUS = {"draft": "📝 Черновик", "running": "🚀 Идёт", "paused": "⏸ На паузе",
                 "stopped": "🛑 Остановлена", "done": "✅ Завершена"}
NOTICE_ICON = {"draft": "📝", "running": "🚀", "paused": "⏸", "stopped": "🛑", "done": "✅"}
ORDER_STATUS = {"new": "🆕 Создана", "paid": "🕒 Ждёт проверки", "confirmed": "✅ Подтверждена",
                "rejected": "❌ Отклонена", "cancelled": "⚪ Отменена"}


def fmt_usd(v) -> str:
    v = float(v)
    return f"${int(v)}" if v == int(v) else f"${v:.2f}"


def days_text(d: int) -> str:
    return "навсегда" if d == 0 else f"{d} дн."


def limits_text(accounts: int, daily: int, recipients: int) -> str:
    return (f"👤 Макс. аккаунтов: <b>{accounts}</b>\n📊 До <b>{daily}</b> сообщений в сутки на аккаунт\n"
            f"🎯 Получателей в рассылке: <b>{'без лимита' if recipients == 0 else recipients}</b>")


def format_amount(price, rate, currency: str) -> str:
    value = float(price) * float(rate)
    return f"{value:.2f} {currency}" if value >= 1 else f"{value:.8f}".rstrip("0").rstrip(".") + f" {currency}"


def notice_card_text(n) -> str:
    done = n.sent + n.blocked + n.failed
    return "\n".join([
        f"📣 <b>{esc(n.name)}</b>", NOTICE_STATUS.get(n.status, n.status), SEP,
        f"Аудитория: {SEGMENTS.get(n.segment, n.segment)} · получателей: <b>{n.total}</b>", SEP,
        bar(done, n.total) + f"  ({done}/{n.total})",
        f"✅ Доставлено: <b>{n.sent}</b>", f"🚫 Заблокировали бота: <b>{n.blocked}</b>",
        f"⚠️ Ошибки: <b>{n.failed}</b>"])
