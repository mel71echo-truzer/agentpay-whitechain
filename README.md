# AgentPay — Крок 1 Шляху A: токен apUSD з EIP-3009

Це перший модуль стандарт-сумісної x402-реалізації на Whitechain testnet.
Тут — ERC-20 токен **apUSD** з підтримкою **EIP-3009** (`transferWithAuthorization`).
Саме такий токен потрібен для канонічної x402-схеми `exact`: платник **підписує**
дозвіл на переказ off-chain, а facilitator відправляє цей підпис у блокчейн.

> **Терміни одним реченням**
> - **ERC-20** — стандарт звичайного токена в EVM-мережах.
> - **EIP-3009** — розширення, що дозволяє переказ токена за підписом, без окремої транзакції `approve`.
> - **facilitator** — сервіс-посередник, який приймає підпис і виконує оплату в блокчейні (будуємо в кроці 2).
> - **WBT** — нативний токен Whitechain, потрібен як «пальне» (газ) для транзакцій.

---

## Що ти отримаєш після цього кроку
Розгорнутий у Whitechain testnet токен apUSD, адресу якого буде видно в explorer,
і 1 000 000 apUSD на твоєму гаманці для демо.

---

## 0. Передумови (постав один раз)
- **Node.js 18+** — перевір: `node -v`
- **MetaMask** (браузерний гаманець) — https://metamask.io

---

## 1. Створи testnet-гаманець
1. Встанови MetaMask, створи новий гаманець (або окремий акаунт **тільки для testnet** — не використовуй свій основний).
2. Скопіюй **приватний ключ**: MetaMask → три крапки → Account details → Show private key.
   ⚠️ Цей ключ = повний доступ до гаманця. Тримай тільки в `.env`, ніколи не постить у git/чат.

## 2. Додай мережу Whitechain testnet у MetaMask
MetaMask → Networks → Add network → Add manually:
| Поле | Значення |
|---|---|
| Network name | Whitechain Testnet |
| RPC URL | `https://rpc-testnet.whitechain.io` |
| Chain ID | `2625` |
| Symbol | `WBT` |
| Block explorer | `https://testnet.whitechain.io` |

## 3. Візьми WBT з faucet (на газ)
1. Відкрий https://testnet.whitechain.io (explorer) і знайди **Faucet** (або сторінку faucet Whitechain).
2. Встав адресу свого гаманця, отримай **1 WBT** (ліміт: 1 WBT / 24 год на адресу).
3. У MetaMask на мережі Whitechain Testnet має зʼявитись баланс ~1 WBT.

> Якщо faucet не знаходиться — напиши мені, знайдемо актуальне посилання.

## 4. Налаштуй проєкт
```bash
cd agentpay
npm install
cp .env.example .env
```
Відкрий `.env` і встав свій приватний ключ:
```
PRIVATE_KEY=0x....(твій ключ від testnet-гаманця)
```

## 5. Скомпілюй і задеплой
```bash
npm run compile      # має вивести "Compiled 1 Solidity file"
npm run deploy       # деплоїть токен у Whitechain testnet
```
У консолі побачиш:
```
✅ AgentPayUSD розгорнуто за адресою: 0x....
Explorer: https://testnet.whitechain.io/address/0x....
TOKEN_ADDRESS=0x....
```
Скопіюй `TOKEN_ADDRESS=0x...` у свій `.env` — знадобиться в наступних кроках.

## 6. Перевір
```bash
npm run balance      # покаже баланс WBT (газ) і apUSD
```
Або відкрий адресу контракту в explorer — маєш побачити токен apUSD і total supply 1 000 000.

---

## Що всередині
```
agentpay/
├─ contracts/AgentPayUSD.sol   # ERC-20 + EIP-3009 (перевірено: компілюється, typehash-і коректні)
├─ scripts/deploy.js           # деплой у Whitechain testnet
├─ scripts/balance.js          # перевірка балансів
├─ hardhat.config.js           # мережа whitechainTestnet (chainId 2625)
├─ .env.example                # шаблон конфіга (ключ, RPC, адреса токена)
└─ README.md
```

## Типова пастка (на майбутнє, крок 2)
`web3.py` часто падає на PoA-мережах (як Whitechain) з помилкою `extraData`.
Лікується одним рядком — додаванням `geth_poa_middleware`. У цьому кроці (Hardhat/ethers.js)
проблеми нема, але коли писатимемо facilitator на Python — памʼятаємо.

---

## Наступні кроки Шляху A
- [x] **Крок 1 — токен apUSD з EIP-3009** (цей модуль)
- [ ] Крок 2 — Python-facilitator (перевіряє підпис, виконує `transferWithAuthorization`)
- [ ] Крок 3 — платний FastAPI-ендпоінт (віддає контент за 402 → оплату)
- [ ] Крок 4 — агент-покупець на Claude API (підписує оплату, отримує контент)
- [ ] Крок 5 — зібрати демо на testnet + відео + GitHub + стаття
