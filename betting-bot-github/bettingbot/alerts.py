"""Canales de notificación: consola, Telegram, ntfy y Discord.

Todo se hace por HTTP con ``urllib`` de la biblioteca estándar, así que no hay
que instalar nada. Si el envío falla (sin red, token mal, chat inexistente) se
registra y se sigue: una alerta perdida no debe tumbar el bot.

``ntfy`` es la alternativa más rápida a Telegram: no requiere cuenta ni bot,
solo elegir un nombre de tema y suscribirse desde la app o la web.
"""

from __future__ import annotations

import html
import json
import os
import re
import sys
import urllib.error
import urllib.request
from typing import Protocol, Sequence

from . import factors
from .safety import assess
from .timeutil import format_kickoff
from .types import Signal

OUTCOME_LABELS = {
    "home": "Gana local",
    "away": "Gana visitante",
    "draw": "Empate",
    "over": "Más de",
    "under": "Menos de",
    "yes": "Sí",
    "no": "No",
}

# (etiqueta corta, explicación en una línea) por tipo de señal.
KIND_INFO = {
    "VALUE": ("VALOR", "la cuota paga más de lo que vale: hay ventaja"),
    "ARB": ("ARBITRAJE", "beneficio seguro apostando a todas las opciones"),
    "MISPRICING": ("POSIBLE ERROR", "una casa paga muy por encima de las demás"),
    "STEAM": ("CUOTAS CAYENDO", "el precio baja rápido: está entrando dinero"),
    "DRIFT": ("CUOTAS SUBIENDO", "el precio sube: la casa se está cubriendo"),
    "DIVERGENCE": ("CASAS DIVIDIDAS", "las casas no coinciden en el precio"),
}

KIND_ACTION = {
    "VALUE": "ACCIÓN: apuesta la cantidad indicada a la cuota indicada.",
    "ARB": "ACCIÓN: apuesta a TODAS las cuotas del detalle para asegurar ganancia.",
    "MISPRICING": "ACCIÓN: revisa antes de apostar; la casa suele corregir pronto.",
    "STEAM": "ACCIÓN: informativo; vigila el mercado antes de entrar.",
    "DRIFT": "ACCIÓN: informativo; espera a que se estabilice.",
    "DIVERGENCE": "ACCIÓN: compara varias casas antes de decidir.",
}

SPORT_LABELS = {
    "soccer": "Fútbol",
    "basketball": "Baloncesto",
    "tennis": "Tenis",
    "mma": "MMA/Boxeo",
    "table_tennis": "Tenis de mesa",
}

# Alias conservado por compatibilidad con pruebas y código antiguo.
_kickoff = format_kickoff


class Notifier(Protocol):
    name: str

    def send(self, text: str) -> bool:
        ...

    def format(self, signal: Signal) -> str:
        ...


class ConsoleNotifier:
    name = "console"

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stdout

    def send(self, text: str) -> bool:
        print(text, file=self.stream)
        return True

    def format(self, signal: Signal) -> str:
        return format_signal_plain(signal)


