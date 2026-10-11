"""
Telegram-бот "Мафия" — групповая игра.

Функционал:
  1. При добавлении в группу — приветствие, первая игра бесплатная.
  2. /start_game — открывает лобби с кнопкой "Присоединиться", таймер 45 сек.
  3. После набора игроков — каждому в ЛС приходит его роль.
  4. Ночная фаза: мафия выбирает жертву, доктор — кого спасти.
  5. Дневная фаза: групповое голосование, казнь.
  6. Проверка условий победы (мафия vs город).

Роли:
  - 🔪 Мафия — ночью убивает.
  - 💊 Доктор — ночью спасает.
  - 👤 Мирный житель — спит ночью, голосует днём.
"""

import random
import threading
from collections import Counter

import telebot
from telebot import types

# ==========================================================================
# НАСТРОЙКИ
# ==========================================================================

BOT_TOKEN = "8281357378:AAEgOIMvzS1OlrrGU59Kdh8pDBmxPYKutkA"

MIN_PLAYERS = 3          # минимум игроков
MAX_PLAYERS = 10         # максимум игроков
LOBBY_SECONDS = 45       # набор игроков
NIGHT_SECONDS = 45       # длительность ночи (если кто-то не проголосовал)
DAY_SECONDS = 60         # длительность дня

bot = telebot.TeleBot(BOT_TOKEN, parse_mode="HTML")

# ==========================================================================
# СОСТОЯНИЕ
# ==========================================================================
# lobbies[chat_id] = {"players": {uid: name}, "message_id": int,
#                     "timer": Timer, "started_by": uid}
lobbies = {}

# games[chat_id] = {
#   "chat_id": int,
#   "players": {uid: name},
#   "roles": {uid: "mafia"/"doctor"/"civilian"},
#   "alive": set(uid),
#   "phase": "night"/"day",
#   "round": int,
#   "night": {"kill_target": uid|None, "save_target": uid|None,
#             "kill_by": uid|None, "save_by": uid|None},
#   "day": {"votes": {voter: target}, "voted": set(), "message_id": int|None},
#   "night_timer": Timer, "day_timer": Timer,
# }
games = {}

ROLE_NAMES = {"mafia": "🔪 Мафия", "doctor": "💊 Доктор", "civilian": "👤 Мирный житель"}
ROLE_DESC = {
    "mafia": "Ночью вы выбираете, кого убить. Днём притворяетесь мирным и голосуете.",
    "doctor": "Каждую ночь вы выбираете, кого спасти от мафии. Можно спасти себя.",
    "civilian": "Ночью вы спите. Днём голосуете за подозреваемого.",
}


# ==========================================================================
# 1. БОТА ДОБАВИЛИ В ГРУППУ
# ==========================================================================

@bot.my_chat_member_handler()
def on_bot_added_to_group(update: types.ChatMemberUpdated):
    old_status = update.old_chat_member.status
    new_status = update.new_chat_member.status

    was_out = old_status in ("left", "kicked")
    is_in = new_status in ("member", "administrator")

    if was_out and is_in and update.chat.type in ("group", "supergroup"):
        bot.send_message(
            update.chat.id,
            "👋 <b>Привет! Я бот для игры в «Кобилдженную мафию созданую смертным Михаилом и ВЕЛИКИМ ВЕЛИЧАЙШИМ СИЛЬНЕЙШИМ УМНЕЙШИМ и так далее Виктором».</b>\n\n"
            f"Чтобы начать, напишите /start_game (нужно минимум {MIN_PLAYERS} "
            "игроков).\n\n"
            "⚠️ Перед игрой каждому участнику нужно один раз нажать "
            "/start в личных сообщениях со мной — иначе я не смогу "
            "прислать вам роль."
        )


# ==========================================================================
# 2. ЗАПУСК ЛОББИ
# ==========================================================================

