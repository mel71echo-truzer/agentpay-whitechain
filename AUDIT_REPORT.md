# AUDIT_REPORT.md — аудит AgentPay on Whitechain

> Рев'ю: старший інженер-рев'юер. Гілка `audit/cleanup` від `main` (`a8d9c8e`).
> Області: коректність (грошові шляхи), безпека, чистота, узгодженість,
> документація vs код, тести. Безпечні фікси застосовані окремими комітами;
> ризиковані/дизайнерські — у розділі «Пропоную, не застосував».

## Прогін тестів: до і після

| Набір | До аудиту | Після аудиту |
|---|---|---|
| `npx hardhat test` | **14 passing, 1 FAILING** (`AgentPayRouter.test.ts` не запускався) | **15 passing, 0 failing** |
| `python -m pytest tests/` | **105 passed** | **106 passed** (+1 регресійний тест на ledger-шлях) |
| `python scripts/demo.py` (local) | зелений | зелений (9/9 кроків, exit 0) |

Секрети: `.env` у `.gitignore`, не в дереві й **не в git-історії**; `.env.example`
**не містить** справжніх приватних ключів (перевірено грепом 64-hex). Чисто.

---

## Знахідки за рівнями

### CRITICAL

**C-1. `AgentPayRouter.settlePaymentAtomic` — крадіжка через непідписаний `seller` + відсутній контроль доступу. [ЗАКРИТО]**
`contracts/AgentPayRouter.sol`. Стара версія: `external` без контролю доступу;
`seller` НЕ входив у підписане EIP-3009 повідомлення (підпис покривав
`to=router`, не `seller`). Будь-хто, хто бачив валідну авторизацію (тіло HTTP,
мемпул), викликав з власним `seller`, забирав `sellerAmount` і спалював nonce.

**Фікс (гілка `router-seller-binding`, коміт `df36f89`):**
- **nonce виводиться on-chain** як `keccak256(abi.encode(seller, feeBps,
  resourceHash))` і подається в `receiveWithAuthorization`. Зміна отримувача,
  split-у чи ресурсу змінює nonce; покупець підписав оригінальний nonce, тож
  ECDSA-recover у tEURC більше не дає `from` — токен ревертить. Релеєр не може
  підробити підпис.
- **сума** звʼязана як підписаний `value`; **часове вікно** підписане; **asset** —
  immutable `teurcToken`.
- **feeBps** — per-payment, звʼязаний у nonce, з жорстким капом `MAX_FEE_BPS`
  (10%). Немає мутабельного глобального fee → немає другого джерела істини.
- **контроль доступу:** allowlist релеєрів (owner-керований, facilitator-relayed
  модель); навіть дозволений релеєр не може перенаправити кошти (призначення в
  nonce).
- **SafeERC20** на переказах fee/seller (перевіряє return); залишок floor-fee
  дістається seller (як `settlement.py`). **ReentrancyGuard** на settle.

**Тести (`npx hardhat test`, 23 passing):** happy-path, floor-залишок→seller,
атака №1 (підміна seller)→revert, №2a/№2b/№2c (роздути суму/комісію)→revert,
№3 (не-релеєр)→revert, replay→revert, KYA-гейт. Кожна атака **проходить (крадіжка)
на старому контракті** (продемонстровано підміною контракту й old-signature
експлойтом: attacker забирає 995000) і **ревертить на новому**.

**Статус підключення:** роутер і далі **НЕ в живому платіжному шляху** —
roadmap-артефакт (шапка «ROADMAP ARTIFACT — NOT WIRED»), `chain.py` його не
деплоїть, `verify_and_settle` не викликає; живий шлях — `SettlementEngine`
(custody→forward), без змін. Дірки в публічному репо більше немає; підключення —
окрема задача (клієнтська інтеграція nonce-схеми в `agent_client.py`).

### HIGH

**H-1. `agent_client.py:322` — `NameError: price_teurc` у шляху `pay_and_fetch(ledger=...)`. [ВИПРАВЛЕНО]**
`ledger.record(price_teurc, …)` посилався на неіснуючу змінну (залишок
money-міграції; поруч правильна `price_wei`). Крах на КОЖНІЙ покупці, коли
передано `SpendLedger` — а це робить `author/agent.py`. Вижило, бо жоден тест не
зачіпав ledger-шлях, а `author/agent.py` потребує живого Claude-ключа.
Фікс: `price_wei`; додано `tests/test_agent_client_ledger.py` (падав з NameError
до, проходить після). Коміт `c3f68c8`.

