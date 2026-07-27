# Аудит — Фаза 1: інвентаризація (read-only)

> Статус: **тільки читання, нічого не змінено.** Усі твердження виведені з
> коду з посиланням `файл:рядок`. Де код не дає однозначної відповіді —
> позначено «не визначено з коду». Дата зрізу: коміт `9d8c902`, гілка
> `claude/new-session-ng1s5n`.

Впевненість: **точно** = прямо видно в коді; **ймовірно** = випливає, але є
шлях, який я не проганяв; **не визначено** = з коду не встановлюється.

---

## 1.1 Карта репо

**Мови:** Python (рантайм системи), Solidity (контракти `contracts/`),
TypeScript (Hardhat: `deploy/deploy.ts`, `test-solidity/`, `hardhat.config.ts`).

**Точки входу (Python):**
| Вхід | Файл:рядок | Що робить |
|---|---|---|
| `python scripts/demo.py` | `scripts/demo.py:359` (`if __name__`) | наскрізне демо (піднімає сервер, ганяє агентів) |
| `python -m service_provider.server` |  `service_provider/server.py:258` | FastAPI-сервер продавця + реєстр |
| `python -m author.agent` | `author/agent.py` (`if __name__`) | Claude tool-use агент-покупець |
| `python -m wallets.setup_wallets` | `wallets/setup_wallets.py:98` | генерація гаманців / баланси WBT |
| `pytest tests/` | — | тести |

**Відповідальність модулів (Python):**
| Модуль | Роль | Джерело |
|---|---|---|
| `config.py` | усі налаштування з `.env` | `config.py:38-90` |
| `chain.py` | w3-провайдер + деплой/конект контрактів через Hardhat-артефакти + `send_contract_tx` | `chain.py:26,87,92,116` |
| `facilitator/whitechain_facilitator.py` | ТОНКИЙ оркестратор `identity→policy→payment→settlement→event→response` | `:112` (`verify_and_settle`) |
| `facilitator/identity.py` | ЄДИНЕ місце читання WB Soul (soulOf+IsVerified+SBT) + агрегація репутації | `:55` |
| `facilitator/reputation.py` | чиста формула score/tier | `:36` |
| `facilitator/policy.py` | allow/deny за identity+ресурсом (чиста) | `:17` |
| `facilitator/payment.py` | офчейн EIP-712/EIP-3009 валідація | `:64` |
| `facilitator/settlement.py` | релей transferWithAuthorization + форвард комісії | `:59` |
| `facilitator/capability.py` | реєстр можливостей / service discovery | `:17` |
| `facilitator/events.py` | журнал подій платіжного циклу | `:24` |
| `facilitator/store.py` | SQLite: `agent_stats` / `events` / `capabilities` | весь файл |
| `agent_client.py` | будує+підписує EIP-3009 auth; discovery; `SpendLedger` | `:70,169,85` |
| `service_provider/server.py` | FastAPI: `/photo`, `/balance`, `/registry/*` | `:122-236` |
| `author/agent.py` | Claude-агент (tool use) | весь файл |
| `wallets/setup_wallets.py` | нативний WBT (gas) | весь файл |

**Граф залежностей (внутрішні імпорти, «хто кого імпортує»):**
```
config.py            ← (усі)
chain.py             → config
facilitator/reputation.py   → (нічого внутрішнього)
facilitator/store.py        → (нічого внутрішнього)
facilitator/policy.py       → (нічого внутрішнього)
facilitator/events.py       → (нічого внутрішнього; store передається ззовні)
facilitator/capability.py   → (нічого внутрішнього; store передається ззовні)
facilitator/payment.py      → (web3/eth_account; teurc передається ззовні)
facilitator/settlement.py   → chain
facilitator/identity.py     → facilitator.reputation
facilitator/whitechain_facilitator.py → chain, config, facilitator.{events,identity,policy,reputation,payment,settlement,store}
agent_client.py      → config  (+ wallets.setup_wallets: див. нижче)
author/agent.py      → config, agent_client
service_provider/server.py → config, facilitator.capability, facilitator.whitechain_facilitator
scripts/demo.py      → config, chain, agent_client, service_provider.server, facilitator.{store,whitechain_facilitator,events}
```
Джерела: перелічені в `грепі` імпортів (див. кожен файл, рядки import угорі).

