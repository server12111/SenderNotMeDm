from aiogram.fsm.state import State, StatesGroup


class AccountForm(StatesGroup):
    phone = State()
    code = State()
    password = State()


class LeadImport(StatesGroup):
    tag = State()
    data = State()


class MailingForm(StatesGroup):
    name = State()
    text = State()
    media = State()
    buttons = State()
    segment = State()
    delay = State()
    confirm = State()


class AdminForm(StatesGroup):
    search = State()


class CatalogForm(StatesGroup):
    input = State()


class NoticeForm(StatesGroup):
    name = State()
    text = State()
    media = State()
    buttons = State()
    segment = State()
    confirm = State()


class UserMsgForm(StatesGroup):
    text = State()
