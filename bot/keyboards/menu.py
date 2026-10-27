from bot.ui import btn, kb, nav, to_main


def main_menu(is_admin: bool):
    rows = [
        [btn("🚀 Рассылки", nav("camps"), style="primary")],
        [btn("👤 Аккаунты", nav("accs")), btn("🎯 Базы", nav("leads"))],
        [btn("💎 Профиль", nav("profile"))],
    ]
    if is_admin:
        rows.append([btn("⚙️ Админ-панель", nav("adm"))])
    return kb(*rows)


def profile_kb():
    return kb([btn("💳 Тарифы и оплата", nav("plans"), style="success")], to_main())
