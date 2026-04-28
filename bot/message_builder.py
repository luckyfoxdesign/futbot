from .models import Match, Registration


def _escape_md(text: str) -> str:
    for ch in ('\\', '*', '_', '`', '['):
        text = text.replace(ch, f'\\{ch}')
    return text


def build_announce(match: Match, regs: list[Registration]) -> str:
    main = [r for r in regs if r.slot_type == "main"]
    reserve = [r for r in regs if r.slot_type == "reserve"]

    time_str = f"{match.time_start} – {match.time_end}" if match.time_end else match.time_start
    note_part = f'\n"{match.payment_info}"' if match.payment_info else ""
    loc_part = f"{match.location_url}\n\n" if match.location_url else ""
    header = (
        f"{loc_part}"
        f"Дата: {match.date}, {match.weekday}\n"
        f"Место: {match.field}\n"
        f"Стоимость: {match.price}{note_part}\n"
        f"Время: {time_str}\n"
        f"👥 {len(main)} / {match.max_players} записано\n"
    )

    def fmt(r: Registration) -> str:
        return f"{r.slot_number}. {_escape_md(r.display_name)} {_escape_md(r.contact)}"

    main_lines = "\n".join(fmt(r) for r in sorted(main, key=lambda r: r.slot_number)) or "—"
    reserve_lines = "\n".join(fmt(r) for r in sorted(reserve, key=lambda r: r.slot_number)) or "—"

    body = f"\nОсновной список:\n{main_lines}\n\nРезерв:\n{reserve_lines}\n"
    footer = "\n‼️ Выход из списка после 12:00 в день игры = оплата"

    return header + body + footer