**Мертвий / невживаний код (точно, з коду):**
| Елемент | Факт | Джерело |
|---|---|---|
| `wallets/setup_wallets.py:send_wbt`, `explorer_link` | визначені, **ніде не викликаються** (лишок Фази 0, нативний WBT) | греп по репо: 0 зовнішніх викликів |
| `author/agent.py` (`AuthorAgent`) | **не використовується** ні `scripts/demo.py`, ні `tests/` — лише як окремий `python -m` | греп: demo використовує `agent_client.pay_and_fetch`, не `AuthorAgent` |
| dep `redis` | у `requirements.txt:7`, **жодного `import redis`** | греп |
| dep `pillow` | у `requirements.txt:10`, **жодного `import PIL`** у рантаймі (генерація картинок була разова) | греп |

---

## 1.2 Стек

| Факт | Значення | Джерело | Впевненість |
|---|---|---|---|
| Python | 3.11.15 (рантайм середовища) | `python3 --version` | точно (середовище) / версійних обмежень у коді немає |
| Менеджер залежностей | pip + `requirements.txt` / `requirements-dev.txt`; для контрактів npm | файли | точно |
| Версії Python-залежностей | **тільки нижні межі `>=`** (`fastapi>=0.139.0`, `web3>=7.16.0` …) | `requirements.txt:1-10` | точно |
| Lock-файл (Python) | **немає** | немає `requirements.lock`/poetry.lock | точно |
| Lock-файл (Node) | є `package-lock.json` | корінь | точно |
| Фактично встановлено | web3 7.16.0, eth-account 0.13.7, fastapi 0.139.2, starlette 1.3.1, uvicorn 0.51.0, anthropic 0.117.0, pytest 9.1.1, eth-tester 0.13.0b1, py-evm 0.12.1b1 | `pip list` | точно (це середовище, не гарантія в іншому) |

**Наслідок (факт, не оцінка):** оскільки Python-залежності лише `>=` і без
lock-файлу, `pip install` в іншому середовищі може підтягнути інші версії.

---

## 1.3 Модель грошей

**Що рухається:** ERC-20 токен **tEURC** (тестовий євро-стейблкоїн, 6
decimals) — `config.py:60-61`, контракт `contracts/tEURC.sol`. Нативний WBT —
лише gas. Внутрішніх «балансів» у Python-стані, які б рухалися, **немає** —
істина по коштах живе в контракті tEURC on-chain.

**Тип сум — розшарований (це головний факт розділу):**

| Місце | Тип | Джерело | Роль |
|---|---|---|---|
| On-chain сума в авторизації | **int (wei)** — `int(authorization["value"])` | `payment.py:89` | **авторитетна** сума руху коштів |
| Комісія / нетто | **int (wei)**, ціла арифметика `fee = value*bps//10_000`, `net = value-fee` | `settlement.py:95-96` | **авторитетний** розподіл |
| Ціни в конфізі | **float** (`0.02`, `0.10`, `1.0`) | `config.py:80-81,89` | вхід, конвертується у wei |
| Ціна → wei | `round(price_teurc * 10**decimals)` | `payment.py:117`, `agent_client.py:160` | **float→int межа** |
| `SpendLedger.spent_teurc` | **float**, накопичення `+=` | `agent_client.py:114,124` | офчейн-ліміт витрат агента |
| server `_ledger["earned_teurc"]` | **float**, накопичення `+=` | `service_provider/server.py:241` | офчейн-облік заробітку (для `/balance`) |
| Поля відповіді `amount/fee/net_..._teurc` | **float** (`wei / scale`) | `whitechain_facilitator.py:171-173` | звіт/серіалізація (X-Settlement, JSON) |
| capability `price` | **float** | `capability.py:31` | метадані реєстру |

