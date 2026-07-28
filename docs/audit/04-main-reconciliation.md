# Розходження гілки з `main` — аналіз і пропозиція (без злиття)

> Статус: **лише аналіз.** Нічого не злито, `main` не чіпано. Доля
> AgentPayRouter — дизайнерська розвилка, лишається за автором. Цей документ
> дає факти (перевірені командами) і 3 варіанти з чесними наслідками.
>
> Зріз: гілка `claude/new-session-ng1s5n` @ `03fff36`, `main` @ `64c7354`,
> спільний предок `9d8c902`.

---

## 2.1 Факти — підтверджено/спростовано (командами, з виводом)

| # | Твердження | Вердикт | Доказ |
|---|---|---|---|
| 1 | `main` зламаний: `import facilitator.whitechain_facilitator` → ImportError | **ПІДТВЕРДЖЕНО** | у worktree `origin/main`: `ImportError: cannot import name 'SettlementEngine' from 'facilitator.settlement'` |
| 2 | Причина: `a53b097` замінив settlement.py на `SettlementService`, а `whitechain_facilitator.py`/`tests/test_events.py` досі імпортують `SettlementEngine`/`SettlementError` | **ПІДТВЕРДЖЕНО** | `main:settlement.py` має лише `class SettlementService` (`settle_atomic_payment`); **не** визначає ні `SettlementEngine`, ні `SettlementError`. `main:whitechain_facilitator.py:41` = `from facilitator.settlement import SettlementEngine, SettlementError`. `main:tests/test_events.py:15` = `from facilitator.settlement import SettlementError` |
| 3 | `SettlementService` на main нічим не імпортується (осиротілий) | **ПІДТВЕРДЖЕНО** | `git grep SettlementService origin/main -- '*.py'` → єдиний хіт = сама його дефініція |
| 4 | На main НЕМАЄ: `money.py`, `registry_auth.py`, усіх `docs/audit/*`, 8 тестових файлів аудиту | **ПІДТВЕРДЖЕНО** | `git cat-file -e`: `money.py`/`registry_auth.py` — branch=yes/main=NO. Відсутні на main: `docs/audit/00..04`, `tests/test_{money_scaling,payment_overpayment,price_scaling_audit,registry_signed,server_routes,settlement_partial_failure,store_concurrency,store_schema_version,store_single_process}.py` (насправді **9** аудит-тестів, не 8) |
| 5 | На main Є (і немає в гілці): `contracts/AgentPayRouter.sol`, `test-solidity/AgentPayRouter.test.ts`, `pyproject.toml`, `agentpay/__init__.py` | **ПІДТВЕРДЖЕНО** | усі чотири: branch=NO/main=yes |
| 6 | `main` — гілка за замовчуванням (її бачить кожен, хто відкриває репозиторій) | **не перевіряв з CLI** (потребує GitHub API) | приймаю як факт автора; наслідок: зламаний default-бранч = зламане перше враження |

**Уточнення до №4:** аудит-тестів **дев'ять**, не вісім (додатково
`tests/test_price_scaling_audit.py`). Дрібниця, але «перевір сам, не вір на
слово» — тому фіксую.

### Окремо: pyproject.toml на main vs requirements-підхід гілки — ДВІ системи залежностей (реальна проблема)

`main` має **одночасно** `pyproject.toml` **і** `requirements.txt`. Це не просто
зайвий файл — це дві різні моделі й два різні набори версій:

| | Гілка (`requirements.txt` + `.lock` + `-dev`) | main `pyproject.toml` (`agentpay-sdk` 0.2.0-alpha) |
|---|---|---|
| Модель | застосунок, що запускається | бібліотека/SDK для дистрибуції (`agentpay/` пакет) |
| `web3` | `>=7.16.0` (і запінено в lock) | `>=6.0.0` |
| Python | 3.11+ (DEPLOY доки; CI-підлога 3.11) | `requires-python = ">=3.10"` |
| Ще | fastapi/uvicorn/anthropic/rich/eth-tester… | + `pydantic`, без fastapi/anthropic |

