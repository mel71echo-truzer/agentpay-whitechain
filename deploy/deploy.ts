import { ethers } from "hardhat";
import * as dotenv from "dotenv";

dotenv.config();

/**
 * Deploys tEURC (always) and, when USE_MOCK_SOUL=true, the local WB Soul
 * mocks (MockSoulAttribute, MockSoulBoundTokenCollection, MockSoulRegistry).
 *
 * This script is the Hardhat/TS-side deploy path, mainly for deploying to
 * `whitechain_testnet` per DEPLOY_WHITECHAIN.md. Nothing here is
 * hardcoded — USE_MOCK_SOUL and the deployer's own RPC/key come entirely
 * from .env / the --network flag. It only PRINTS the resulting addresses
 * (in .env KEY=VALUE form) rather than writing them into .env itself, to
 * avoid a script silently mutating a file that may already hold secrets —
 * copy the printed block into your .env by hand.
 *
 * The Python side (facilitator, agent_client, scripts/demo.py) has its own
 * mirroring deployment path (contracts_py.py) that reads the same Hardhat
 * compiler artifacts and deploys via web3.py — used for the local/offline
 * demo and test suite so they don't depend on a running Node process. This
 * script exists for real testnet deploys and for the Hardhat/TS test
 * suite in test-solidity/.
 */
async function main() {
  const useMockSoul = (process.env.USE_MOCK_SOUL ?? "true").toLowerCase() !== "false";
  const [deployer] = await ethers.getSigners();

  console.log(`Deployer: ${deployer.address}`);
  console.log(`Network:  ${(await ethers.provider.getNetwork()).name} (chainId=${(await ethers.provider.getNetwork()).chainId})`);
  console.log(`USE_MOCK_SOUL: ${useMockSoul}\n`);

  const TEURC = await ethers.getContractFactory("tEURC");
  const teurc = await TEURC.deploy();
  await teurc.waitForDeployment();
  const teurcAddress = await teurc.getAddress();
  console.log(`tEURC deployed: ${teurcAddress}`);

  const envLines = [`TEURC_ADDRESS=${teurcAddress}`];

  if (useMockSoul) {
    const MockSoulAttribute = await ethers.getContractFactory("MockSoulAttribute");
    const isVerifiedAttribute = await MockSoulAttribute.deploy();
    await isVerifiedAttribute.waitForDeployment();
    const isVerifiedAddress = await isVerifiedAttribute.getAddress();
    console.log(`MockSoulAttribute (IsVerified) deployed: ${isVerifiedAddress}`);

    const MockSbtCollection = await ethers.getContractFactory("MockSoulBoundTokenCollection");
    const sbtCollection = await MockSbtCollection.deploy();
    await sbtCollection.waitForDeployment();
    const sbtCollectionAddress = await sbtCollection.getAddress();
    console.log(`MockSoulBoundTokenCollection deployed: ${sbtCollectionAddress}`);

    const MockSoulRegistry = await ethers.getContractFactory("MockSoulRegistry");
    const soulRegistry = await MockSoulRegistry.deploy(isVerifiedAddress, sbtCollectionAddress);
    await soulRegistry.waitForDeployment();
    const soulRegistryAddress = await soulRegistry.getAddress();
    console.log(`MockSoulRegistry deployed: ${soulRegistryAddress}`);

    envLines.push(
      "USE_MOCK_SOUL=true",
      `SOUL_REGISTRY_ADDRESS=${soulRegistryAddress}`,
      `SOUL_ATTRIBUTE_REGISTRY_ADDRESS=${soulRegistryAddress}`,
      `SOUL_BOUND_TOKEN_REGISTRY_ADDRESS=${soulRegistryAddress}`,
      `IS_VERIFIED_ATTRIBUTE_ADDRESS=${isVerifiedAddress}`,
      `SBT_COLLECTION_ADDRESS=${sbtCollectionAddress}`
    );
  } else {
    console.log(
      "USE_MOCK_SOUL=false: skipping mock deployment — this network must already have " +
        "SOUL_REGISTRY_ADDRESS / SOUL_ATTRIBUTE_REGISTRY_ADDRESS / SOUL_BOUND_TOKEN_REGISTRY_ADDRESS / " +
        "IS_VERIFIED_ATTRIBUTE_ADDRESS / SBT_COLLECTION_ADDRESS set in .env, pointing at the real WB Soul contracts."
    );
    envLines.push("USE_MOCK_SOUL=false");
  }

  console.log("\nCopy these into your .env:\n");
  console.log(envLines.join("\n"));
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