**Місця, де тип змінюється/змішується (усі, що знайшов):**
1. `payment.py:117` — float ціна → int wei через `round()`.
2. `agent_client.py:160` — те саме на боці клієнта.
3. `whitechain_facilitator.py:171-173` — int wei → float для відповіді (`/scale`).
4. `agent_client.py:124` — накопичення float (`spent_teurc += amount_teurc`).
5. `service_provider/server.py:241` — накопичення float (`earned_teurc += net_..._teurc`).

**Ключові факти (перевірено):**
- **Рух коштів on-chain — цілочисловий (wei).** `fee+net==value` точно (ціле
  віднімання). Впевненість: **точно** (`settlement.py:95-96`).
- **Залишок від ділення комісії** (`value*bps` не кратне 10_000) через floor
  `//` дістається **нетто (сервісу)**, а не комісії: `net = value - floor(fee)`.
  Джерело: `settlement.py:95-96`. Впевненість: **точно**.
- **`round()` наразі нейтралізує float-похибку** конвертації ціни для
  сконфігурованих значень (перевірив `4.10*1e6 = 4099999.9999999995` →
  `round`→`4100000`). Тобто float-представлення ціни є, але межа
  конвертації зараз безпечна *завдяки round*. Впевненість: **ймовірно** (не
  для всіх можливих `.env`-значень доводив).
- **Decimal не використовується ніде** (`grep` по репо). Впевненість: **точно**.
- **Офчейн-накопичувачі — float** (`spent_teurc`, `earned_teurc`) → дрейф при
  багатьох операціях. Це **не** авторитетна сума, але це spend-cap-гейт і
  цифра заробітку. Впевненість: **точно** (місце), наслідок дрейфу — **ймовірно**.

---

## 1.4 Модель зберігання (`store.py`)

| Факт | Значення | Джерело |
|---|---|---|
| Що це | **SQLite** (`sqlite3`) | `store.py:27` |
| Шлях | `config.STORE_DB_PATH`, дефолт `.agentpay.db` (файл) або `:memory:` | `config.py`, `store.py:24` |
| Таблиці | `agent_stats`, `events`, `capabilities` | `store.py:36-63` |
| Переживає рестарт | файл — так; `:memory:` (тести, demo) — **ні** | `store.py:25,27` |
| Демо/тести | `:memory:` → стан губиться щоразу | `demo.py:141`, `conftest.py:100` |
| Транзакції | так, `with self._lock, self._conn:` → авто-commit контексту з'єднання | `store.py:33,85,104,118,141` |
| Атомарність запису | під одним `threading.Lock` + транзакція з'єднання | `store.py:29` |
| Інкремент лічильника | атомарний SQL `... = completed_payments + 1` під локом | `store.py:100-110` |
| Міграції | **немає**; схема через `CREATE TABLE IF NOT EXISTS` | `store.py:34-62` |

**Наслідок (факт):** один процес — запис серіалізований локом і атомарний.
Кілька **процесів** (напр. `uvicorn --workers N`) — кожен матиме своє
з'єднання; SQLite-файл сам по собі має блокування, але Python-`Lock` НЕ
міжпроцесний. Скільки воркерів реально запускається — див. 1.5.

---

## 1.5 Модель конкурентності

| Факт | Значення | Джерело | Впевненість |
|---|---|---|---|
| GET-роути | синхронні (`def`) | `server.py:148,161,172` | точно |
| POST-роути `/photo`, `/registry/register` | `async def`, але тіло — `await request.body()`, далі **синхронний** блокуючий `verify_and_settle` прямо в циклі подій | `server.py:184,131,225` | точно |
| Скільки воркерів | у коді запуску `uvicorn.run(app, ...)` **без `workers=`** → 1 процес | `server.py:262`, `demo.py:~150` (без `workers=`) | точно (для цих точок запуску) |
| Кілька воркерів передбачено? | **не визначено з коду** (README згадує як ризик, але запуск однопроцесний) | — | не визначено |
| RMW над спільним станом | (a) `store.increment_completed_payment` — атомарний під локом; (b) `SpendLedger` (JSON-файл) — RMW **без локу**; (c) `_ledger` у server — `+=` у пам'яті процесу | `store.py:100`, `agent_client.py:124`, `server.py:241` | точно |
| await між читанням і записом store | немає (store — синхронний, під локом) | `store.py` | точно |
| Два одночасні виклики над одним акаунтом | можливі на рівні HTTP; **ідемпотентність — on-chain** (nonce у tEURC), Python-перевірка `authorizationState` — до мутації, але це TOCTOU-«порада» | `payment.py:111`, контракт | ймовірно |