**Проблема:** злиття «в лоб» дасть суперечливі декларації залежностей (різні
підлоги `web3` і Python, різні набори), і незрозуміло, що є істиною —
`pip install -r requirements.txt` чи `pip install .` з pyproject. Це **окрема
розвилка** (app vs SDK-пакет), яку теж треба вирішити свідомо, а не змерджити
мовчки.

---

## 2.2 AgentPayRouter.sol по суті — робочий контракт чи заготовка?

**Це РОБОЧИЙ, зв'язний контракт, не заглушка** — і саме той «атомарний router»,
який docstring `settlement.py` у гілці називає напрямком Фази 2.5:

- `settlePaymentAtomic(from, seller, amount, validAfter, validBefore, nonce, v, r, s)`:
  1. KYA on-chain: `ISoulRegistry.soulOf(from)` + `isVerified` → `revert KYACheckFailed`;
  2. `fee = amount*feeBps/10000` (floor), `sellerAmount = amount - fee` — **той самий
     залишок-сервісу**, що й у гілці (`net = value - floor(fee)`);
  3. `receiveWithAuthorization(from, address(this), amount, …)` — стягує кошти в router;
  4. `transfer(owner, fee)` + `transfer(seller, sellerAmount)`;
  5. подія `AtomicPaymentSettled`.
- `setFeeBps` (onlyOwner, cap ≤1000 bps).

**Головна перевага:** усе в ОДНІЙ транзакції → **атомарно**. Це нативно закриває
**F3 (частковий збій)**, задля якого в гілці зроблено офчейн «журнал + утримані
кошти + звірка». Тобто main — не сміття, а незавершений наступний крок.

### Чи покриває тест щось реальне?

**Майже ні — один revert-кейс.** `AgentPayRouter.test.ts` має єдиний `it(...)`:
покупець без Soul → `KYACheckFailed`. Він **не** перевіряє happy-path (реальний
EIP-3009-підпис, фінансування токеном, стягнення `receiveWithAuthorization`,
спліт fee/seller, виплату). Тобто доведено лише «контракт компілюється і KYA-гейт
ревертить»; ядро атомарного розрахунку — не покрито.

### Чи узгоджений інтерфейс `settlePaymentAtomic` з тим, що формує гілка?

**Частково — і ключове НЕ збігається.**

| Аспект | Гілка (payment.py / agent_client.py) | Router (main) | Збіг? |
|---|---|---|---|
| EIP-3009 варіант | підписує **TransferWithAuthorization** (`TRANSFER_…_TYPEHASH`) | кличе **receiveWithAuthorization** (`RECEIVE_…_TYPEHASH`) | **НІ — різний typehash → різний підпис** |
| `to` у підписі | `to == FACILITATOR_WALLET_ADDRESS` (перевірка в payment.py) | `to == address(router)` (вимога `receiveWithAuthorization`: msg.sender==to) | **НІ** |
| Порядок/типи полів | from, to, value, validAfter, validBefore, nonce(bytes32), v(uint8), r/s(bytes32) | from, seller, amount, validAfter, validBefore, nonce(bytes32), v(uint8), r/s(bytes32) | **ТАК** (порядок і типи), nonce bytes32 ✓, v/r/s ✓ |
| `seller` | прив'язаний офчейн; nonce = keccak(resource‖salt) (M-1 resource-binding) | `seller` — окремий аргумент, НЕ в підписі; router не знає про ресурси | частково (nonce проходить, але resource-binding — поза router) |
| Гейт | KYA **+ reputation tiers + SBT + premium** | KYA **бінарний** (verified/revert), без репутації | **НІ** (router — простіший гейт) |
| Токен підтримує? | tEURC має обидва варіанти | `main:tEURC.sol` реалізує і transfer-, і **receiveWithAuthorization** | ТАК (токен готовий) |

