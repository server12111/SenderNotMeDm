from bot.database.models import Notice, Order, PaymentMethod, Plan, User
from bot.ui import back, btn, kb, nav, pager, to_main
from bot.views import NOTICE_ICON, ORDER_STATUS, SEGMENTS, days_text, fmt_usd


def admin_main(pending: int = 0):
    return kb(
        [btn("📊 Статистика", nav("adm_stats")), btn("👥 Пользователи", nav("adm_users"))],
        [btn("📣 Рассылка пользователям", nav("adm_notices"), style="primary")],
        [btn("💳 Тарифы", nav("adm_plans")), btn("💰 Реквизиты", nav("adm_methods"))],
        [btn(f"🧾 Заявки{f' ({pending})' if pending else ''}", nav("adm_orders"),
             style="success" if pending else None), btn("💾 Бэкап БД", nav("adm_backup"))],
        to_main(),
    )


def refresh(a: str):
    return kb([btn("🔄 Обновить", nav(a))], back("adm"))


# ---------- пользователи ----------
def users_list(users: list[User], page: int, pages: int):
    rows = [[btn(f"{'🚫' if u.is_banned else '👑' if u.is_admin else '👤'} "
                 f"{(u.first_name or u.username or '—')[:22]} · {u.id}", nav("adm_user", u.id))] for u in users]
    return kb(*rows, pager("adm_users", page, pages), [btn("🔎 Поиск", nav("adm_find"))], back("adm"))


def user_card(u: User, has_sub: bool):
    return kb(
        [btn("🎁 Выдать тариф", nav("adm_grant", u.id), style="success"),
         btn("❌ Снять тариф", nav("adm_revoke", u.id), style="danger") if has_sub else
         btn("✉️ Написать", nav("adm_msg", u.id))],
        [btn("✉️ Написать", nav("adm_msg", u.id))] if has_sub else [],
        [btn("✅ Разблокировать", nav("adm_unban", u.id), style="success") if u.is_banned
         else btn("🚫 Заблокировать", nav("adm_ban", u.id), style="danger"),
         btn("👑 Снять админа" if u.is_admin else "👑 Выдать админа", nav("adm_op", u.id))],
        back("adm_users", text="◀️ К списку"),
    )


def grant_plans(user_id: int, plans: list[Plan]):
    rows = [[btn(f"💎 {p.name} · {days_text(p.duration_days)}", nav("adm_grant_do", user_id, p.id))] for p in plans]
    return kb(*rows, back("adm_user", user_id))


def find_cancel():
    return kb(back("adm_users", text="✖️ Отмена"))


# ---------- рассылка админа ----------
def notices_list(items: list[Notice], page: int, pages: int):
    rows = [[btn(f"{NOTICE_ICON.get(n.status, '•')} {n.name[:34]} · {n.sent}/{n.total}", nav("adm_notice", n.id))]
            for n in items]
    return kb(*rows, pager("adm_notices", page, pages),
              [btn("➕ Новая рассылка", nav("nt_new"), style="success")], back("adm"))


def notice_card(n: Notice):
    rows = []
    if n.status == "draft":
        rows += [[btn("▶️ Запустить", nav("nt_start", n.id), style="success")],
                 [btn("👁 Предпросмотр", nav("nt_test", n.id))]]
    elif n.status == "running":
        rows.append([btn("⏸ Пауза", nav("nt_pause", n.id)), btn("🛑 Стоп", nav("nt_stop", n.id), style="danger")])
    elif n.status == "paused":
        rows.append([btn("▶️ Продолжить", nav("nt_resume", n.id), style="success"),
                     btn("🛑 Стоп", nav("nt_stop", n.id), style="danger")])
    elif n.status == "stopped":
        rows.append([btn("▶️ Возобновить", nav("nt_start", n.id), style="success")])
    if n.status != "draft":
        rows.append([btn("🔄 Обновить", nav("adm_notice", n.id))])
    if n.status != "running":
        rows.append([btn("🗑 Удалить", nav("nt_del", n.id), style="danger")])
    rows.append(back("adm_notices", text="◀️ К списку"))
    return kb(*rows)


def nt_cancel():
    return kb([btn("✖️ Отмена", nav("nt_cancel"), style="danger")])