**Знайдені read-modify-write над спільним станом:**
1. `store.increment_completed_payment` — `store.py:100-110`. Атомарний (SQL `+1` під локом). **Безпечний у межах процесу.**
2. `SpendLedger.record` — `agent_client.py:123-125`: `read → += → write_text`, **без локу**. Клієнтський, на боці агента.
3. `_ledger["earned_teurc"] += ...` — `server.py:241`: у пам'яті, під GIL атомарність `+=` для float **не гарантована** між await-точками (хоча тут sync). Тільки в межах одного процесу.
4. Nonce-ідемпотентність: перевірка `authorizationState(from,nonce).call()` у `payment.py:111` відбувається ДО релею в `settlement.py`; між ними — вікно (TOCTOU). Захист реальний — **сам контракт** відхилить повторний nonce (revert), тож подвійного списання немає; Python-перевірка лише «швидка відмова».

---

## 1.6 Межі довіри

**Дані ззовні і від кого:**
| Вхід | Звідки | Валідація | Джерело |
|---|---|---|---|
| `POST /photo/{name}` body `{authorization, resource, resource_salt}` | будь-який мережевий клієнт | наявність полів `{from,to,value,validAfter,validBefore,nonce,v,r,s}` (не типи), розмір тіла ≤4096 | `server.py:203-213,191` |
| `POST /registry/register` body | будь-який мережевий клієнт | **без авторизації**; наявність `{id,capability_type,provider_url}` | `server.py:130-146`, `capability.py:20-33` |
| `name` у шляху `/photo/{name}` | клієнт | whitelist `^[A-Za-z0-9_-]+$` (анти-traversal) | `server.py:~55` |
| `.env` | оператор | typed-геттери, без секретів у git (`.gitignore`) | `config.py:21-35` |

**Що робить `identity.py`:** читає **тільки on-chain** WB Soul
(`soulOf`, `soulAttributeValue(IsVerified)`, `tokensCountBySoul`) —
`identity.py:62,73,78`. **Підписів/токенів/ключів не перевіряє** (це не його
робота). Впевненість: **точно**.

**Де перевіряється підпис платежу:** `payment.py:91-99` —
`Account.recover_message(...) == from`; плюс часове вікно (`:103-107`),
on-chain nonce (`:111`), отримувач `to == FACILITATOR_WALLET_ADDRESS`
(`:115`), сума `value >= price_wei` (`:117-118`).

**Хто ініціює платіж і сеттлмент, чим обмежено:**
- Платіж авторизує **платник** офчейн-підписом EIP-712 (прив'язка до ресурсу
  через `nonce = keccak256(resource||salt)`), `agent_client.py:157-160`.
- Релей у мережу робить **facilitator** ключем `FACILITATOR_WALLET_PRIVATE_KEY`
  (`settlement.py:73`, `config.py:70`). Тобто транзакцію на ланцюг подає
  facilitator, платить за неї gas.
- Обмеження, що фактично є в коді: підпис має відновитися в `from`; `to`
  мусить дорівнювати адресі facilitator; сума ≥ ціни; nonce не використаний.
  **Немає** перевірки, що викликач HTTP = платник (будь-хто може пред'явити
  чужу валідну авторизацію — але вона однаково прив'язана до ресурсу і
  списує рівно `value` на facilitator). **Немає** авторизації на
  `/registry/register`.

---

## 1.7 Інваріанти (виведені з коду)