class NullNotifier:
    name = "null"

    def send(self, text: str) -> bool:
        return True

    def format(self, signal: Signal) -> str:
        return format_signal(signal)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str, timeout: float = 10.0) -> None:
        if not token or not chat_id:
            raise ValueError("Telegram necesita token y chat_id")
        self.token = token
        self.chat_id = str(chat_id)
        self.timeout = timeout
        self.failures = 0

    def format(self, signal: Signal) -> str:
        return format_signal(signal)

    def send(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.token}/sendMessage"
        payload = json.dumps(
            {
                "chat_id": self.chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            }
        ).encode("utf-8")
        req = urllib.request.Request(
            url, data=payload, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return 200 <= resp.status < 300
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            self.failures += 1
            print(f"[telegram] fallo al enviar: {exc}", file=sys.stderr)
            return False


def _esc(value: str) -> str:
    return html.escape(str(value), quote=False)


def _selection_label(key: str, outcome: str, sport: str = "") -> str:
    from .types import split_key

    market, point = split_key(key)
    label = OUTCOME_LABELS.get(outcome, outcome)
    if market == "btts":
        return f"Ambos marcan: {label}"
    if market == "h2h_ht":
        return f"1ª parte: {label}"
    if market in ("sets_home", "sets_away"):
        who = "Local" if market == "sets_home" else "Visitante"
        return (
            f"{who} gana al menos 1 set" if outcome == "over"
            else f"{who} pierde todos los sets"
        )
    if market == "cards":
        unit = " tarjetas"
    elif market == "corners":
        unit = " córners"
    elif market == "team_totals_home":
        unit = " goles del local"
    elif market == "team_totals_away":
        unit = " goles del visitante"
    elif market == "totals":
        unit = {
            "basketball": " puntos",
            "table_tennis": " juegos",
            "tennis": " juegos",
        }.get(sport, " goles")
    else:
        unit = ""
    if point is not None:
        return f"{label} {point:g}{unit}"
    return label


SPORT_RISK = {
    "tennis": (
        " Tenis: vigila retiradas/lesiones y partidos que se alargan varios días; "
        "comprueba la regla de anulación de tu casa."
    ),
    "mma": (
        " MMA: el pesaje, una lesión o un 'no contest' pueden anular la pelea."
    ),
    "basketball": " Baloncesto: lesiones de última hora y rotaciones pueden cambiar el resultado.",
    "table_tennis": " Tenis de mesa: sensible a retiradas y al estado del jugador.",
}


def explain(signal: Signal) -> tuple[str, str]:
    """Devuelve (por qué apostar, si es seguro o no), en lenguaje claro."""
    prob, price, kind = signal.fair_prob, signal.price, signal.kind

    if kind == "VALUE" and prob and price:
        fair = 1.0 / prob
        edge = f"{signal.edge:+.1%}" if signal.edge is not None else "un margen positivo"
        why = (
            f"el modelo le da {prob:.0%} de probabilidad y la casa paga {price:.2f} "
            f"(cuota justa {fair:.2f}): a ese precio la apuesta gana {edge} de media "
            "a largo plazo"
        )
        safe = (
            "NO es segura: es una ventaja estadística, puedes perder este evento. "
            "Arriesga solo el importe indicado."
        )
        if kind == "VALUE" and assess(signal).decision == "APOSTAR":
            safe = (
                "Es una apuesta de valor con ventaja estadística: no es garantía, "
                "pero el modelo la recomienda con importe reducido."
            )
    elif kind == "ARB":
        why = (
            "distintas casas permiten cubrir TODOS los resultados y quedar con "
            "beneficio hagan lo que hagan"
        )
        safe = (
            "SÍ es casi segura: apuesta todas las cuotas del detalle antes de que "
            "cambien. Riesgo: error al colocar, límites de apuesta o anulación."
        )
    elif kind == "MISPRICING":
        why = "una casa paga mucho más que las demás y el modelo coincide con el consenso"
        safe = (
            "NO es segura: la casa puede tener información o corregir la cuota; "
            "tómalo como pista, no como certeza."
        )
    elif kind == "STEAM":
        why = "está entrando dinero y la cuota baja rápido: el mercado se está ajustando"
        safe = "NO es segura: es una señal de movimiento, no una recomendación de apuesta."
    elif kind == "DRIFT":
        why = "la cuota sube: la casa se está cubriendo o hay dudas sobre el favorito"
        safe = "NO es segura: es informativo; espera a que el precio se estabilice."
    elif kind == "DIVERGENCE":
        why = "las casas no coinciden, así que puede haber valor en la más generosa"
        safe = "NO es segura: compara casas y decides tú con tu propio criterio."
    else:
        why = "el bot ha detectado una anomalía en el mercado"
        safe = "NO es segura: revisa los datos antes de decidir."

    return why, safe + SPORT_RISK.get(signal.sport, "")


_ARB_LEG = re.compile(
    r"\b(home|away|draw|over|under|yes|no)\s+([\d.]+)\s+en\s+([A-Za-z0-9_.-]+)",
    re.IGNORECASE,
)


def exact_bet(signal: Signal, risk) -> str:
    """Instrucción literal: qué selección, a qué cuota y en qué casa."""
    if risk.decision != "APOSTAR":
        return "APUESTA EXACTA: NINGUNA · no apuestes a esta señal."

    if signal.kind != "ARB":
        label = _selection_label(signal.market, signal.outcome, signal.sport)
        stake = f" · {signal.stake:.2f} €" if signal.stake else ""
        note = " · riesgo medio: stake reducido" if risk.level == "MEDIO" else ""
        return (
            f"APUESTA EXACTA: {label} a {signal.price:.2f} "
            f"en {signal.bookmaker}{stake}{note}"
        )

    legs = [
        (code.lower(), float(price), book)
        for code, price, book in _ARB_LEG.findall(signal.detail or "")
    ]
    legs = [leg for leg in legs if leg[1] > 1.0]
    if len(legs) < 2:
        return (
            f"APUESTA EXACTA: {_selection_label(signal.market, signal.outcome, signal.sport)} "
            f"a {signal.price:.2f} en {signal.bookmaker} (ver detalle del arbitraje)"
        )

    total = signal.stake or 20.0
    inv_sum = sum(1.0 / price for _, price, _ in legs)
    payout = total / inv_sum
    parts = []
    for code, price, book in legs:
        stake = payout / price
        label = _selection_label(signal.market, code, signal.sport)
        parts.append(f"{label} @{price:.2f} en {book}: {stake:.2f} €")
    profit = payout - total
    return (
        "APUESTA EXACTA: " + " + ".join(parts)
        + f" · beneficio ~{profit:.2f} € por {total:.0f} € invertidos"
    )


def _event_word(sport: str) -> str:
    return {
        "soccer": "Partido",
        "basketball": "Partido",
        "tennis": "Encuentro",
        "table_tennis": "Encuentro",
        "mma": "Combate",
    }.get(sport, "Evento")


def _is_grave(signal: Signal) -> bool:
    return (
        signal.kind in {"ARB", "MISPRICING", "DIVERGENCE"}
        and (signal.severity >= 3 or "ERROR GRAVE" in (signal.detail or ""))
    )


def format_signal(signal: Signal) -> str:
    label, why = KIND_INFO.get(signal.kind, (signal.kind, ""))
    sport = SPORT_LABELS.get(signal.sport, signal.sport)
    risk = assess(signal)
    lines = [
        f"<b>{_esc(label)}</b>" + (f" · {_esc(why)}" if why else ""),
    ]
    if _is_grave(signal):
        lines.append("<b>PELIGRO: ERROR GRAVE · revisa antes de apostar</b>")
    lines.append(
        f"<b>DECISIÓN: {_esc(risk.decision)}</b> · "
        f"riesgo {_esc(risk.level)} ({risk.score}/100)"
    )
    lines.append(f"<b>{_esc(exact_bet(signal, risk))}</b>")
    lines += [
        f"{_event_word(signal.sport)}: <b>{_esc(signal.match)}</b>",
        f"Cuándo: {_esc(_kickoff(signal.commence))}",
        f"Deporte: {_esc(sport)} · {_esc(signal.league)}",
        f"Factores: {_esc(factors.describe(signal))}",
        f"Mercado: {_esc(_selection_label(signal.market, signal.outcome, signal.sport))}",
        f"Cuota: <b>{signal.price:.2f}</b> en {_esc(signal.bookmaker)}",
    ]
    if signal.fair_prob:
        lines.append(f"Cuota justa: {1 / signal.fair_prob:.2f}")
    if signal.edge is not None:
        lines.append(f"Ventaja: <b>{signal.edge:+.1%}</b>")
    if signal.stake is not None and signal.stake > 0 and risk.decision == "APOSTAR":
        lines.append(f"Apuesta: <b>{signal.stake:.2f} €</b>")
    if signal.fair_prob is not None:
        lines.append(f"Prob. modelo: {signal.fair_prob:.1%}")
    if signal.detail:
        lines.append(f"<i>Detalle: {_esc(signal.detail)}</i>")
    why, safe = explain(signal)
    lines.append(f"Por qué: {_esc(why)}")
    lines.append(f"<b>Seguridad:</b> {_esc(safe)}")
    lines.append(f"<i>Motivos del riesgo: {_esc('; '.join(risk.reasons))}</i>")
    action = KIND_ACTION.get(signal.kind)
    if risk.decision == "NO APOSTAR":
        action = "ACCIÓN: NO APOSTES a esta señal."
    elif risk.decision == "ESPERAR":
        action = "ACCIÓN: ESPERA a tener más datos antes de decidir."
    if action:
        lines.append(f"<i>{_esc(action)}</i>")
    return "\n".join(lines)


def format_brief(signal: Signal) -> str:
    """Versión de una línea para señales que no son apuestas recomendadas."""
    risk = assess(signal)
    label = KIND_INFO.get(signal.kind, (signal.kind, ""))[0]
    market = _selection_label(signal.market, signal.outcome, signal.sport)
    flag = "PELIGRO" if _is_grave(signal) else "INFO"
    return (
        f"[{flag}][{label}] {risk.decision} · {signal.match} · "
        f"{market} · riesgo {risk.level} ({risk.score}/100) · sin apuesta recomendada"
    )


def format_batch(signals: Sequence[Signal], header: str = "") -> str:
    blocks = [header] if header else []
    blocks.extend(format_signal(s) for s in signals)
    return "\n\n".join(blocks)


def format_signal_plain(signal: Signal) -> str:
    """Versión sin HTML ni emoji, para consola y logs."""
    label, why = KIND_INFO.get(signal.kind, (signal.kind, ""))
    sport = SPORT_LABELS.get(signal.sport, signal.sport)
    header = f"[{label}]" + (f" {why}" if why else "")
    risk = assess(signal)
    body = [header]
    if _is_grave(signal):
        body.append("PELIGRO: ERROR GRAVE · revisa antes de apostar")
    body.append(
        f"DECISIÓN: {risk.decision} · riesgo {risk.level} ({risk.score}/100)"
    )
    body.append(exact_bet(signal, risk))
    body += [
        f"{_event_word(signal.sport)}: {signal.match}",
        f"Cuándo:  {_kickoff(signal.commence)}",
        f"Deporte: {sport} · {signal.league}",
        f"Factores: {factors.describe(signal)}",
        f"Mercado: {_selection_label(signal.market, signal.outcome, signal.sport)}",
        f"Cuota:   {signal.price:.2f} en {signal.bookmaker}",
    ]
    if signal.fair_prob:
        body.append(f"Cuota justa: {1 / signal.fair_prob:.2f}")
    if signal.edge is not None:
        body.append(f"Ventaja: {signal.edge:+.1%}")
    if signal.stake and risk.decision == "APOSTAR":
        body.append(f"Apuesta: {signal.stake:.2f} EUR")
    if signal.fair_prob is not None:
        body.append(f"Prob. modelo: {signal.fair_prob:.1%}")
    if signal.detail:
        body.append(f"Detalle: {signal.detail}")
    why, safe = explain(signal)
    body.append(f"Por qué: {why}")
    body.append(f"Seguridad: {safe}")
    body.append(f"Motivos del riesgo: {'; '.join(risk.reasons)}")
    action = KIND_ACTION.get(signal.kind)
    if risk.decision == "NO APOSTAR":
        action = "ACCIÓN: NO APOSTES a esta señal."
    elif risk.decision == "ESPERAR":
        action = "ACCIÓN: ESPERA a tener más datos antes de decidir."
    if action:
        body.append(action)
    return "\n".join(body)


def build_notifier(token: str, chat_id: str, force_console: bool = False) -> Notifier:
    if force_console or not token or not chat_id:
        return ConsoleNotifier()
    return TelegramNotifier(token, chat_id)


# --------------------------------------------------------------------- ntfy


def _post(
    url: str, data: bytes, headers: dict[str, str], timeout: float, name: str
) -> bool:
    req = urllib.request.Request(url, data=data, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return 200 <= resp.status < 300
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
        print(f"[{name}] fallo al enviar: {exc}", file=sys.stderr)
        return False


class NtfyNotifier:
    """Push gratuito a móvil/escritorio con ntfy.sh. Sin cuenta ni token.

    1. Instala la app ``ntfy`` (Android/iOS) o abre https://ntfy.sh.
    2. Suscríbete a un tema inventado y difícil de adivinar, p. ej.
       ``hecto-bets-8f3a91``.
    3. Pon ese tema en ``.env`` como ``NTFY_TOPIC``. Nada más.
    """

    name = "ntfy"

    def __init__(
        self,
        topic: str,
        server: str = "https://ntfy.sh",
        timeout: float = 10.0,
    ) -> None:
        if not topic:
            raise ValueError("ntfy necesita un tema (NTFY_TOPIC)")
        self.topic = topic.strip().strip("/")
        self.server = (server or "https://ntfy.sh").rstrip("/")
        self.timeout = timeout

    def format(self, signal: Signal) -> str:
        return format_signal_plain(signal)

    def send(self, text: str) -> bool:
        url = f"{self.server}/{self.topic}"
        headers = {
            "Title": "Betting bot",
            "Tags": "moneybag",
            "Content-Type": "text/plain; charset=utf-8",
        }
        return _post(url, text.encode("utf-8"), headers, self.timeout, "ntfy")


class WindowsNotifier:
    """Aviso local en Windows (globo/toast). Sin cuenta ni internet.

    Usa PowerShell y ``System.Windows.Forms.NotifyIcon``, que ya vienen con
    Windows. Sirve para comprobar que el bot alerta aunque ntfy/Telegram no
    lleguen al móvil.
    """

    name = "windows"
    MAX_CHARS = 240

    def __init__(self, timeout: float = 25.0) -> None:
        self.timeout = timeout

    def format(self, signal: Signal) -> str:
        return format_signal_plain(signal)

    def send(self, text: str) -> bool:
        import base64
        import os
        import subprocess

        if os.name != "nt":
            # En la nube no hay escritorio: el canal se omite y ntfy/Discord
            # siguen enviando las alertas.
            return True

        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        # El globo de Windows admite muy pocos caracteres: resumen + seguridad.
        keep = [lines[0]]
        keep += [
            ln for ln in lines[1:] if ln.startswith(("PELIGRO:", "DECISIÓN:", "APUESTA EXACTA:"))
        ]
        keep += [
            ln for ln in lines[1:]
            if ln.startswith(("Partido:", "Encuentro:", "Combate:", "Evento:", "Cuándo:", "Factores:"))
        ]
        seg = next((ln for ln in lines if ln.startswith("Seguridad:")), None)
        if seg:
            keep.append(seg)
        keep += [
            ln for ln in lines[3:]
            if ln.startswith(("Mercado:", "Ventaja:", "Apuesta:"))
        ]
        body = " | ".join(keep)
        if len(body) > self.MAX_CHARS:
            body = body[: self.MAX_CHARS - 3] + "..."
        payload = base64.b64encode(body.encode("utf-8")).decode("ascii")
        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "Add-Type -AssemblyName System.Drawing;"
            f"$t=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('{payload}'));"
            "$n=New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon=[System.Drawing.SystemIcons]::Information;"
            "$n.Visible=$true;"
            "$n.ShowBalloonTip(10000,'Betting bot',$t,"
            "[System.Windows.Forms.ToolTipIcon]::Info);"
            "Start-Sleep -Seconds 6;"
            "$n.Dispose();"
        )
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=self.timeout,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"[windows] fallo al enviar: {exc}", file=sys.stderr)
            return False
        if proc.returncode != 0:
            err = proc.stderr.decode("utf-8", "replace").strip()
            print(f"[windows] fallo al enviar: {err}", file=sys.stderr)
            return False
        return True