**H-2. `npx hardhat test` був ЧЕРВОНИЙ — `AgentPayRouter.test.ts` не запускався. [ВИПРАВЛЕНО]**
`test-solidity/AgentPayRouter.test.ts:17` деплоїв `MockSoulRegistry` без
аргументів (конструктор вимагає 2) → `beforeEach` кидав «incorrect number of
arguments», єдиний тест роутера ніколи не виконувався (хибна впевненість). CI не
ганяє `hardhat test` (лише compile + pytest), тож ніхто не помітив.
Фікс: деплой `MockSoulAttribute`+`MockSoulBoundTokenCollection` перед реєстром.
Коміт `7c64de2`.

### MEDIUM

**M-1. `scripts/demo.py` — `KeyError: 'held_count'` на testnet-шляху. [ВИПРАВЛЕНО]**
`scripts/demo.py` (підсумковий блок). На `NETWORK=whitechain_testnet`
`check_testnet_config()` не ставить `ADMIN_API_TOKEN` (лише local-шлях ставив),
тож demo шле порожній Bearer, отримує 403 і падає на `held["held_count"]`.
Фікс: слати заголовок лише за наявності токена; на не-200 показувати «н/д»
замість індексації. Коміт `ce5aa5a`.

**M-2. `ADMIN_API_TOKEN` не задокументований у `.env.example`. [ВИПРАВЛЕНО]**
`config.py:93` читає `ADMIN_API_TOKEN`, але його не було в `.env.example` —
оператор не знав, що `/admin/*` існує і що порожній токен = вимкнено (не
відкрито). Додано з поясненням. Коміт `22faa7f`.

### LOW

**L-1. Невживані імпорти `identity_mod`, `reputation_mod`. [ВИПРАВЛЕНО]**
`facilitator/whitechain_facilitator.py:36,38` — імпортовані, ніде не вжиті
(pyflakes). Видалено. Коміт `1c6aeac`.

**L-2. Невживана змінна `vet_res`. [ВИПРАВЛЕНО]** `scripts/demo.py` (крок 10). Коміт `ce5aa5a`.

**L-3. Застаріла troubleshooting-нотатка про cancun→shanghai. [ВИПРАВЛЕНО]**
`DEPLOY_WHITECHAIN.md` радив «якщо Whitechain не підтримує Cancun — знизь до
shanghai», але `TESTNET_DEPLOYMENT.md` підтверджує, що cancun працює на реальному
testnet без fallback. Переформульовано в оборонну нотатку. Коміт `22faa7f`.

**L-4. Застарілий docstring у `wallets/setup_wallets.py`. [ВИПРАВЛЕНО]**
Стверджував «вміє відправляти WBT», але `send_wbt()` видалено. Коміт `22faa7f`.

### NIT

**N-1. `hardhat.config.ts:13-20` — коментар «egress-політика цієї сесії блокує
whitechain.io / немає мережевого доступу».** Після реального деплою на testnet це
історичний контекст, що може заплутати нового читача коду. Не критично;
**не чіпав** (див. «Пропоную»).

**N-2. `README.md:178 «Step 0»`** — описує обмеження давньої dev-сесії (egress).
Статус-блок README вже коректно каже «now also deployed on testnet», тож
суперечності немає; Step 0 лишається як історичний контекст. Прийнятно.

---

## Короткий безпековий огляд НОВОЇ поверхні Фази 1

(SECURITY_REVIEW.md покриває лише Фазу 0 — нативний WBT. Нижче — нова поверхня.)