| # | Інваріант | Гарантія | Джерело |
|---|---|---|---|
| I1 | Одна авторизація `(payer, nonce)` сеттлиться щонайбільше раз | **явна (on-chain)** — tEURC.authorizationState + revert при повторі; Python-перевірка `payment.py:111` — неявна/порадна | контракт + `payment.py:111` |
| I2 | `fee_wei + net_wei == value` (нічого не «зникає» при розподілі) | **явна** — ціле віднімання | `settlement.py:95-96` |
| I3 | Кошти йдуть на адресу facilitator | **явна** — перевірка `to == FACILITATOR_WALLET_ADDRESS` | `payment.py:115` |
| I4 | Сплачено не менше ціни ресурсу | **явна** — `value >= round(price*10**dec)` | `payment.py:117-118` |
| I5 | Доступ до ресурсу видається лише після підтвердження релею (при `WAIT_FOR_CONFIRMATION=true`) | **явна** — settle чекає receipt перед поверненням; AccessGranted після | `settlement.py:90-93`, `whitechain_facilitator.py:154-160` |
| I6 | Форвард сервісу ≤ зібраного | **неявна** — `net_wei = value - fee_wei < value`, але форвард — **окрема транзакція** після отримання `value`; при частковому збої релей пройшов, форвард ні → інваріант «сервіс отримав свою частку» **не** гарантований | `settlement.py:98-114` |

**Порушувані шляхи (попередньо, деталі в 1.9):** I5 не тримається при
`WAIT_FOR_CONFIRMATION=false` (доступ на broadcast). I6 не тримається при
частковому збої (relay ok, forward revert) — платник списаний, сервіс без
коштів, компенсації немає.

---

## 1.8 Тести

| Факт | Значення | Джерело |
|---|---|---|
| Каркас | pytest; фікстура на локальному EthereumTesterProvider | `tests/conftest.py` |
| Проходять зараз | **так, 36 passed** (щойно прогнав) | `pytest tests/ -q` |
| Solidity-тести | 14 (окремо, `npx hardhat test`) — у цю Фазу не переганяв повторно | `test-solidity/` |
| Файли тестів | `test_facilitator, test_reputation, test_policy, test_capability, test_identity, test_events` | `tests/` |

**Покриті:** формула репутації (юніт), policy (юніт), capability
resolve/list (юніт), identity (verified/no-soul/no-sbt/behavioral),
event-race (доступ лише після SettlementConfirmed), payment-гілки KYA/
reputation/replay/binding/underpayment/forged/expired.