@bot.message_handler(commands=["start_game"])
def cmd_start_game(message: types.Message):
    chat_id = message.chat.id

    if message.chat.type not in ("group", "supergroup"):
        bot.reply_to(message, "Эта команда работает только в групповых чатах.")
        return

    if chat_id in lobbies:
        bot.reply_to(message, "Лобби уже открыто — присоединяйтесь кнопкой ниже 👇")
        return

    if chat_id in games:
        bot.reply_to(message, "Игра уже идёт в этом чате.")
        return

    keyboard = types.InlineKeyboardMarkup()
    keyboard.add(types.InlineKeyboardButton("✅ Присоединиться", callback_data="join_lobby"))
    keyboard.add(types.InlineKeyboardButton("🚫 Отменить набор", callback_data="cancel_lobby"))

    sent = bot.send_message(chat_id, lobby_text({}), reply_markup=keyboard)

    timer = threading.Timer(LOBBY_SECONDS, lambda: close_lobby(chat_id))
    timer.daemon = True
    timer.start()

    lobbies[chat_id] = {
        "players": {},
        "message_id": sent.message_id,
        "timer": timer,
        "started_by": message.from_user.id,
    }


def lobby_text(players: dict) -> str:
    count = len(players)
    names = "\n".join(f"• {name}" for name in players.values()) or "пока никого"
    return (
        "🎮 <b>Набор игроков в «Мафию»</b>\n\n"
        f"Игроков: {count}/{MAX_PLAYERS} (нужно минимум {MIN_PLAYERS})\n\n"
        f"{names}\n\n"
        f"⏳ Лобби закроется автоматически через {LOBBY_SECONDS} сек."
    )


# ==========================================================================
# 3. КНОПКИ ЛОББИ
# ==========================================================================

@bot.callback_query_handler(func=lambda call: call.data == "join_lobby")
def on_join_lobby(call: types.CallbackQuery):
    chat_id = call.message.chat.id
    lobby = lobbies.get(chat_id)

    if lobby is None:
        bot.answer_callback_query(call.id, "Лобби уже закрыто.")
        return

    user = call.from_user
    if user.id in lobby["players"]:
        bot.answer_callback_query(call.id, "Вы уже в лобби 🙂")
        return

    if len(lobby["players"]) >= MAX_PLAYERS:
        bot.answer_callback_query(call.id, "Мест больше нет.")
        return

    lobby["players"][user.id] = user.full_name
    bot.answer_callback_query(call.id, "Вы в игре!")

    bot.edit_message_text(
        lobby_text(lobby["players"]),
        chat_id=chat_id,
        message_id=lobby["message_id"],
        reply_markup=call.message.reply_markup,
    )

    if len(lobby["players"]) >= MAX_PLAYERS:
        lobby["timer"].cancel()
        close_lobby(chat_id)


@bot.callback_query_handler(func=lambda call: call.data == "cancel_lobby")
def on_cancel_lobby(call: types.CallbackQuery):
    chat_id = call.message.chat.id
    lobby = lobbies.get(chat_id)

    if lobby is None:
        bot.answer_callback_query(call.id, "Лобби уже закрыто.")
        return

    if call.from_user.id != lobby["started_by"]:
        bot.answer_callback_query(
            call.id, "Отменить набор может только тот, кто его запустил."
        )
        return

    lobby["timer"].cancel()
    message_id = lobby["message_id"]
    lobbies.pop(chat_id, None)
    bot.answer_callback_query(call.id, "Набор отменён.")
    bot.edit_message_text(
        "🚫 Набор игроков отменён.", chat_id=chat_id, message_id=message_id
    )


# ==========================================================================
# 4. ЗАКРЫТИЕ ЛОББИ — СТАРТ ИГРЫ
# ==========================================================================

def close_lobby(chat_id: int):
    lobby = lobbies.pop(chat_id, None)
    if lobby is None:
        return

    players = lobby["players"]

    if len(players) < MIN_PLAYERS:
        bot.send_message(
            chat_id,
            f"⏳ Время вышло. Собралось только {len(players)} из {MIN_PLAYERS} "
            "нужных игроков — игра отменена.\n\nПопробуйте снова: /start_game",
        )
        return

    names = ", ".join(players.values())
    bot.send_message(chat_id, f"✅ Набор завершён! Играют: {names}\n\nРаздаю роли…")
    start_game(chat_id, players)


# ==========================================================================
# 5. РАЗДАЧА РОЛЕЙ И СТАРТ ИГРЫ
# ==========================================================================

