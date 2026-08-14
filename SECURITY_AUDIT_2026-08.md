# Security & Production-Readiness Audit — agentpay-whitechain

**Date:** 2026-08-14 · **Branch:** `main` (после злиття атомарного роутера, merge `c28d41e`)
**Scope:** contracts/ (Solidity/Hardhat) · facilitator/ + service_provider/ + agent_client.py (Python/FastAPI/web3.py) · store (SQLite) · keys/secrets · reliability
**Method:** read-only. Кожна знахідка має `файл:рядок`. Кожне «missing» доведене grep-ом. CRITICAL/HIGH перевірені другим проходом. **Нічого не змінено.**

Дві планки оцінюються **окремо** для кожної знахідки:
**(A)** проблема для поточного testnet-демо (single-operator, single-process, без реальних коштів);
**(B)** що треба, перш ніж через це пройдуть реальні гроші.

---

## Резюме (пів сторінки)

**Планка A (testnet-демо): ЗДОРОВО.** Криптографічне ядро — коректне і покрите тестами. Атака C-1 (підміна отримувача) справді закрита: `AgentPayRouter.settlePaymentAtomic` вшиває `seller+feeBps+resourceHash` у EIP-3009 nonce, і tEURC відновлює підпис саме над ним — будь-яка підміна продавця/суми/комісії/ресурсу ламає підпис (доведено тестами revert). Офчейн-валідатор (`payment.py`) точно дзеркалить контракт через спільний `router_binding.py` (RECEIVE typehash, `to==router`, той самий похідний nonce, точна сума, anti-replay через on-chain стан). Комісія **реально** йде on-chain на `owner()` роутера (`_split` → `safeTransfer(owner(), feeAmount)`, `AgentPayRouter.sol:173`) — попередні критики, що казали «немає ончейн-комісії», **помилялись**. Реентрансі закрито (`nonReentrant` + токен-рівневий CEI: nonce позначається used до `_transfer`). Секретів у git-історії/`.env.example` немає (доведено grep-ом). Тести зелені: **23 hardhat + 114 pytest**. **Жодного CRITICAL чи HIGH-рівня [VULN] не знайдено.**

**Планка B (реальні гроші): не готово — і це очікувано для гранту.** Основне — не діри в логіці, а операційне загартування й свідомо відставлені межі: (1) немає rate-limiting/DoS-захисту на FastAPI (доведено); (2) немає таймауту на RPC-читання — повільна нода блокує воркер; (3) ончейн-KYA-гейт роутера на testnet — заглушка (`MockRouterKYA` з **неаутентифікованими** сеттерами), справжній гейт наразі офчейн (policy) + allowlist релеєрів; (4) single-process store, plaintext-ключі в `.env`, legacy-режим із вікном «funds-held», sybil-able репутація — усе задокументоване як scoped. Плюс одна дрібна справжня вада: текст винятку розрахунку витікає клієнту (`whitechain_facilitator.py:205`). Перед реальними коштами обов'язковий незалежний зовнішній аудит саме живого EIP-3009/KYA-шляху (наявний `SECURITY_REVIEW.md` покриває лише Phase 0).

**Найгостріше зверху:** нічого, що блокувало б демо. Для B — F-02 (rate-limit) і F-04 (реальний ончейн-KYA) першочергові.

---

## Таблиця знахідок