**НЕ покриті взагалі (з коду видно відсутність тестів):**
| Модуль/аспект | Стан |
|---|---|
| `store.py` | **немає прямого юніт-тесту** (тестується опосередковано) |
| `settlement.py` частковий збій (relay ok / forward revert) | **не покрито** |
| Конкурентність (два одночасні `verify_and_settle`) | **не покрито** |
| `SpendLedger` (float, RMW, ліміт-межі) | **не покрито** (клієнтський) |
| `agent_client.pay_and_fetch` HTTP-гілки | покрито лише опосередковано у demo, прямого юніта немає |
| `chain.py`, `wallets/setup_wallets.py` | без юніт-тестів |
| `author/agent.py` | без тестів |
| межові суми (0, від'ємні, > типу) | **не покрито** |

---

## 1.9 ПОПЕРЕДНІЙ список того, що вже виглядає як CRITICAL/MAJOR (без виправлень)

> Це попередня оцінка з інвентаризації. Жодних змін не внесено. Точна
> класифікація і патчі — Фаза 2 після твого «go».

- **[preCRITICAL, потребує підтвердження моделі загроз] `POST /registry/register` без авторизації → отруєння discovery.**
  Будь-хто реєструє можливість того ж `capability_type`. `resolve()` повертає
  `matches[0]` за `ORDER BY id` (`capability.py:38`, `store.py`) → атакер із
  малим `id` виграє і підміняє `provider_url`. Агент, що довіряє discovery,
  піде на сервер атакера; той віддає 402 зі своїм `payTo`, агент підписує
  авторизацію на **адресу атакера** (перевірка `to==facilitator` у нашому
  payment.py тут не діє — платіж релеїть чужий facilitator). Наслідок:
  перенаправлення коштів. Джерело: `server.py:130-146`, `capability.py:36-40`.
  *Умовно* на тому, що агент бере `payTo` з discovery без окремої перевірки —
  так і є в `agent_client.pay_and_fetch` (`agent_client.py:226-238`).

- **[preMAJOR] Частковий збій сеттлменту (A3).** `settlement.settle`: релей
  `transferWithAuthorization` (кошти → facilitator) і форвард нетто —
  **дві окремі транзакції** (`settlement.py:73,100`). Якщо релей замайнено, а
  форвард ревертнув (напр. брак WBT-gas у facilitator), при
  `WAIT_FOR_CONFIRMATION=true` кидається `SettlementError` **після** списання
  платника → оркестратор повертає 402 (`whitechain_facilitator.py:150`),
  контенту немає, кошти застрягли у facilitator, **компенсації/реконсиляції
  немає**, у store немає журналу для відновлення. Джерело: `settlement.py:98-114`.

- **[preMAJOR/MINOR] float в офчейн-обліку коштів (A1).** `SpendLedger.spent_teurc`
  (`agent_client.py:124`) і `_ledger["earned_teurc"]` (`server.py:241`) —
  накопичення `float +=`. Це не авторитетна сума (on-chain — int), але це
  spend-cap-гейт і цифра заробітку → накопичувальний дрейф. Джерело вище.

- **[preMINOR, задокументовано] `WAIT_FOR_CONFIRMATION=false` віддає доступ на
  broadcast** (I5 не тримається) — свідомий trade-off у README, але це шлях,
  де контент виданий до підтвердження оплати. `settlement.py:88-93`.

- **[інформативно, НЕ баг] Ідемпотентність tEURC-nonce — коректна (on-chain).**
  TOCTOU між `authorizationState`-перевіркою і релеєм існує, але подвійного
  списання немає, бо контракт ревертить повторний nonce. `payment.py:111`.

---

## ПИТАННЯ ДО АВТОРА (без відповідей аудит буде вгадуванням; ≤10)

1. **Модель розгортання:** один процес чи `uvicorn --workers N`/кілька
   інстансів? Від цього залежить, чи Python-`Lock` у `store.py` достатній, чи
   потрібна міжпроцесна атомарність. (У коді запуск однопроцесний — 1.5.)
2. **Довіра до discovery:** чи має агент довіряти `provider_url`/`payTo` з
   `/registry/capabilities` без окремої перевірки? Хто має право писати в
   реєстр (`/registry/register` зараз без авторизації)?
3. **Частковий збій сеттлменту (relay ok, forward revert):** яка бажана
   поведінка — відкат неможливий (кошти вже у facilitator), тож ретрай
   форварду? реконсиляція з журналу? Це визначає фікс A3.
4. **Гроші як тип:** чи прийнятно лишати ціни/облік у float (на межі round),
   чи перейти на int-wei/Decimal усюди, включно зі `SpendLedger` і
   `_ledger`? (On-chain уже int.)
5. **Overpayment:** `value > price` приймається і сеттлиться повністю. Це
   бажано (чайові/буфер) чи треба відхиляти/повертати різницю?
6. **Джерело поведінкової статистики для репутації** (`agent_stats`)
   оновлюється лише локально (`increment_completed_payment`) — чи це прийнятно
   для гейту, знаючи, що воно sybil-able (self-dealing накручує `completed`)?
7. **`WAIT_FOR_CONFIRMATION=false`:** лишається як легітимний продакшн-режим
   чи тільки локальне demo? Якщо перше — потрібен реконсилятор невдалих релеїв.
8. **Мертвий код:** `wallets/setup_wallets.send_wbt`/`explorer_link`,
   `author/agent.py`, deps `redis`/`pillow` — видаляти чи лишити свідомо?
9. **Пін залежностей:** чи потрібен lock-файл / точні версії для Python
   (зараз лише `>=`)?
10. **Порядок модулів Фази 2:** ти назвав `store→payment→settlement→identity→
    reputation`. Підтверджуєш? (Я б технічно поставив `settlement` перед
    `payment` за грошовою вагою, але піду за твоїм порядком.)

---

## Стоп

Фаза 1 завершена, нічого не змінено. Чекаю на підтвердження, перш ніж
переходити до Фази 2 (аудит + виправлення CRITICAL/MAJOR з тестами).
