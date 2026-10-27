from bot.database.models import Account
from bot.ui import back, btn, kb, nav, pager, to_main
from bot.services.plans import limit_presets
from bot.views import acc_icon, sent_today


def accounts_list(accs: list[Account], page: int, pages: int):
    rows = [[btn(f"{acc_icon(a)} {a.phone} · {sent_today(a)}/{a.daily_limit}", nav("acc", a.id))] for a in accs]
    return kb(*rows, pager("accs", page, pages),
              [btn("➕ Подключить аккаунт", nav("acc_add"), style="success")], to_main())


def account_card(a: Account, cap: int):
    rows = []
    if a.status == "active":
        rows.append([btn("⚫ Выключить", nav("acc_off", a.id))])
    elif a.status == "off":
        rows.append([btn("🟢 Включить", nav("acc_on", a.id), style="success")])
    elif a.status == "limited":
        rows.append([btn("♻️ Вернуть в работу", nav("acc_on", a.id), style="success")])
    rows.append([btn(f"{'✅ ' if a.daily_limit == n else ''}{n}", nav("acc_lim", a.id, n)) for n in limit_presets(cap)])
    rows.append([btn("🔄 Переподключить", nav("acc_re", a.id)),
                 btn("🗑 Удалить", nav("acc_del", a.id), style="danger")])
    rows.append(back("accs", text="◀️ К списку"))
    return kb(*rows)


def login_cancel():
    return kb([btn("✖️ Отмена", nav("acc_cancel"), style="danger")])


def code_pad():
    """Код вводится кнопками, а не текстом: так Telegram не аннулирует его как «слитый в чат»."""
    d = lambda n: btn(str(n), nav("pad", v=str(n)))  # noqa: E731
    return kb(
        [d(1), d(2), d(3)], [d(4), d(5), d(6)], [d(7), d(8), d(9)],
        [btn("⌫", nav("pad", v="del"), style="danger"), d(0), btn("✅", nav("pad", v="ok"), style="success")],
        [btn("✖️ Отмена", nav("acc_cancel"))],
    )
