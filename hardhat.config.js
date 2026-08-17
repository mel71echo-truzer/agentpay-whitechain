require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

const PRIVATE_KEY = process.env.PRIVATE_KEY || "";

/** @type import('hardhat/config').HardhatUserConfig */
module.exports = {
  solidity: {
    version: "0.8.24",
    settings: {
      optimizer: { enabled: true, runs: 200 },
    },
  },
  networks: {
    whitechainTestnet: {
      url: process.env.RPC_URL || "https://rpc-testnet.whitechain.io",
      chainId: 2625,
      accounts: PRIVATE_KEY ? [PRIVATE_KEY] : [],
    },
  },
};