def assign_roles(player_ids):
    n = len(player_ids)
    mafia_count = max(1, n // 4)          # 1 мафия на ~4 игрока, минимум 1
    doctor_count = 1 if n >= 3 else 0
    civilian_count = n - mafia_count - doctor_count

    roles_list = (
        ["mafia"] * mafia_count
        + ["doctor"] * doctor_count
        + ["civilian"] * civilian_count
    )
    random.shuffle(roles_list)
    return dict(zip(player_ids, roles_list))


def start_game(chat_id: int, players: dict):
    player_ids = list(players.keys())
    roles = assign_roles(player_ids)

    games[chat_id] = {
        "chat_id": chat_id,
        "players": players,
        "roles": roles,
        "alive": set(player_ids),
        "phase": "night",
        "round": 0,
        "night": {},
        "day": {},
        "night_timer": None,
        "day_timer": None,
    }

    failed = []
    for uid, role in roles.items():
        try:
            bot.send_message(
                uid,
                f"🎭 <b>Ваша роль: {ROLE_NAMES[role]}</b>\n\n{ROLE_DESC[role]}",
            )
        except Exception:
            failed.append(players[uid])

    if failed:
        bot.send_message(
            chat_id,
            "⚠️ Не смог написать в ЛС: "
            + ", ".join(failed)
            + ".\nПускай каждый нажмёт /start в личке со мной, иначе игра "
              "не сможет продолжиться. Запустите заново: /start_game",
        )
        # Отменить игру, т.к. часть игроков без роли
        games.pop(chat_id, None)
        return

    # Небольшая пауза, чтобы все успели прочитать роль
    threading.Timer(2.0, lambda: start_night(chat_id)).start()


# ==========================================================================
# 6. НОЧНАЯ ФАЗА
# ==========================================================================

def start_night(chat_id: int):
    game = games.get(chat_id)
    if game is None:
        return

    game["phase"] = "night"
    game["round"] += 1
    game["night"] = {
        "kill_target": None,
        "save_target": None,
        "kill_by": None,
        "save_by": None,
    }

    bot.send_message(
        chat_id,
        f"🌙 <b>Ночь {game['round']}</b>\n\nГород засыпает. Мафия просыпается, "
        "доктор готовит аптечку…",
    )

    alive = list(game["alive"])
    for uid in alive:
        role = game["roles"][uid]
        try:
            if role == "mafia":
                targets = [p for p in alive if p != uid]
                if not targets:
                    continue
                kb = types.InlineKeyboardMarkup()
                for t in targets:
                    kb.add(types.InlineKeyboardButton(
                        game["players"][t], callback_data=f"kill:{chat_id}:{t}"
                    ))
                bot.send_message(uid, "🔪 Кого убить этой ночью?", reply_markup=kb)
            elif role == "doctor":
                kb = types.InlineKeyboardMarkup()
                for t in alive:
                    kb.add(types.InlineKeyboardButton(
                        game["players"][t], callback_data=f"save:{chat_id}:{t}"
                    ))
                bot.send_message(uid, "💊 Кого спасти этой ночью?", reply_markup=kb)
        except Exception:
            pass

    t = threading.Timer(NIGHT_SECONDS, lambda: end_night(chat_id))
    t.daemon = True
    t.start()
    game["night_timer"] = t


@bot.callback_query_handler(func=lambda call: call.data.startswith("kill:"))
def on_kill(call: types.CallbackQuery):
    try:
        _, chat_id_s, target_s = call.data.split(":")
        chat_id = int(chat_id_s)
        target = int(target_s)
    except ValueError:
        bot.answer_callback_query(call.id, "Ошибка.")
        return

    game = games.get(chat_id)
    if game is None or game["phase"] != "night":
        bot.answer_callback_query(call.id, "Сейчас не ночь.")
        return

    killer = call.from_user.id
    if killer not in game["alive"] or game["roles"].get(killer) != "mafia":
        bot.answer_callback_query(call.id, "Вы не мафия.")
        return

    if game["night"]["kill_target"] is not None:
        bot.answer_callback_query(call.id, "Жертва уже выбрана.")
        return

    if target not in game["alive"] or target == killer:
        bot.answer_callback_query(call.id, "Нельзя выбрать этого игрока.")
        return

    game["night"]["kill_target"] = target
    game["night"]["kill_by"] = killer

    bot.answer_callback_query(call.id, f"Жертва: {game['players'][target]}")
    try:
        bot.edit_message_text(
            f"🔪 Вы выбрали жертву: <b>{game['players'][target]}</b>",
            chat_id=call.from_user.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass

    check_night_done(chat_id)


@bot.callback_query_handler(func=lambda call: call.data.startswith("save:"))
def on_save(call: types.CallbackQuery):
    try:
        _, chat_id_s, target_s = call.data.split(":")
        chat_id = int(chat_id_s)
        target = int(target_s)
    except ValueError:
        bot.answer_callback_query(call.id, "Ошибка.")
        return

    game = games.get(chat_id)
    if game is None or game["phase"] != "night":
        bot.answer_callback_query(call.id, "Сейчас не ночь.")
        return

    doctor = call.from_user.id
    if doctor not in game["alive"] or game["roles"].get(doctor) != "doctor":
        bot.answer_callback_query(call.id, "Вы не доктор.")
        return

    if game["night"]["save_target"] is not None:
        bot.answer_callback_query(call.id, "Выбор уже сделан.")
        return

    if target not in game["alive"]:
        bot.answer_callback_query(call.id, "Нельзя выбрать этого игрока.")
        return

    game["night"]["save_target"] = target
    game["night"]["save_by"] = doctor

    bot.answer_callback_query(call.id, f"Спасаете: {game['players'][target]}")
    try:
        bot.edit_message_text(
            f"💊 Вы спасаете: <b>{game['players'][target]}</b>",
            chat_id=call.from_user.id,
            message_id=call.message.message_id,
        )
    except Exception:
        pass

    check_night_done(chat_id)


def check_night_done(chat_id: int):
    game = games.get(chat_id)
    if game is None or game["phase"] != "night":
        return

    mafia_alive = [p for p in game["alive"] if game["roles"][p] == "mafia"]
    doctor_alive = [p for p in game["alive"] if game["roles"][p] == "doctor"]

    kill_ready = (not mafia_alive) or game["night"]["kill_target"] is not None
    save_ready = (not doctor_alive) or game["night"]["save_target"] is not None

    if kill_ready and save_ready:
        end_night(chat_id)


def end_night(chat_id: int):
    game = games.get(chat_id)
    if game is None or game["phase"] != "night":
        return

    if game["night_timer"]:
        game["night_timer"].cancel()
        game["night_timer"] = None

    kill_target = game["night"]["kill_target"]
    save_target = game["night"]["save_target"]

    killed = None
    if kill_target is not None and kill_target != save_target:
        killed = kill_target
        game["alive"].discard(kill_target)

    if killed is not None:
        bot.send_message(
            chat_id,
            f"☀️ <b>Утро {game['round']}</b>\n\n"
            f"Этой ночью погиб: <b>{game['players'][killed]}</b>",
        )
    else:
        bot.send_message(
            chat_id,
            f"☀️ <b>Утро {game['round']}</b>\n\n"
            "Этой ночью никто не погиб — доктор успел вовремя!",
        )

    if check_win(chat_id):
        return

    start_day(chat_id)


# ==========================================================================
# 7. ДНЕВНАЯ ФАЗА — ГОЛОСОВАНИЕ
# ==========================================================================

def start_day(chat_id: int):
    game = games.get(chat_id)
    if game is None:
        return

    game["phase"] = "day"
    game["day"] = {"votes": {}, "voted": set(), "message_id": None}

    alive = list(game["alive"])
    kb = types.InlineKeyboardMarkup()
    for p in alive:
        kb.add(types.InlineKeyboardButton(
            game["players"][p], callback_data=f"vote:{chat_id}:{p}"
        ))

    sent = bot.send_message(
        chat_id,
        f"🗳 <b>День {game['round']}</b>\n\n"
        "Обсуждайте и голосуйте, кого казнить. "
        f"Голосование закроется через {DAY_SECONDS} сек, "
        "или когда проголосуют все живые.",
        reply_markup=kb,
    )
    game["day"]["message_id"] = sent.message_id

    t = threading.Timer(DAY_SECONDS, lambda: end_day(chat_id))
    t.daemon = True
    t.start()
    game["day_timer"] = t


@bot.callback_query_handler(func=lambda call: call.data.startswith("vote:"))
def on_vote(call: types.CallbackQuery):
    try:
        _, chat_id_s, target_s = call.data.split(":")
        chat_id = int(chat_id_s)
        target = int(target_s)
    except ValueError:
        bot.answer_callback_query(call.id, "Ошибка.")
        return

    game = games.get(chat_id)
    if game is None or game["phase"] != "day":
        bot.answer_callback_query(call.id, "Сейчас не день.")
        return

    voter = call.from_user.id
    if voter not in game["alive"]:
        bot.answer_callback_query(call.id, "Вы мертвы и не можете голосовать.")
        return

    if voter in game["day"]["voted"]:
        bot.answer_callback_query(call.id, "Вы уже голосовали.")
        return

    if target not in game["alive"]:
        bot.answer_callback_query(call.id, "Нельзя голосовать за этого игрока.")
        return

    game["day"]["voted"].add(voter)
    game["day"]["votes"][voter] = target
    bot.answer_callback_query(call.id, f"Голос за: {game['players'][target]}")

    if len(game["day"]["voted"]) >= len(game["alive"]):
        end_day(chat_id)


def end_day(chat_id: int):
    game = games.get(chat_id)
    if game is None or game["phase"] != "day":
        return

    if game["day_timer"]:
        game["day_timer"].cancel()
        game["day_timer"] = None

    votes = game["day"]["votes"]

    if not votes:
        bot.send_message(chat_id, "🤷 Никто не проголосовал. Казни не будет.")
        if not check_win(chat_id):
            start_night(chat_id)
        return

    counts = Counter(votes.values())
    max_v = max(counts.values())
    top = [t for t, c in counts.items() if c == max_v]

    if len(top) > 1:
        bot.send_message(
            chat_id,
            "⚖️ Голоса разделились. Никто не казнён.\n\nНаступает ночь…",
        )
        if not check_win(chat_id):
            start_night(chat_id)
        return

    lynched = top[0]
    game["alive"].discard(lynched)
    role = game["roles"][lynched]

    bot.send_message(
        chat_id,
        f"⚖️ Город казнил: <b>{game['players'][lynched]}</b>\n"
        f"Его роль: <b>{ROLE_NAMES[role]}</b>\n\nНаступает ночь…",
    )

    if not check_win(chat_id):
        start_night(chat_id)


def check_win(chat_id: int) -> bool:
    game = games.get(chat_id)
    if game is None:
        return True

    mafia_alive = [p for p in game["alive"] if game["roles"][p] == "mafia"]
    non_mafia_alive = [p for p in game["alive"] if game["roles"][p] != "mafia"]

    if not mafia_alive:

        reveal = "\n".join(
            f"• {game['players'][uid]} — {ROLE_NAMES[game['roles'][uid]]}"
            for uid in game["roles"]
        )
        bot.send_message(
            chat_id,
            "🎉 <b>Победа города!</b> Вся мафия уничтожена.\n\n"
            f"<b>Роли игроков:</b>\n{reveal}",
        )
        games.pop(chat_id, None)
        return True

    if len(mafia_alive) >= len(non_mafia_alive):
        reveal = "\n".join(
            f"• {game['players'][uid]} — {ROLE_NAMES[game['roles'][uid]]}"
            for uid in game["roles"]
        )
        bot.send_message(
            chat_id,
            "🔪 <b>Победа мафии!</b> Мафия захватила город.\n\n"
            f"<b>Роли игроков:</b>\n{reveal}",
        )
        games.pop(chat_id, None)
        return True

    return False

@bot.message_handler(commands=["start"], chat_types=["private"])
def cmd_start_private(message: types.Message):
    bot.send_message(
        message.chat.id,
        "Привет! Я бот для игры в «Мафию».\n\n"
        "Добавь меня в группу и напиши там /start_game.\n"
        "Когда игра начнётся, я пришлю сюда твою роль.",
    )


if __name__ == "__main__":
    print("Бот запущен...")
    bot.infinity_polling()