def nt_media():
    return kb([btn("✅ Готово / без медиа", nav("nt_media_done"), style="success")],
              [btn("✖️ Отмена", nav("nt_cancel"), style="danger")])


def nt_buttons():
    return kb([btn("⏭ Без кнопок", nav("nt_btn_skip"))], [btn("✖️ Отмена", nav("nt_cancel"), style="danger")])


def nt_segments():
    rows = [[btn(label, nav("nt_seg", v=key))] for key, label in SEGMENTS.items()]
    return kb(*rows, [btn("✖️ Отмена", nav("nt_cancel"), style="danger")])


def nt_confirm():
    return kb([btn("✅ Создать рассылку", nav("nt_create"), style="success")],
              [btn("👁 Тест мне", nav("nt_wtest")), btn("✖️ Отмена", nav("nt_cancel"), style="danger")])


# ---------- каталог: тарифы и реквизиты ----------
def plans_list(plans: list[Plan]):
    rows = [[btn(f"{'💎' if p.is_active else '⚫'} {p.name} · {fmt_usd(p.price)} · {days_text(p.duration_days)}",
                 nav("adm_plan", p.id))] for p in plans]
    return kb([btn("🆓 Free-тариф (лимиты)", nav("adm_free"))], *rows,
              [btn("➕ Новый тариф", nav("adm_plan_new"), style="success")], back("adm"))


def plan_card(p: Plan):
    e = lambda f, label: btn(label, nav("adm_edit", p.id, v=f"plan.{f}"))  # noqa: E731
    return kb(
        [e("name", "✏️ Название"), e("price", "✏️ Цена")],
        [e("duration_days", "✏️ Срок"), e("max_accounts", "✏️ Аккаунты")],
        [e("daily_limit", "✏️ Лимит/сутки"), e("max_recipients", "✏️ Получатели")],
        [btn("⚫ Скрыть из продажи" if p.is_active else "💎 Вернуть в продажу", nav("adm_plan_toggle", p.id)),
         btn("🗑 Удалить", nav("adm_plan_del", p.id), style="danger")],
        back("adm_plans", text="◀️ К тарифам"),
    )


def free_card():
    e = lambda f, label: btn(label, nav("adm_edit", 0, v=f"free.{f}"))  # noqa: E731
    return kb([e("free_max_accounts", "✏️ Аккаунты"), e("free_daily_limit", "✏️ Лимит/сутки")],
              [e("free_max_recipients", "✏️ Получатели в рассылке")], back("adm_plans", text="◀️ К тарифам"))


def methods_list(methods: list[PaymentMethod]):
    rows = [[btn(f"{'🟢' if m.is_enabled else '⚫'} {m.title} · {m.currency}", nav("adm_method", m.id))]
            for m in methods]
    return kb(*rows, [btn("➕ Добавить способ", nav("adm_method_new"), style="success")], back("adm"))


def method_card(m: PaymentMethod):
    e = lambda f, label: btn(label, nav("adm_edit", m.id, v=f"method.{f}"))  # noqa: E731
    return kb(
        [e("title", "✏️ Название"), e("currency", "✏️ Валюта")],
        [e("rate", "✏️ Курс"), e("details", "✏️ Реквизиты")],
        [btn("⚫ Выключить" if m.is_enabled else "🟢 Включить", nav("adm_method_toggle", m.id)),
         btn("🗑 Удалить", nav("adm_method_del", m.id), style="danger")],
        back("adm_methods", text="◀️ К реквизитам"),
    )


def form_cancel(target):
    return kb([btn("✖️ Отмена", target, style="danger")])


# ---------- заявки ----------
def orders_list(orders: list[Order], page: int, pages: int):
    rows = [[btn(f"{ORDER_STATUS[o.status].split()[0]} #{o.id} · {o.plan_name} · {fmt_usd(o.price_usd)}",
                 nav("adm_order", o.id))] for o in orders]
    return kb(*rows, pager("adm_orders", page, pages), back("adm"))


def order_review(order_id: int):
    return kb([btn("✅ Подтвердить оплату", nav("adm_order_ok", order_id), style="success")],
              [btn("❌ Отклонить", nav("adm_order_no", order_id), style="danger")])
