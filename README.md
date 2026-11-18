# Bot de análisis de apuestas deportivas

Sistema multi-deporte que **analiza cuotas con estadística real**, detecta
errores de las casas y avisa por Telegram cuando las cuotas se mueven.

Deportes soportados: **fútbol, baloncesto, tenis, MMA/boxeo y tenis de mesa**.

> **Qué es**: una herramienta de análisis y alertas. Te dice dónde hay una
> discrepancia entre el precio y la probabilidad, no que vayas a ganar.
>
> **Qué no es**: una máquina de dinero. El propio backtest incluido
> (sección [Backtest](#10-backtest-la-prueba-de-fuego)) te mostrará, con datos
> reales, que batir al mercado es difícil. Eso es precisamente lo valioso: el
> sistema no te miente.

---

## 1. Arranque rápido

Requisitos: **Python 3.11 o superior**. Cero dependencias externas.

```powershell
cd C:\Users\hecto\betting-bot

# 1. Demo completa con datos sintéticos (sin red, sin claves)
python run.py demo

# 2. Backtest con datos reales de la Premier League 2024/25
python run.py backtest --csv data/E0.csv

# 3. Ver la configuración efectiva
python run.py config

# 4. Ver el catálogo de ligas y los mercados soportados
python run.py leagues
python run.py markets
```

Si tu `python` no está en el PATH, usa la ruta completa:

```powershell
& "C:\Users\hecto\venv\Scripts\python.exe" run.py demo
```

Tests:

```powershell
python -m unittest discover -s tests -t .
```

---

## 2. Cómo funciona

```
Proveedor de cuotas          Almacenamiento         Análisis              Alertas
(The Odds API / sample)  →   SQLite histórico   →   · devig              →   Telegram
                                                      · value + Kelly        o consola
                                                      · movimientos
                                                      · errores de casa
```

Cada sondeo (`poll`):

1. Descarga cuotas de varias casas para los deportes configurados.
2. Guarda una instantánea en SQLite (esto construye tu histórico gratis).
3. Convierte cuotas a probabilidades sin margen (*devig*).
4. Compara con la probabilidad del modelo de cada deporte.
5. Detecta: **value bets**, **arbitrajes**, **cuotas desalineadas** y
   **movimientos bruscos** dentro de una ventana de tiempo.
6. Envía cada señal nueva, con *cooldown* para no repetir la misma alerta.

---

## 3. La matemática (lo que hace que no sea un juguete)

### 3.1 Quitar el margen de la casa (devig)

Una cuota decimal `o` implica `1/o`. La suma siempre supera 1: ese exceso es el
margen. Cuatro métodos implementados en `bettingbot/devig.py`:

| Método | Idea | Sesgo que corrige |
|---|---|---|
| `multiplicative` | reparte el margen proporcionalmente | ninguno (referencia) |
| `additive` | resta el mismo margen a todos | margen constante |
| `power` | busca `k` con `Σ pᵏ = 1` | favorito-longshot |
| `shin` | modelo de insider trading (Shin, 1993) | el más realista |

Por defecto se usa **Shin**, y el consenso de mercado pondera más las casas
sharps (Pinnacle, Betfair) que las blandas.

### 3.2 Value bets y Kelly

```
edge = p_modelo × cuota_mejor − 1
```

Si `edge ≥ min_edge` (3% por defecto) se alerta. El stake usa **Kelly
fraccional al 25%**, limitado al 2% de la banca por apuesta:

```
f* = (p × o − 1) / (o − 1) × fracción_kelly
```

Además hay un **guarda de cordura**: si el modelo se separa más de 20 puntos
del consenso sharp, se descarta la señal (probablemente el modelo esté mal, no
la casa).

### 3.3 Movimientos de cuota

Por cada (evento, mercado, selección, casa) se guarda la serie temporal.
Se mide el cambio **en una ventana** (90 min por defecto):

- **STEAM**: la cuota cae ≥ 8% → dinero entrando.
- **DRIFT**: la cuota sube ≥ 10% → dinero en contra o mala noticia.
- Se agrupan todas las casas en **una sola alerta**, tomando Pinnacle como
  referencia cuando está disponible.

### 3.4 Errores de casa

- **MISPRICING**: una casa paga ≥ 5% por encima del consenso del mercado.
- **ARB**: apostando a la mejor cuota de cada selección, suma de inversos < 1.
- **DIVERGENCE**: las casas discrepan ≥ 12% entre la mejor y la peor cuota.

> Ojo con el **error palpable**: si la cuota es absurdamente buena, la casa
> puede anular la apuesta. Estas alertas son para investigar, no para apostar
> a ciegas.

---

## 4. Los modelos por deporte

| Deporte | Modelo | Archivo |
|---|---|---|
| Fútbol | **Dixon-Coles** (Poisson bivariado) con decaimiento temporal, `rho` y calibración `kappa` | `models/football.py` |
| Fútbol: tarjetas | **Poisson** con factor de equipo, árbitro y derbi | `models/cards.py` |
| Fútbol: córners | **Binomial negativa** (sobredispersión) con ritmo y contexto | `models/corners.py` |
| Baloncesto | Elo con margen de victoria + totals por normal | `models/basketball.py` |
| Tenis | Elo por superficie + modelo punto→juego→set→partido | `models/tennis.py`, `models/pointmodel.py` |
| MMA/Boxeo | Elo + ajustes de edad, inactividad y alcance | `models/mma.py` |
| Tenis de mesa | Elo con K alto + conversión a ventaja por punto | `models/tabletennis.py` |

Un deporte puede tener **varios modelos a la vez** (fútbol + tarjetas + córners).
El registro (`models/registry.py`) combina sus mercados.

### Mercados que analiza

| Mercado | Qué es | Cobertura |
|---|---|---|
| `h2h` | ganador / 1X2 | todos |
| `totals` | goles o puntos over/under **en cualquier línea** | Odds API, sample, manual |
| `btts` | ambos equipos marcan | sample, manual |
| `team_totals_home/away` | goles de un solo equipo | sample, manual |
| `spreads` | hándicap (el `point` es el del local) | Odds API, sample, manual |
| `cards` | tarjetas over/under | manual (la API casi no lo cubre) |
| `corners` | córners over/under | manual (la API casi no lo cubre) |
| `h2h_ht` | **ganador de la 1ª parte** (local/empate/visitante) | sample, manual; Odds API `h2h_1h` |
| `sets_home / sets_away` | **gana al menos 1 set** en tenis (`over 0.5`) | sample, manual; Odds API según liga |

El modelo de fútbol **no hardcodea líneas**: calcula exactamente la línea que
ofrezca la casa (2.5, 3.25, la que sea), además de goles por equipo y hándicap.
La probabilidad de la 1ª parte sale de la matriz Dixon-Coles con intensidad
reducida a media. En tenis se invierte el modelo de sets para obtener
`P(ganar al menos un set)` a partir de la probabilidad de ganar el partido
(mejor de 3 o de 5 según `extra.best_of`).

Con el proveedor `sample` ambos mercados ya generan alertas de demostración. Con
`manual` u Odds API, la 1ª parte se analiza con el prior de goles de la liga;
para los sets de tenis conviene tener un `TennisModel` ajustado con histórico
(ver §7), porque sin él no hay probabilidades fiables de sets.

### Ligas: priorizar donde hay más errores

`bettingbot/leagues.py` contiene ~70 ligas con:

* **tier** (1 élite, 2 segunda división/ligas fuertes, 3 nicho),
* **priors** de goles, tarjetas y córners por partido,
* **riesgo de error** (`baja`/`media`/`alta`),
* **prioridad** calculada: riesgo + bonus por liga secundaria.

Las ligas de tier 2-3 con riesgo alto (Serie B, Championship, Süper Lig,
Brasileirão B, ligas de Europa del Este, Asia...) reciben **severidad extra**
en las alertas. Mira el catálogo:

```powershell
python run.py leagues --sport soccer --max-tier 2
```

Si solo quieres vigilar unas pocas, ponlas en `config.toml`:

```toml
[general]
leagues = ["Serie B", "Championship", "Süper Lig", "Brasileirão Série B"]
```

El modelo de fútbol se ajusta por **máxima verosimilitud** (descenso con
búsqueda de línea, sin dependencias), con:

```
λ_local  = exp(μ + ataque_local − defensa_visitante + ventaja_local)
λ_visit  = exp(μ + ataque_visitante − defensa_local)
```

y corrección Dixon-Coles en resultados de pocos goles (0-0, 1-0, 0-1, 1-1).

El motor de tenis/tenis de mesa (`pointmodel.py`) resuelve la cadena de Markov
completa: probabilidad de punto → juego (con deuce en forma cerrada) → set →
partido, incluyendo tiebreak con saque alternando y reglas de 11 puntos.

---

## 5. Conectar The Odds API

1. Regístrate en <https://the-odds-api.com> y copia tu clave (hay plan gratis).
2. Crea `.env` a partir de `.env.example`:

```ini
ODDS_API_KEY=tu_clave
PROVIDER=oddsapi
```

3. Prueba:

```powershell
python run.py poll --console
```

El proveedor **descubre las ligas activas automáticamente** y limita cuántas
pide por deporte (`max_leagues_per_sport = 3`) para no quemar la cuota del plan.

> **Tenis de mesa**: la cobertura de las APIs comerciales es irregular. Si no
> aparece, el bot simplemente no genera señales de ese deporte. No se inventa
> nada.

### Sin modelo, ¿qué pasa?

Con `PROVIDER=oddsapi` y sin modelos ajustados, el bot **solo** detecta
movimientos y errores de casa. No inventa value bets. Para activar value bets
necesitas ajustar un modelo (sección siguiente).

---

## 5b. Proveedor manual: tarjetas y córners

The Odds API no da de forma fiable tarjetas ni córners, y en ligas menores la
cobertura es irregular. Para esos mercados hay un proveedor que lee un JSON
que escribes tú:

```powershell
Copy-Item data/odds_manual.example.json data/odds_manual.json
```

```toml
[general]
provider = "manual"
manual_odds_file = "data/odds_manual.json"
```

Formato:

```json
{
  "events": [
    {
      "id": "palermo-bari",
      "sport": "soccer",
      "league": "Serie B",
      "home": "Palermo",
      "away": "Bari",
      "commence": "2026-11-18T20:00:00+00:00",
      "extra": {"derby": true, "referee_factor": 1.15},
      "quotes": [
        {"bookmaker": "bet365", "market": "cards", "outcome": "over",
         "point": 4.5, "price": 2.05},
        {"bookmaker": "pinnacle", "market": "corners", "outcome": "under",
         "point": 10.5, "price": 1.90}
      ]
    }
  ]
}
```

También puedes escribir `h2h_ht` (local/draw/away) para la 1ª parte y
`sets_home`/`sets_away` (over/under, point `0.5`) para "gana al menos un set";
el ejemplo `odds_manual.example.json` ya trae ambos.

Con `provider="manual"` el bot registra automáticamente los modelos de
tarjetas y córners (funcionan con los priors de liga sin histórico), así que
detecta value en esos mercados desde el primer momento. El archivo se relee en
cada sondeo: puedes editarlo con el bot corriendo.

`extra` admite `derby`, `referee_factor`, `pace` y `must_win` para ajustar los
modelos de conteo.

---

## 6. Conectar Telegram

1. Habla con [@BotFather](https://t.me/BotFather), crea un bot y copia el token.
2. Escríbele algo a tu bot y abre
   `https://api.telegram.org/bot<TOKEN>/getUpdates` para obtener tu `chat_id`.
3. En `.env`:

```ini
TELEGRAM_TOKEN=123456:ABC...
TELEGRAM_CHAT_ID=123456789
```

4. Verifica:

```powershell
python run.py test-telegram
```

5. Vigilancia continua:

```powershell
python run.py watch --interval 300
```

### Cómo leer una alerta

Todas las alertas llevan: tipo, partido/encuentro/combate, **fecha y hora en
hora de España**, deporte y liga, mercado, cuota de la casa, cuota justa
(según el modelo), ventaja, apuesta sugerida y una línea `ACCIÓN` con lo que
significa. Ejemplo real generado por `python run.py preview`:

```
[VALOR] la cuota paga más de lo que vale: hay ventaja
Partido: Palermo vs Bari
Cuándo:  mar 1 dic 21:00 (hora de España)
Deporte: Fútbol · Serie B
Factores: Palermo en casa vs Bari fuera; Serie B: media 2.6 goles; línea 2.5 vs media 2.6 goles
Mercado: Más de 2.5 goles
Cuota:   2.10 en bet365
Cuota justa: 1.92
Ventaja: +9.2%
Apuesta: 18.40 EUR
Prob. modelo: 52.0%
Detalle: modelo 52.0% vs cuota 2.10 (justa 1.92) · Kelly 25%
DECISIÓN: NO APOSTAR · riesgo ALTO (23/100)
APUESTA EXACTA: NINGUNA · no apuestes a esta señal.
Por qué: el modelo le da 52% de probabilidad y la casa paga 2.10 (cuota justa 1.92): a ese precio la apuesta gana +9.2% de media a largo plazo
Seguridad: NO es segura: es una ventaja estadística, puedes perder este evento. Arriesga solo el importe indicado.
Motivos del riesgo: probabilidad del modelo 52%; mercado de recuento/total: más variable; liga con historial de errores (Serie B)
ACCIÓN: NO APOSTES a esta señal.
```

- `APUESTA EXACTA` da la orden literal: selección, cuota, casa y, cuando la
  decisión es APOSTAR, el importe. En un arbitraje lista **todas** las casas y
  cuánto poner en cada una para igualar el retorno (ejemplo con 20 € de referencia). Si la decisión es ESPERAR o NO APOSTAR, dice `NINGUNA`.
- `DECISIÓN` es la recomendación final: **APOSTAR**, **ESPERAR** o **NO APOSTAR**.
  Solo aparece `Apuesta:` (importe sugerido) cuando la decisión es APOSTAR, para
  que el mensaje no se contradiga.
- `PELIGRO: ERROR GRAVE` aparece en los errores serios: es una alerta para
  revisar, **no** para apostar a ciegas.
- `Factores` resume lo que el bot **sí sabe** del partido (local/visitante o
  jugadores, media de goles/tarjetas/córners de la liga y línea comparada). Si
  el proveedor no aporta forma, superficie o lesiones, lo dice: **no inventa
  datos de jugadores**. Contrástalos siempre antes de apostar.
- `Riesgo` va de 0 a 100 (BAJO ≥70, MEDIO ≥50, ALTO <50) y `Motivos del riesgo`
  explica la puntuación. Tenis y MMA penalizan por retiradas/lesiones;
  tarjetas y córners, por su variabilidad.

- `Cuota justa` es la que correspondería a la probabilidad del modelo; si la
  cuota de la casa es mayor, hay valor.
- `Ventaja` es cuánto gana la apuesta a largo plazo: 2.10 × 52.0% − 1 = +9.2%.
- `Apuesta` es la sugerencia Kelly (25%) con tope del 2% de la banca.
- `Por qué` da el motivo numérico; `Seguridad` dice claramente si es fiable o
  no. **Solo el arbitraje es casi seguro**, y aun así puede fallar por errores
  al colocar, límites o anulación. Todo lo demás es ventaja estadística, no
  certeza.
- En **tenis** se añade una advertencia específica: retiradas/lesiones y
  partidos que se alargan varios días; comprueba la regla de anulación. En MMA
  se avisa del pesaje y los `no contest`.
- `GRAVEDAD: ALTA` aparece en los errores serios (severidad 3): arbitrajes y
  desvíos grandes. Se buscan en partidos de hasta **7 días**, aunque las apuestas
  de valor y los movimientos se limitan al rango de 1-3 días.
- Los tipos son: `VALOR`, `ARBITRAJE`, `POSIBLE ERROR`, `CUOTAS CAYENDO`,
  `CUOTAS SUBIENDO` y `CASAS DIVIDIDAS`.
- Los avisos de Windows son un resumen corto (el globo solo admite ~255
  caracteres) que incluye tipo, partido, fecha y `Seguridad`; ntfy/Discord/
  Telegram reciben el texto completo.

```powershell
python run.py preview --send
```

Envía 8 ejemplos (uno por tipo y deporte incluido MMA y tenis de mesa) a tu
canal configurado para que veas cómo se leen en el móvil. Las fechas de los
ejemplos se generan **relativas a hoy**, nunca fijas.

### Solo apuestas seguras y solo 1-3 días

Por defecto `SAFETY_MODE=safe`: el bot calcula una puntuación de riesgo y solo
te deja pasar señales con `riesgo BAJO` (≥70). Además reduce Kelly al 10% y el
tope por apuesta al 1% de la banca. Si prefieres ver todo, pon
`SAFETY_MODE=balanced` o `SAFETY_MODE=all` en `.env`.

Los errores se siguen buscando hasta 7 días (`ERROR_SCAN_DAYS=7`), pero
`NOTIFY_DAY=days:1` impide que te lleguen notificaciones de partidos fuera del
rango de 1-3 días. Puedes ampliarlo a `days:2` o `days:3`.

Con `FOCUS_ACTIONABLE=1` (por defecto) **solo se envían las apuestas con
decisión `APOSTAR`**. El resto queda registrado en la base de datos y se muestra
abreviado en `demo`/`preview` (`[INFO] ... sin apuesta recomendada`), para que no
te distraigan. Pon `FOCUS_ACTIONABLE=0` si quieres volver a recibirlo todo.

Con `SINGLE_BOOK_ONLY=1` (por defecto) **nunca se apuesta en dos casas**: los
arbitrajes se descartan y se muestran como `[INFO] ... necesitas dos casas`. Los
errores de casa con ventaja se convierten en una apuesta VALUE normal en esa
misma casa, con Kelly calculado y `APUESTA EXACTA` de una sola casa. Si quieres
volver a ver arbitrajes, pon `SINGLE_BOOK_ONLY=0`.

Se aceptan **dos niveles de riesgo**, nunca alto: `BAJO` (lo más seguro) y
`MEDIO` (ventaja moderada). Con `MAX_RISK_LEVEL=MEDIO` (por defecto) te llegan
ambos; las apuestas de riesgo medio llevan `riesgo medio: stake reducido` y se
les aplica la **mitad del importe**. Pon `MAX_RISK_LEVEL=BAJO` para recibir solo
las más seguras, o `ALTO` para ver también las arriesgadas (desaconsejado).


### Si Telegram te bloquea la creación de bots

Telegram a veces restringe la creación de bots en cuentas nuevas o marcadas
por su antispam. Si BotFather responde *"you cannot create new bots at this
time"*:

1. Habla con `@SpamBot` y envía `/start`. Te dirá si tienes límite y la fecha
   de fin, y ofrecerá un botón de apelación.
2. La restricción suele levantarse sola con el tiempo.
3. Mientras tanto, usa **ntfy** o **Discord**: cero burocracia.

**ntfy (recomendado como sustituto)**: push gratis, sin cuenta ni bot.
1. Instala la app `ntfy` en el móvil (o abre <https://ntfy.sh>).
2. Suscríbete a un tema inventado y difícil de adivinar, por ejemplo
   `hecto-bets-8f3a91`.
3. En `.env`:

```ini
NOTIFIER=ntfy
NTFY_TOPIC=hecto-bets-8f3a91
```

4. Prueba: `python run.py test-notify`.

**Aviso local en Windows** (`windows`): globo/toast del propio Windows, sin
cuenta ni internet. Útil para confirmar que el bot alerta aunque el móvil no
reciba el push:

```ini
NOTIFIER=windows
```

Puedes combinar canales separándolos por coma; la alerta va a todos:

```ini
NOTIFIER=windows,ntfy
```

**Discord**: Ajustes del canal → Integraciones → Webhooks → Nuevo webhook →
Copiar URL. Luego en `.env`:

```ini
NOTIFIER=discord
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...
```

Con `NOTIFIER=auto` (por defecto) el bot elige Telegram si está configurado,
si no ntfy, si no Discord, y si no consola. Acepta lista: `windows,ntfy,discord`.
`python run.py config` te dice cuál usaría.


---

## 7. Ajustar el modelo de fútbol con datos reales

Descarga CSVs gratuitos de <https://www.football-data.co.uk> (Premier, LaLiga,
Serie A, Bundesliga, Ligue 1...). Formato esperado: columnas `Div, Date,
HomeTeam, AwayTeam, FTHG, FTAG` y cuotas `B365H/D/A` o `PSCH/PSD/PSCA`.

```powershell
python run.py backtest --csv data/E0.csv --book b365
```

El CSV de ejemplo (`data/E0.csv`, Premier League 2024/25) ya está incluido.

Para producción, la idea es encadenar varias temporadas y ligas:

```python
from bettingbot.backtest import load_csv
from bettingbot.models.football import FootballModel, MatchResult
from bettingbot.models.registry import ModelRegistry

rows = load_csv("data/E0.csv")
model = FootballModel().fit(
    [MatchResult(r.date, r.home, r.away, r.home_goals, r.away_goals) for r in rows]
)
registry = ModelRegistry()
registry.register("soccer", model)
```

---

## 8. Configuración

Copia `config.example.toml` a `config.toml`. Los secretos van en `.env`.

| Parámetro | Defecto | Significado |
|---|---|---|
| `sports` | los 5 | deportes a analizar |
| `leagues` | vacío (= todas) | si se rellena, solo esas ligas |
| `prioritize_error_prone` | `true` | sube severidad en ligas con riesgo alto |
| `provider` | `sample` | `sample`, `oddsapi` o `manual` |
| `manual_odds_file` | `data/odds_manual.json` | JSON del proveedor manual |
| `poll_interval` | 180 | segundos entre sondeos |
| `alert_cooldown_min` | 120 | no repetir la misma alerta antes de esto |
| `event_day` | `days:1` | partidos: `days:1` a `days:3`, `next24h`, `today`, `tomorrow`, `hours:12`, `all` |
| `error_scan_days` | 7 | errores graves (ARB/MISPRICING/DIVERGENCE) hasta 7 días |
| `notify_day` | `days:1` | solo se **notifican** partidos de 1-3 días |
| `safety_mode` | `safe` | `safe` (solo lo más seguro), `balanced` o `all` |
| `min_safe_score` | 70 | umbral de riesgo para `safe` |
| `focus_actionable` | `true` | solo se envían señales con decisión APOSTAR |
| `single_book_only` | `true` | nunca apuestas en dos casas (se descartan arbitrajes) |
| `min_single_book_score` | 45 | umbral para value de una sola casa |
| `max_risk_level` | `MEDIO` | `BAJO` (solo seguro), `MEDIO` (recomendado) o `ALTO` |
| `value.min_edge` | 0.03 | ventaja mínima para alertar |
| `value.kelly_fraction` | 0.25 | fracción de Kelly |
| `value.max_stake_pct` | 0.02 | tope por apuesta sobre la banca |
| `value.bankroll` | 1000 | banca de referencia |
| `movement.drop_pct` | 0.08 | caída que dispara STEAM |
| `movement.rise_pct` | 0.10 | subida que dispara DRIFT |
| `movement.window_min` | 90 | ventana de análisis |
| `errors.mispricing_edge` | 0.05 | desvío mínimo vs consenso |
| `notifier` | `auto` | canal: console/telegram/ntfy/windows/discord (o lista `windows,ntfy`) |
| `ntfy_topic` | vacío | tema de ntfy.sh para push |
| `discord_webhook_url` | vacío | webhook de Discord |

Prioridad de configuración: defectos del código → `config.toml` → variables de
entorno → `.env`.

---

## 9. La nube (24/7 sin depender del PC)

El bot no necesita pip ni servicios externos, así que se puede alojar en
cualquier VPS o proveedor de contenedores. En la nube **no hay escritorio**:
el canal `windows` se ignora automáticamente y las alertas van por **ntfy**
(o Discord). La fecha sigue mostrándose en hora de España.

**Opción 0 — GitHub Actions (servidor gratis, sin VPS):**
Incluyo `.github/workflows/poll.yml`, que ejecuta `run.py poll` cada 5 minutos
en las máquinas de GitHub. No tienes que mantener ningún servidor.

1. Crea un repo en GitHub y sube esta carpeta.
2. En *Settings → Secrets and variables → Actions* añade:
   - `NTFY_TOPIC` = tu tema (el que ya tienes: `bet-td7esrsx2x2hlhaxos`).
   - `PROVIDER` = `sample` o `oddsapi`.
   - `ODDS_API_KEY` = solo si usas `oddsapi`.
3. Pestaña *Actions* → habilita el workflow → **Run workflow** para probar.

El workflow guarda `betting.db` en la caché de GitHub para conservar el
cooldown; si la caché no se restaura, las alertas podrían repetirse. Ojo: los cron de GitHub pueden retrasarse unos minutos en horas
punta. Con repo público no consumes minutos; con privado, el gasto es muy bajo
(~1 h/mes).

**Opción A — Docker / docker compose (recomendada):**

```powershell
Copy-Item .env.cloud.example .env.cloud
# edita .env.cloud: PROVIDER, ODDS_API_KEY, NTFY_TOPIC...
docker compose up -d --build
docker compose logs -f
```

El volumen `bot-data` guarda `betting.db`, así que el cooldown y el historial
sobreviven a reinicios. `restart: unless-stopped` lo levanta solo si el
contenedor case o la máquina se reinicia.

**Opción B — VPS con systemd (sin Docker):**

```bash
sudo mkdir -p /opt/betting-bot /var/lib/betting-bot
# copia el proyecto a /opt/betting-bot y .env.cloud a ese directorio
sudo cp bettingbot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now bettingbot
sudo journalctl -u bettingbot -f
```

Ajusta `User`, `WorkingDirectory` y `Environment=DB_PATH` en el `.service`
según tu VPS.

**Datos reales:** pon `PROVIDER=oddsapi` y tu `ODDS_API_KEY` en `.env.cloud`.
Para tarjetas, córners, 1ª parte y sets usa `PROVIDER=manual` con tu JSON.
Necesitas un servidor con **disco persistente**; si el proveedor solo ofrece
filesystem efímero, las alertas se repetirán porque se pierde el cooldown.

---

## 10. Comandos

| Comando | Qué hace |
|---|---|
| `python run.py demo` | pipeline completo con datos sintéticos |
| `python run.py preview` | muestra una alerta explicada de cada tipo `--send` para recibirlas |
| `python run.py poll` | un sondeo real + análisis |
| `python run.py watch` | bucle continuo |
| `python run.py report` | últimas alertas y estadísticas |
| `python run.py backtest --csv ...` | evalúa el modelo contra el mercado |
| `python run.py leagues` | catálogo de ligas, priors y prioridad |
| `python run.py markets` | mercados soportados y cobertura |
| `python run.py test-telegram` | mensaje de prueba |
| `python run.py test-notify` | prueba el canal configurado (ntfy/Discord/Telegram) |
| `python run.py config` | configuración efectiva |

Opciones útiles:

```powershell
python run.py demo --steps 6 --sports soccer,tennis
python run.py poll --console
python run.py backtest --csv data/E0.csv --book pinnacle --max-price 3.0
```

---

## 11. Backtest: la prueba de fuego

Con `data/E0.csv` (Premier League 2024/25, 380 partidos, 266 entrenamiento /
114 test) y la casa **b365**:

```
Casa de referencia: b365 · calibración kappa=1.71
Partidos: 266 entrenamiento / 114 test
Log-loss modelo: 0.9937
Log-loss mercado: 0.9387  (el modelo NO bate al mercado)
Apuestas (edge mínimo 3%): 108 · acierto 24.1%
ROI: -22.23%
```

**Interpretación honesta**:

1. Un Dixon-Coles básico **no bate a la casa**. Casi ningún modelo público lo
   hace de forma sostenida. Si un bot te promete lo contrario, desconfía.
2. Apostar siempre a la mayor ventaja tiende a seleccionar *longshots*, donde
   el error del modelo es mayor. Restringir a favoritos o a cuotas bajas cambia
   el resultado, pero con 20-40 apuestas la muestra es demasiado pequeña para
   concluir nada.
3. Donde este sistema sí aporta valor real: **comparar precios entre casas**
   (quedarte siempre con la mejor cuota), **detectar movimientos** antes de que
   el mercado se ajuste, y **detectar errores** de casas blandas.

Prueba tú mismo variantes:

```powershell
python run.py backtest --csv data/E0.csv --book b365 --max-price 2.5
python run.py backtest --csv data/E0.csv --book pinnacle
```

La regla de oro: si no hay ventaja clara, **no se apuesta**.

---

## 12. Estructura del proyecto

```
betting-bot/
├── run.py                     entrada CLI
├── config.example.toml
├── .env.example
├── data/E0.csv                datos reales de ejemplo
├── bettingbot/
│   ├── types.py               Event, Quote, Signal
│   ├── config.py              TOML + entorno
│   ├── leagues.py             catálogo de ligas, priors y riesgo de error
│   ├── devig.py               métodos de eliminación de margen
│   ├── storage.py             SQLite: eventos, cuotas, alertas
│   ├── alerts.py              consola y Telegram
│   ├── pipeline.py            orquestación
│   ├── backtest.py            evaluación contra el mercado
│   ├── cli.py                 comandos
│   ├── analysis/
│   │   ├── value.py           edge + Kelly
│   │   ├── movement.py        steam / drift
│   │   └── errors.py          mispricing / arb / divergencia
│   ├── models/
│   │   ├── football.py        Dixon-Coles
│   │   ├── cards.py           tarjetas (Poisson)
│   │   ├── corners.py         córners (binomial negativa)
│   │   ├── counts.py          pmf/cdf Poisson y binomial negativa
│   │   ├── elo.py             Elo
│   │   ├── pointmodel.py      punto→juego→set→partido
│   │   ├── basketball.py, tennis.py, mma.py, tabletennis.py
│   │   └── registry.py        multi-modelo por deporte
│   └── providers/
│       ├── sample.py          sintético (demo)
│       ├── oddsapi.py         The Odds API
│       └── manual.py          tarjetas/córners desde JSON
└── tests/                     117 tests, sin dependencias
```

---

## 13. Límites y advertencias

- **Cobertura**: The Odds API no cubre bien tarjetas/córners y tiene cobertura
  irregular en ligas menores. Para tarjetas y córners usa el proveedor
  `manual`. El sistema tolera los huecos, no los disimula.
- **Latencia**: para *in-play* real hace falta otro nivel (websockets, no REST).
  Este bot está pensado para **pre-partido**.
- **Datos de estadísticas**: los modelos Elo necesitan historial de resultados.
  Sin datos, no hay value bets. El bot es explícito cuando no tiene modelo.
- **Cuota de API**: el plan gratis de The Odds API son ~500 peticiones/mes.
  Sondea cada 5-10 minutos y pocas ligas.
- **Legal**: el uso personal está generalmente permitido; **distribuir picks de
  pago o asesorar a terceros está regulado** en muchos países. Comprueba tu
  jurisdicción antes de convertirlo en producto.
- **Juego responsable**: esto es análisis, no una garantía. Define una banca,
  un tope por apuesta y un límite de pérdida semanal. Si dejas de poder parar,
  busca ayuda.

---

## 14. Roadmap sugerido

1. **Fase 2**: modelo de totals por ritmo/eficiencia (baloncesto), props de
   jugador, y ajuste de fútbol con xG.
2. **Fase 3**: dashboard web con gráficas de movimiento y ROI acumulado.
3. **Fase 4**: registro de apuestas (*ledger*) con CLV (closing line value),
   que es la mejor métrica de si tienes edge real.
4. **Fase 5**: ingesta de lesiones y alineaciones para ajustar antes del cierre.

---

## 15. Siguiente paso

```powershell
python run.py demo
python run.py backtest --csv data/E0.csv
python run.py report
```

Cuando veas el demo funcionando, conecta tus claves de The Odds API y Telegram
en `.env` y lanza `python run.py watch`.
