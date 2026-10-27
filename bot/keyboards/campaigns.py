from bot.database.models import Campaign
from bot.ui import back, btn, kb, nav, pager, to_main
from bot.views import CAMP_ICON

# Реальные аккаунты — пресеты консервативные. per_minute считается на каждый аккаунт.
DELAY_PRESETS = {
    "fast": ("⚡ Быстро · 3–8 с · до 8/мин", 3.0, 8.0, 8),
    "std": ("⚖️ Стандарт · 8–25 с · до 4/мин", 8.0, 25.0, 4),
    "safe": ("🐢 Осторожно · 20–60 с · до 2/мин", 20.0, 60.0, 2),
}


def campaigns_list(camps: list[Campaign], page: int, pages: int):
    rows = []
    for c in camps:
        done = c.sent + c.blocked + c.failed
        pct = f" · {done * 100 // c.total}%" if c.total and c.status != "draft" else ""
        rows.append([btn(f"{CAMP_ICON.get(c.status, '•')} {c.name[:34]}{pct}", nav("camp", c.id))])
    return kb(*rows, pager("camps", page, pages),
              [btn("➕ Создать рассылку", nav("camp_new"), style="success")], to_main())


def campaign_card(c: Campaign):
    rows = []
    if c.status == "draft":
        rows.append([btn("▶️ Запустить", nav("camp_start", c.id), style="success")])
        rows.append([btn("👁 Предпросмотр", nav("camp_test", c.id))])
    elif c.status == "running":
        rows.append([btn("⏸ Пауза", nav("camp_pause", c.id)), btn("🛑 Стоп", nav("camp_stop", c.id), style="danger")])
    elif c.status == "paused":
        rows.append([btn("▶️ Продолжить", nav("camp_resume", c.id), style="success"),
                     btn("🛑 Стоп", nav("camp_stop", c.id), style="danger")])
    elif c.status == "stopped":
        rows.append([btn("▶️ Возобновить", nav("camp_start", c.id), style="success")])
    if c.status != "draft":
        rows.append([btn("📊 Статистика", nav("camp_stats", c.id)), btn("📋 Ошибки", nav("camp_logs", c.id))])
        rows.append([btn("🔄 Обновить", nav("camp", c.id))])
    if c.status != "running":
        rows.append([btn("🗑 Удалить", nav("camp_del", c.id), style="danger")])
    rows.append(back("camps", text="◀️ К списку"))
    return kb(*rows)


def wizard_cancel():
    return kb([btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def wizard_media():
    return kb([btn("✅ Готово / без медиа", nav("w_media_done"), style="success")],
              [btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def wizard_buttons():
    return kb([btn("⏭ Без ссылок", nav("w_btn_skip"))], [btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def wizard_tags(tags: list[tuple[str, int]]):
    rows = [[btn(f"🎯 {t} · {n}", nav("w_tag", v=t))] for t, n in tags]
    return kb(*rows, [btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def wizard_delay():
    rows = [[btn(label, nav("w_delay", v=key))] for key, (label, *_ ) in DELAY_PRESETS.items()]
    return kb(*rows, [btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def wizard_confirm():
    return kb([btn("✅ Создать рассылку", nav("w_create"), style="success")],
              [btn("👁 Тест мне", nav("w_test")), btn("✖️ Отмена", nav("w_cancel"), style="danger")])


def stats_kb(cid: int):
    return kb([btn("📋 Ошибки", nav("camp_logs", cid))], back("camp", cid, text="◀️ К рассылке"))


def logs_kb(cid: int, page: int, pages: int):
    return kb(pager("camp_logs", page, pages, id=cid), back("camp", cid, text="◀️ К рассылке"))


def no_bases():
    return kb([btn("📥 Загрузить базу", nav("lead_new"), style="success")], back("camps", text="◀️ К рассылкам"))
