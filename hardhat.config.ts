import { HardhatUserConfig } from "hardhat/config";
import "@nomicfoundation/hardhat-toolbox";
import * as dotenv from "dotenv";

dotenv.config();

// Нічого тут не хардкодиться — усе з .env. Порожні значення нижче не
// заважають `hardhat compile`/`hardhat test` (вони не чіпають мережу
// whitechain_testnet), але деплой на неї без реальних WHITECHAIN_TESTNET_RPC
// і DEPLOYER_PRIVATE_KEY впаде з зрозумілою помилкою — це навмисно.
const WHITECHAIN_TESTNET_RPC = process.env.WHITECHAIN_TESTNET_RPC || "";
const DEPLOYER_PRIVATE_KEY = process.env.DEPLOYER_PRIVATE_KEY || "";
const CHAIN_ID = process.env.CHAIN_ID ? Number(process.env.CHAIN_ID) : 2625;

// Мережа whitechain_testnet тут налаштована і готова до використання, навіть
// якщо з поточного середовища до неї немає мережевого доступу (див. README,
// розділ "Step 0" — egress-політика цієї сесії блокує весь домен
// whitechain.io). Перехід з локальної розробки на реальний Whitechain
// testnet — це виключно заповнення .env, без жодних змін коду:
// `npx hardhat run deploy/deploy.ts --network whitechain_testnet`
// (детальний порядок дій — DEPLOY_WHITECHAIN.md).
const config: HardhatUserConfig = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
      evmVersion: "cancun",
    },
  },
  paths: {
    sources: "./contracts",
    tests: "./test-solidity",
    cache: "./cache",
    artifacts: "./artifacts",
  },
  networks: {
    hardhat: {},
    whitechain_testnet: {
      url: WHITECHAIN_TESTNET_RPC,
      chainId: CHAIN_ID,
      accounts: DEPLOYER_PRIVATE_KEY ? [DEPLOYER_PRIVATE_KEY] : [],
    },
  },
};

export default config;
