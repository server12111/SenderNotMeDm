from bot.ui import back, btn, kb, nav, pager, to_main


def leads_list(tags: list[tuple[str, int, int]], page: int, pages: int):
    rows = [[btn(f"🎯 {tag} · {alive}/{total}", nav("lead", v=tag))] for tag, total, alive in tags]
    return kb(*rows, pager("leads", page, pages),
              [btn("📥 Новая база", nav("lead_new"), style="success"), btn("🧲 Спарсить", nav("parser"))],
              to_main())


def lead_card(tag: str, dead: int):
    rows = [[btn("➕ Добавить ещё", nav("lead_add", v=tag)), btn("📤 Скачать .txt", nav("lead_dl", v=tag))]]
    if dead:
        rows.append([btn(f"🧹 Убрать недоступных ({dead})", nav("lead_clean", v=tag))])
    rows.append([btn("🗑 Удалить базу", nav("lead_del", v=tag), style="danger")])
    rows.append(back("leads", text="◀️ К списку"))
    return kb(*rows)


def import_cancel(tag: str = ""):
    return kb([btn("✖️ Отмена", nav("lead", v=tag) if tag else nav("leads"))])
