# Карта доступу до HTTP-маршрутів (перевірка №1)

> Кожен маршрут: хто може викликати → де САМЕ в коді контроль доступу.
> Порожня клітинка «контроль» = знахідка. Мережева межа: сервер за
> замовчуванням слухає `127.0.0.1` (`config.SERVICE_PROVIDER_HOST`).

| Маршрут | Метод | Хто може викликати | Контроль доступу (де в коді) |
|---|---|---|---|
| `/registry/capabilities` | GET | будь-хто (публічний discovery — за задумом) | немає HTTP-авторизації; записи ПІДПИСАНІ, агент сам звіряє (`agent_client.verify_capability_record`) |
| `/registry/register` | POST | будь-хто з валідним підписом провайдера | **підпис обов'язковий**: `capability.register` → `registry_auth.verify_registration` (id == підписант), `server.py` register-handler |
| `/admin/held-settlements` | GET | оператор із Bearer-токеном; **вимкнено** без `ADMIN_API_TOKEN` | `server._admin_auth_error` (403 якщо токен не заданий, 401 без/з хибним; `hmac.compare_digest`) |
| `/photos` | GET | будь-хто (публічний каталог цін — за задумом) | немає (лише ціни/назви, не чутливе) |
| `/balance` | GET | публічно — **лише агрегат**; деталізація `sales` — під адмін-токеном | `server.balance` → `_admin_auth_error` гейтить `sales` (адреси покупців/tx) |
| `/photo/{name}` | GET | будь-хто (завжди 402, безкоштовного доступу нема) | за задумом 402 (`request_photo`) |
| `/photo/{name}` | POST | будь-хто з валідною ПІДПИСАНОЮ EIP-3009-авторизацією | `facilitator.verify_and_settle` (KYA + reputation + `payment.validate_authorization`: підпис, вікно, nonce, отримувач, точна сума) |

**Висновок:** порожніх клітинок немає. Два публічні читання (`/registry/capabilities`,
`/photos`) відкриті СВІДОМО (discovery/каталог — не чутливе). Записи й гроші
(`/registry/register`, `/photo POST`) — під криптографічним підписом.
Операторські дані (`/admin/*`, деталізація `/balance`) — під токеном, вимкнені
за замовчуванням.

**Що змінилося в цьому проході:**
- `/admin/held-settlements` — раніше було відкрито (знахідка, внесена мною ж);
  тепер під токеном, вимкнено без `ADMIN_API_TOKEN`.
- `/balance` — деталізований `sales` (адреси покупців + tx) сховано за той самий
  токен; публічно лишився тільки агрегат доходу.

Тести: `tests/test_server_routes.py` (усі маршрути + 403/401/200 для `/admin`,
агрегат-vs-деталь для `/balance`). Покриття `service_provider/server.py`:
0% → ~90%.
