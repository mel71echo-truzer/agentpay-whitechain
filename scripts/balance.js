const hre = require("hardhat");

async function main() {
  const [me] = await hre.ethers.getSigners();
  const wbt = await hre.ethers.provider.getBalance(me.address);
  console.log("Адреса:", me.address);
  console.log("WBT (газ):", hre.ethers.formatEther(wbt));

  const tokenAddr = process.env.TOKEN_ADDRESS;
  if (tokenAddr) {
    const token = await hre.ethers.getContractAt("AgentPayUSD", tokenAddr);
    const [dec, sym, bal] = await Promise.all([
      token.decimals(), token.symbol(), token.balanceOf(me.address),
    ]);
    console.log(`${sym}:`, hre.ethers.formatUnits(bal, dec));
  }
}

main().catch((e) => { console.error(e); process.exitCode = 1; });