class MultiNotifier:
    """Envía a varios canales a la vez (p. ej. ``windows,ntfy``)."""

    def __init__(self, channels: Sequence[Notifier]) -> None:
        if not channels:
            raise ValueError("MultiNotifier necesita al menos un canal")
        self.channels = list(channels)
        self.name = "+".join(ch.name for ch in self.channels)

    def format(self, signal: Signal) -> str:
        return self.channels[0].format(signal)

    def send(self, text: str) -> bool:
        ok = True
        for channel in self.channels:
            if not channel.send(text):
                ok = False
        return ok


class DiscordNotifier:
    """Webhook de Discord: canal gratuito y sin crear bot.

    En Discord: Ajustes del canal → Integraciones → Webhooks → Nuevo webhook →
    Copiar URL. Pega la URL en ``DISCORD_WEBHOOK_URL``.
    """

    name = "discord"
    MAX_CHARS = 1900

    def __init__(self, webhook_url: str, timeout: float = 10.0) -> None:
        if not webhook_url:
            raise ValueError("Discord necesita la URL del webhook")
        self.webhook_url = webhook_url
        self.timeout = timeout

    def format(self, signal: Signal) -> str:
        return format_signal_plain(signal)

    @classmethod
    def _chunks(cls, text: str) -> list[str]:
        if len(text) <= cls.MAX_CHARS:
            return [text]
        chunks, current = [], ""
        for line in text.splitlines(keepends=True):
            if len(current) + len(line) > cls.MAX_CHARS:
                chunks.append(current)
                current = ""
            current += line
        if current:
            chunks.append(current)
        return chunks or [text[: cls.MAX_CHARS]]

    def send(self, text: str) -> bool:
        ok = True
        for chunk in self._chunks(text):
            payload = json.dumps({"content": chunk}).encode("utf-8")
            ok = _post(
                self.webhook_url,
                payload,
                {"Content-Type": "application/json"},
                self.timeout,
                "discord",
            ) and ok
        return ok