**Висновок 2.2:** контракт реальний і архітектурно кращий для F3, але **не
drop-in**: щоб його підключити, гілці треба (а) підписувати `ReceiveWithAuthorization`
з `to=router` замість `Transfer…`+facilitator; (б) переписати перевірку `to`
в payment.py; (в) вирішити, де лишається reputation/SBT-гейт (router його не
робить — має лишитися офчейн ПЕРЕД викликом router); (г) написати справжні
happy-path тести (зараз лише KYA-revert). Плюс задеплоїти router у пайплайн.

---

## 2.3 Варіанти дій (чесні наслідки)

### Варіант A — гілка = істина для Python; router як явний roadmap-артефакт (рекомендую)
Зробити **гілку базою** (протестована, 105 зелених, працює). Перенести з main
`AgentPayRouter.sol` + його тест у гілку **як явно позначений roadmap, НЕ
підключений** (коментар «Phase 2.5 target, not wired; див. 04-main-reconciliation»).
Живим лишається `SettlementEngine` (протестований off-chain relay). Долю
`pyproject.toml`/`agentpay`-SDK вирішити окремо (не тягнути мовчки).
- **Що ламається:** нічого в робочій системі (router не підключений).
- **Що втрачається:** з гілки — нічого. З main — осиротілий `SettlementService`
  (клієнт router) або відкидається, або переноситься поруч із .sol як roadmap.
- **Обсяг:** малий — 2 файли + roadmap-коментар + реєстрація контракту в hardhat +
  рішення по pyproject.
- **main робочий одразу після злиття:** **ТАК** (main стає = гілка + roadmap-файли;
  зламаний імпорт зникає, бо на гілці `SettlementEngine` існує й тестується).

### Варіант B — прийняти атомарний router як бойовий шлях зараз (доробити Фазу 2.5)
Переписати підпис на `ReceiveWithAuthorization`(to=router), змінити валідацію
`to` в payment.py, лишити reputation-гейт офчейн перед router, зробити
`SettlementService` живим, задеплоїти router, написати справжні Solidity+Python
тести, пере-аудитувати.
- **Що ламається:** уся гілкова гілка settlement/payment/частина тестів
  (SettlementEngine, transfer-варіант, F3-логіка утримання стають зайвими).
- **Що втрачається:** офчейн-звірка F3 стає непотрібною (це добре), але її
  тести/доки під переробку; протестований relay-шлях заміняється менш
  протестованим (зараз лише KYA-revert покрито).
- **Обсяг:** **великий** — нове підписання, нова валідація, деплой, справжні
  тести, повторний аудит.
- **main робочий одразу після злиття:** **НІ** — лише після всієї цієї роботи;
  високий ризик; не «зелений з першого разу».

### Варіант C — обидва шляхи за прапорцем (relay за замовчуванням, router opt-in)
Лишити `SettlementEngine` дефолтом; додати `SettlementService`(router) як
альтернативу за `SETTLEMENT_MODE=relay|router`; підписання — умовне (Transfer або
Receive залежно від режиму).
- **Що ламається:** нічого за замовчуванням (relay лишається).
- **Що втрачається:** нічого; але +складність (два шляхи, умовне підписання —
  найважча частина).
- **Обсяг:** середньо-великий.
- **main робочий одразу після злиття:** **ТАК** (дефолт=relay).

---

## Рекомендація (не рішення)

**Варіант A** — найменший ризик до робочого `main` і зберігає протестований шлях,
не ховаючи розвилку: router їде як **позначений roadmap**, а «доробити атомарний
router» (Варіант B) лишається окремою свідомою задачею. Розвилку `pyproject`/SDK
теж винести окремим рішенням. Але вибір B (прийняти router) — легітимний
дизайнерський крок, і він **за автором**, не за мною.

## Що зроблено і що НІ
- **Зроблено:** аналіз, перевірка фактів, цей документ.
- **НЕ зроблено (свідомо, за забороною):** нічого не злито, `main` не чіпано,
  `SettlementEngine` не дописано поруч із `SettlementService`, історію не
  переписано, у `main` не пушено. Read-only worktree `origin/main`
  використано лише для відтворення ImportError і видалено після аналізу.
