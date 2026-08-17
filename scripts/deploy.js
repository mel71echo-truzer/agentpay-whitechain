const hre = require("hardhat");

async function main() {
  const [deployer] = await hre.ethers.getSigners();
  console.log("Деплоїмо від адреси:", deployer.address);

  const balance = await hre.ethers.provider.getBalance(deployer.address);
  console.log("Баланс WBT (для газу):", hre.ethers.formatEther(balance));

  const decimals = 6;                       // як у USDC
  const initialSupply = hre.ethers.parseUnits("1000000", decimals); // 1 000 000 apUSD

  const Token = await hre.ethers.getContractFactory("AgentPayUSD");
  const token = await Token.deploy(decimals, initialSupply, deployer.address);
  await token.waitForDeployment();

  const address = await token.getAddress();
  console.log("\n✅ AgentPayUSD розгорнуто за адресою:", address);
  console.log("Explorer: https://testnet.whitechain.io/address/" + address);
  console.log("\nДодай у .env рядок:\nTOKEN_ADDRESS=" + address);
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