| ID | Severity A / B | Тип | Файл:рядок | Суть | Відтворення / аргумент | Рекомендація | Статус |
|----|----------------|-----|------------|------|------------------------|--------------|--------|
| **F-01** | LOW / LOW-MED | [VULN] | `facilitator/whitechain_facilitator.py:205`; вихід — `service_provider/server.py:308` | Текст винятку розрахунку йде клієнту: `_deny(f"Розрахунок не вдався: {exc}")`, а `exc` для atomic — `SettlementRelayError(f"…: {web3_exc}")` (`atomic_settlement.py:92`). Сервер повертає `result["reason"]` у 402. | Підписати валідну авторизацію так, щоб релей ревертнув (напр. revert у токені) → 402 несе внутрішній web3/revert-текст. Інші deny-причини — статичні рядки; тільки ця гілка вставляє `{exc}`. Суперечить принципу SECURITY_REVIEW «не віддавати RPC-текст клієнту». | Клієнту — узагальнена причина; деталь лише в `logger` (як уже зроблено в гілці FUNDS_HELD). | **CLOSED** — Fixed in `d786da1`: клієнту узагальнена причина, `str(exc)` лише в лог; регресійний тест `tests/test_error_sanitization.py`. |
| **F-02** | LOW / HIGH | [MISSING] | `service_provider/server.py` (усі роути); доведено grep — 0 збігів `rate.?limit\|slowapi\|limiter\|throttl` | Немає rate-limiting/DoS-захисту. `POST /photo/{name}` для **верифікованого** агента ініціює on-chain relay (витрата gas); будь-який роут робить 1–3 синхронних `.call()` до RPC на запит. | Неверифікований флуд відсікається policy до settlement (gas не витрачається — добре), але RPC-читання identity виконуються до того й підсилюють навантаження на ноду; верифікований агент може флудити реальні settlements. | slowapi/reverse-proxy rate-limit + автентифікація на платіжному роуті. | Open (B) |
| **F-03** | LOW / MEDIUM | [MISSING] | `chain.py:41` (`Web3.HTTPProvider(...)` без `request_kwargs`); читання — `identity.py:62,73,78` | RPC-`.call()` без таймауту. `get_agent_identity` робить 3 синхронних `.call()` у воркер-треді FastAPI; зависла/повільна нода блокує тред без обмеження → вичерпання пулу. | `HTTPProvider` створюється без `request_kwargs={'timeout':…}`; лише `wait_for_transaction_receipt` має `timeout=120` (`chain.py:115`), читання — ні. | `request_kwargs={'timeout': N}` + retry-middleware; ідеально — кеш verified-стану. | Open (B) |
| **F-04** | LOW / HIGH | [SCOPED] | `contracts/AgentPayRouter.sol:153` (`_requireKYA`); заглушка `contracts/mocks/MockRouterKYA.sol:15,19` | Ончейн-KYA роутера читає `ISoulRegistry.isVerified(uint256)`, якого реальний атрибутний WB Soul не має → роутер деплоїться проти `MockRouterKYA`. Її `setSoul/setVerified` **без контролю доступу** — на публічному testnet будь-хто себе «верифікує». | Справжній KYA-гейт у atomic — **офчейн** `policy.check_policy` (`policy.py:19-23`, реальний WB Soul при `USE_MOCK_SOUL=false`) + allowlist релеєрів (покупець не б'є роутер напряму). Ончейн-гейт — no-op-заглушка. Задокументовано в README/DEPLOY_WHITECHAIN. | Адаптер під реальний `ISoulAttributeRegistry` (планка B). Для демо — лишити, позначено як заглушка. | Scoped/documented |
| **F-05** | LOW / HIGH | [SCOPED] | `facilitator/store.py:60-73`, `113-139` | Single-process інваріант: `threading.Lock` серіалізує лише в межах процесу; кілька воркерів зламали б атомарність лічильників. Форсується ексклюзивним `flock` — 2-й процес падає `StoreLockError`. | `uvicorn --workers N` → 2-й інстанс не стартує (гучно, не тихо). Для горизонтального масштабу треба БД-транзакції. Задокументовано. | Postgres + `SELECT … FOR UPDATE` / міжпроцесні локи (планка B). | Scoped/documented |
| **F-06** | LOW / MED | [SCOPED] | `facilitator/settlement.py:144-149`; обробка — `whitechain_facilitator.py` (гілка `SettlementForwardError`) | Legacy-режим має вікно часткового збою «relay ok, forward reverted» → funds-held. Atomic (тепер дефолт) цього не має (одна tx). | Доведено тестом, що atomic ніколи не кидає `SettlementForwardError` (`test_atomic_settlement_integration.py`). Legacy лишається за `SETTLEMENT_MODE=legacy`. | Для реальних грошей — вимкнути legacy, лишити тільки atomic. | Scoped/documented |
| **F-07** | INFO / MED | [SCOPED] | `facilitator/policy.py:37-45` (`_within_spend_limits` no-op); репутація — `store.py` (single-node) | Spend-limit gate — заглушка (завжди allow). Репутація — trust-on-first-use, sybil-able, лічильники в локальному SQLite, який сам процес і пише. | `_within_spend_limits` повертає `True`; README вже чесно описує sybil/cold-start. | Реальні per-agent ліміти + cross-node/on-chain provenance лічильників (B). | Scoped/documented |
| **F-08** | LOW / HIGH | [SCOPED] | `config.py:80-87` (ключі з env); `.gitignore:1` | Приватні ключі фасилітатора/агента — plaintext у `.env`/env-vars. `.env` у `.gitignore`, **ніколи не комітився** (доведено `git log --all`), у `.env.example` поля порожні, у трекнутих файлах немає 64-hex ключів. | grep по історії й трекнутих файлах — чисто. Для демо це прийнятно. | KMS/HSM або хоча б розділення ключів і least-privilege релеєра (B). | Scoped/documented |
| **F-09** | INFO / N/A | [DOC-DRIFT] | `scripts/demo.py` (рядок наративу «комісія … списана на facilitator») | У atomic-режимі комісія йде on-chain на `owner()`=treasury, а демо-рядок каже «на facilitator». Косметика виводу демо, не логіка розрахунку. | `_split` шле fee на `owner()` (`AgentPayRouter.sol:173`); у локальному демо `owner=deployer`. | Поправити рядок демо (поза скоупом цього read-only аудиту). | **CLOSED** — Fixed in `d786da1`: демо-рядок тепер називає реальний напрямок за режимом (treasury/owner у atomic, facilitator у legacy). |
| **F-10** | INFO / MED | [DOC-DRIFT] | `SECURITY_REVIEW.md` (scope-нота); `README.md` Security Notes | `SECURITY_REVIEW.md` покриває **Phase 0** (нативні WBT, без KYA). Живий EIP-3009/KYA/atomic-шлях **не** мав еквівалентного незалежного аудиту. README це чесно зазначає — тож це не прихована розбіжність, а відкритий пункт. | README Security Notes прямо каже «Phase 1 surface … has not had an equivalent dedicated review yet». | Незалежний зовнішній аудит живого шляху (планка B, критично). | Documented open item |

---

## Що вже зроблено правильно (не переробляти)

- **C-1 закрито по-справжньому, з тестами.** `settlePaymentAtomic` (`AgentPayRouter.sol:123-151`): `isRelayer[msg.sender]`-гейт → `feeBps>MAX_FEE_BPS`-cap (`:136`, 10%) → `_requireKYA` → `nonce=computeNonce(seller,feeBps,resourceHash)` (`:143`). tEURC відновлює підпис над цим nonce і `value`, тож підміна продавця/суми/комісії/ресурсу дає інший nonce/value → recover≠from → revert. Покрито: seller-swap, amount-inflate, feeBps-change, feeBps>cap, non-relayer, replay, KYA (`test-solidity/AgentPayRouter.test.ts`, 9 кейсів).
- **Ончейн-комісія Є.** `_split` (`AgentPayRouter.sol:163-173`): `safeTransfer(owner(), feeAmount)` + `safeTransfer(seller, sellerAmount)`, floor-інваріант `fee+net==value`. (Спростовує попередні хибні «no on-chain fee».)
- **EIP-3009 у tEURC — за референсом Circle.** Typehash-і обчислені компілятором з рядка (`tEURC.sol:34-40`), спільний anti-replay реєстр для transfer/receive (`:45`), `receiveWithAuthorization` вимагає `to==msg.sender` (`:111`), часове вікно (`:128-129`). Покрито tEURC-тестами (replay/expired/forged/receive-payee).
- **Офчейн ↔ ончейн дзеркало без дрейфу.** `router_binding.py` — ЄДИНЕ джерело похідного nonce й RECEIVE-типів; і клієнт (`agent_client.py`), і валідатор (`payment.py`) імпортують його. `payment.py` перевіряє `to==router`, точну суму (overpayment reject, `:139-147` в базовій версії), anti-replay через on-chain `authorizationState`.
- **Reentrancy + CEI.** `nonReentrant` на `settlePaymentAtomic`; на рівні токена nonce позначається used **до** `_transfer` (`tEURC.sol:120-121`). `SafeERC20` на всіх переказах.
- **Anti-replay має on-chain джерело істини.** `payment.py` читає `teurc.authorizationState(...)`, а не локальний стан; atomic replay — чиста відмова без руху коштів (доведено тестом «no funds-held»).
- **KYA реально гейтить (офчейн).** `policy.check_policy` відхиляє not-KYA/not-verified **до** будь-якого settlement (`policy.py:19-23`), тож gas не витрачається на неверифікованих.
- **Store fail-fast, не тихий злам.** Ексклюзивний `flock` проти multi-process, `PRAGMA user_version`-guard проти старої схеми (`store.py:87-139`).
- **Підписана реєстрація (F4).** `registry_auth.verify_registration` вимагає `id==signer` (`registry_auth.py:61`); порядок вибору провайдера — за серверним `created_at/rowid`, не за полем реєстранта (`store.py:310`).
- **/admin під токеном, безпечний дефолт.** Порожній `ADMIN_API_TOKEN` → 403 (вимкнено, не відкрито); `hmac.compare_digest` (`server.py:174-178`). Параметризований SQL усюди (`store.py`). `.env` не в git.

---

## Продакшн-gap (планка B) — звідний чесний список

Обов'язкове, перш ніж через систему пройдуть реальні кошти:

1. **Незалежний зовнішній аудит живого шляху** — EIP-3009 relay + atomic router + KYA/reputation, НЕ відставленого Phase 0. Наявний `SECURITY_REVIEW.md` цього не покриває (F-10).
2. **Реальна ончейн-KYA** — адаптер `AgentPayRouter` → справжній `ISoulAttributeRegistry` WB Soul замість `MockRouterKYA`-заглушки; прибрати неаутентифіковані сеттери з деплой-шляху (F-04).
3. **Rate-limiting + автентифікація** на FastAPI, особливо на платіжному роуті (F-02).
4. **Стійкість до RPC** — таймаути на `.call()`, retry, кеш verified-стану, деградація без блокування воркерів (F-03).
5. **Shared store замість single-process** — Postgres/БД-транзакції для атомарності лічильників і горизонтального масштабу (F-05).
6. **Custody ключів** — KMS/HSM, least-privilege релеєр, ротація (F-08).
7. **Atomic як єдиний режим** — вимкнути legacy (усунути вікно funds-held) для реальних коштів (F-06).
8. **Реальні spend-ліміти + sybil-стійка репутація** — cross-node/on-chain provenance лічильників (F-07).
9. **Моніторинг** — баланс нативного gas релеєра, nonce-гонки, alerting на failed settlements / held-funds.
10. **Не витікати внутрішні помилки** клієнту (F-01).

---

## Прогін тестів (поточний стан, факт)

| Набір | Результат |
|-------|-----------|
| `npx hardhat test` | **23 passing** |
| `python -m pytest tests/` | **114 passed** (2 warnings: сторонні DeprecationWarning) |
| `python scripts/demo.py` (atomic default) | проходить наскрізь (KYA-deny, reputation-gate, replay-reject, 4 settlements) |

**Покриті security-властивості (тести):** seller-binding revert, amount/feeBps-inflate revert, feeBps>cap, non-relayer revert, replay revert (Solidity + Python), floor-інваріант fee+net==value, KYA-deny, atomic «no funds-held», клієнтський похідний nonce == Solidity computeNonce.
**Не покрито тестами (для планки B):** rate-limiting/DoS, поведінка при RPC-таймауті/деградації, multi-process store race (форсується локом, не тестом), реальний WB Soul KYA-шлях.

---

## Вердикт

- **Планка A (testnet-демо):** готово. Ядро коректне, тести зелені, знайдені пункти — або дрібні (F-01/F-09), або свідомо scoped і задокументовані. Демо можна показувати під грант.
- **Планка B (реальні гроші):** не готово; блокери — незалежний аудит живого шляху + реальна ончейн-KYA + операційне загартування (rate-limit, RPC-стійкість, shared store, custody). Це очікуваний стан для гранто-заявочного PoC і саме те, що описано як next-phase роботу.

**CRITICAL не знайдено** — зупинка для рішення не потрібна. Жодних правок не застосовано (read-only). F-01 і F-09 — єдині справжні (нехай і дрібні) дефекти; **обидва закриті в `d786da1`** (див. Status). Решта — scoped/doc-drift/gap планки B.
