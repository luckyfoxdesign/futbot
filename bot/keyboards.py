from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def match_keyboard(match_id: int, closed: bool = False) -> InlineKeyboardMarkup:
    if closed:
        return InlineKeyboardMarkup([
            [InlineKeyboardButton("⚙️ Опции", callback_data=f"options_{match_id}")],
        ])
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Записаться ✅", callback_data=f"join_{match_id}"),
            InlineKeyboardButton("Выйти ❌", callback_data=f"leave_{match_id}"),
        ],
        [
            InlineKeyboardButton("В резерв 📝", callback_data=f"joinreserve_{match_id}"),
            InlineKeyboardButton("Выйти из резерва ❌", callback_data=f"leavereserve_{match_id}"),
        ],
        [
            InlineKeyboardButton("⚙️ Опции", callback_data=f"options_{match_id}"),
        ],
    ])


def options_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✏️ Изменить инфо", callback_data=f"optedit_{match_id}"),
            InlineKeyboardButton("🗑️ Удалить матч", callback_data=f"optdelete_{match_id}"),
        ],
        [
            InlineKeyboardButton("↩️ Назад", callback_data=f"optback_{match_id}"),
        ],
    ])


def delete_confirm_keyboard(match_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("⚠️ Да, удалить", callback_data=f"optdelconfirm_{match_id}"),
            InlineKeyboardButton("↩️ Отмена", callback_data=f"optback_{match_id}"),
        ],
    ])