| Аспект | Стан | Джерело |
|---|---|---|
| **EIP-3009 relay** — підпис recover у `from`, вікно validAfter/validBefore, on-chain nonce anti-replay, `to==facilitator`, точна сума | **OK** | `facilitator/payment.py:100-146` |
| **Resource-binding** — `nonce = keccak(resource‖salt)`, звірка на сервері | **OK** | `payment.py`, `agent_client.py` |
| **KYA-гейт** — `soulOf != 0` + IsVerified-атрибут, on-chain | **OK** | `facilitator/identity.py` |
| **Reputation/tier** — явна формула, cold-start guard (`max(attested, behavioral)`) | **OK, з відомою межею** (sybil — див. нижче) | `facilitator/reputation.py`, `identity.py` |
| **Overpayment** — `value > price` відхиляється (немає шляху повернення) | **OK** | `payment.py:131-146` |
| **Int-wei арифметика** — жодного float у грошових шляхах; залишок floor→сервісу | **OK** | `money.py`, `settlement.py:95-96` |
| **Fee-forwarding custody** — `fee+net==value` точно; частковий збій → журнал + стан «кошти утримані» + `/admin/held-settlements` | **OK для custody-моделі**; атомарність — Phase 2.5 (роутер) | `settlement.py`, `whitechain_facilitator.py` |
| **Path traversal** — whitelist `^[A-Za-z0-9_-]+$` з `fullmatch` | **OK** | `server.py:53,129` |
| **Ніколи 500 на недовірений вхід** — try/except → 400 | **OK** | `server.py:163,253,285` |
| **/admin/* автентифікація** — порожній токен = 403 (вимкнено), інакше Bearer + `hmac.compare_digest`, токен не логується; `/balance` деталь під тим самим токеном | **OK** | `server.py:168-178,225` |
| **Реєстр** — підписана реєстрація, `id==підписант`, payTo прив'язаний, агент звіряє payTo (hard-fail) | **OK** | `registry_auth.py`, `capability.py`, `agent_client.py` |
| **Приватні ключі лише з .env; bind 127.0.0.1** | **OK** | `config.py:66-73,90` |

**Висновок огляду:** жодної НОВОЇ активної діри в живому платіжному шляху Фази 1
не знайдено. Відомі межі (нижче) задокументовані свідомо.

---

## Пропоную, але НЕ застосував (потрібне твоє рішення)

1. **C-1 (роутер, CRITICAL) — ЗАКРИТО** (`df36f89`, гілка `router-seller-binding`):
   seller/сума/split/ресурс/вікно звʼязані підписом через nonce, allowlist
   релеєрів, SafeERC20, ReentrancyGuard, 23 Solidity-тести. Див. секцію CRITICAL
   вище. Лишається окремою задачею **підключення** (клієнтська nonce-схема).
2. **Fee bps — ЗАКРИТО у роутері.** Стара знахідка була про дублювання
   `facilitatorFeeBps` (контракт) vs `FACILITATOR_FEE_BPS` (config). Новий роутер
   не має мутабельного глобального fee взагалі — `feeBps` per-payment і звʼязаний
   у nonce, тож другого джерела істини немає. (Custody-шлях і далі читає bps із
   config — це його власна модель, окремо.)
3. **`transfer()` ігнорує return — ЗАКРИТО.** Новий роутер використовує SafeERC20
   (`safeTransfer`) на переказах fee/seller.
4. **Sybil-репутація (F7)** — `completed_payments` інкрементується локально;
   self-dealing накручує behavioral-tier. Задокументовано в
   `docs/audit/03-reputation-threat-model.md`; довіра тримається on-chain шаром
   (KYA+SBT). Потрібне дизайн-рішення (крос-контрагентність / on-chain провенанс /
   стейк). *Не механічна чистка.*
5. **`hardhat.config.ts` egress-коментар (N-1)** — косметика; можу пом'якшити
   формулювання, якщо хочеш. Лишив як є, щоб не чіпати конфіг без потреби.

---

## Підсумок

**Що змінив (7 комітів у `audit/cleanup`):** виправив 1 реальний баг (H-1,
NameError у ledger-шляху, з регресійним тестом), розчервонений Solidity-набір
(H-2), крихкий testnet-demo (M-1), задокументував `ADMIN_API_TOKEN` (M-2),
прибрав мертві імпорти/змінну (L-1/L-2), оновив застарілі доки (L-3/L-4).

**Що лишив на твоє рішення:** роутерну CRITICAL і супутні (fee-bps, SafeERC20) —
до окремої задачі підключення; sybil-репутацію — дизайн; косметику конфігу.

**Готовність до публічного показу (GitHub/грант):** **висока для PoC.** Живий
платіжний шлях (EIP-3009 relay + KYA/reputation-гейт + custody-forward)
коректний, покритий тестами (106 pytest + 23 Solidity, усі зелені), задеплоєний
і прогнаний на реальному Whitechain testnet, з чесно задокументованими межами.
Головний застережний прапорець для рецензента — **не сплутати roadmap-роутер із
живим шляхом**: C-1 у ньому вже закрито (див. секцію CRITICAL), і він явно позначений як непідключений.
У публічному репо не лишилось
контракту з відомою критичною дірою — навіть як roadmap-артефакт.