def _build_one(cfg, kind: str) -> Notifier:
    token = getattr(cfg, "telegram_token", "")
    chat = getattr(cfg, "telegram_chat_id", "")
    topic = getattr(cfg, "ntfy_topic", "")
    webhook = getattr(cfg, "discord_webhook_url", "")

    if kind == "auto":
        if token and chat:
            kind = "telegram"
        elif topic:
            kind = "ntfy"
        elif webhook:
            kind = "discord"
        else:
            return ConsoleNotifier()

    if kind == "telegram":
        if token and chat:
            return TelegramNotifier(token, chat)
        print("[alertas] notifier=telegram sin token/chat; uso consola", file=sys.stderr)
    elif kind == "ntfy":
        if topic:
            return NtfyNotifier(topic, getattr(cfg, "ntfy_server", "https://ntfy.sh"))
        print("[alertas] notifier=ntfy sin NTFY_TOPIC; uso consola", file=sys.stderr)
    elif kind == "discord":
        if webhook:
            return DiscordNotifier(webhook)
        print("[alertas] notifier=discord sin webhook; uso consola", file=sys.stderr)
    elif kind == "windows":
        return WindowsNotifier()
    elif kind == "console":
        return ConsoleNotifier()
    else:
        print(f"[alertas] notifier desconocido {kind!r}; uso consola", file=sys.stderr)
    return ConsoleNotifier()


def build_notifier_for_config(cfg, force_console: bool = False) -> Notifier:
    """Elige el canal según la configuración, con fallback a consola.

    ``notifier`` admite varios canales separados por coma, p. ej.
    ``windows,ntfy``.
    """
    if force_console:
        return ConsoleNotifier()

    raw = (getattr(cfg, "notifier", "auto") or "auto").lower()
    kinds = [k.strip() for k in raw.replace("+", ",").split(",") if k.strip()]
    if os.name != "nt" and "windows" in kinds:
        print("[alertas] canal windows ignorado (no es Windows)", file=sys.stderr)
        kinds = [k for k in kinds if k != "windows"]
    if not kinds:
        return ConsoleNotifier()

    channels = [_build_one(cfg, kind) for kind in kinds]
    if len(channels) == 1:
        return channels[0]
    return MultiNotifier(channels)


