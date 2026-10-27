from bot.database.models import Account
from bot.services.parser import MODES, SOURCES
from bot.ui import back, btn, kb, nav, to_main


def parser_accounts(accs: list[Account]):
    rows = [[btn(f"👤 {a.phone}" + (f" · @{a.username}" if a.username else ""), nav("pr_acc", a.id))] for a in accs]
    return kb(*rows, [btn("🎯 Мои базы", nav("leads"))], to_main())


def parser_no_accounts():
    return kb([btn("➕ Подключить аккаунт", nav("acc_add"), style="success")],
              [btn("🎯 Мои базы", nav("leads"))], to_main())


def parser_sources():
    rows = [[btn(label, nav("pr_src", v=key))] for key, label in SOURCES.items()]
    return kb(*rows, [btn("🎯 Мои базы", nav("leads"))], to_main())


def parser_modes():
    rows = [[btn(label, nav("pr_mode", v=key))] for key, label in MODES.items()]
    return kb(*rows, back("parser", text="◀️ Назад"))


def parser_cancel():
    return kb([btn("✖️ Отмена", nav("pr_cancel"), style="danger")])


def parser_card(job):
    if job.status == "running":
        return kb([btn(f"📥 Скачать собранное ({len(job.found)})", nav("pr_dl", job.id))],
                  [btn("🔄 Обновить", nav("pr_view", job.id)), btn("🛑 Остановить", nav("pr_stop", job.id), style="danger")])
    rows = []
    if job.saved:
        rows.append([btn("📥 Скачать .txt", nav("lead_dl", v=job.tag)), btn("🎯 Открыть базу", nav("lead", v=job.tag))])
        rows.append([btn("🚀 Создать рассылку", nav("camp_new"), style="success")])
    rows.append([btn("🧲 Ещё парсинг", nav("parser"))])
    return kb(*rows, to_main())
