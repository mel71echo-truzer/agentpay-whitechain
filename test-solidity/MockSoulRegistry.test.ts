import { expect } from "chai";
import { ethers } from "hardhat";

describe("MockSoulRegistry", function () {
  async function deployFixture() {
    const [owner, agentA, agentB] = await ethers.getSigners();

    const MockSoulAttribute = await ethers.getContractFactory("MockSoulAttribute");
    const isVerified = await MockSoulAttribute.deploy();
    await isVerified.waitForDeployment();

    const MockSbtCollection = await ethers.getContractFactory("MockSoulBoundTokenCollection");
    const sbt = await MockSbtCollection.deploy();
    await sbt.waitForDeployment();

    const MockSoulRegistry = await ethers.getContractFactory("MockSoulRegistry");
    const registry = await MockSoulRegistry.deploy(await isVerified.getAddress(), await sbt.getAddress());
    await registry.waitForDeployment();

    return { registry, isVerified, sbt, owner, agentA, agentB };
  }

  it("soulOf returns 0 for an address with no soul", async function () {
    const { registry, agentA } = await deployFixture();
    expect(await registry.soulOf(agentA.address)).to.equal(0n);
  });

  it("registerSoul assigns an increasing soul id and setVerified flips soulAttributeValue", async function () {
    const { registry, isVerified, owner, agentA } = await deployFixture();

    await registry.connect(owner).registerSoul(agentA.address);
    const soulId = await registry.soulOf(agentA.address);
    expect(soulId).to.equal(1n);

    // Not verified yet: bytes20(0)
    expect(await registry.soulAttributeValue(soulId, await isVerified.getAddress())).to.equal(
      "0x0000000000000000000000000000000000000000"
    );

    await registry.connect(owner).setVerified(soulId, true);
    expect(await registry.soulAttributeValue(soulId, await isVerified.getAddress())).to.not.equal(
      "0x0000000000000000000000000000000000000000"
    );

    await registry.connect(owner).setVerified(soulId, false);
    expect(await registry.soulAttributeValue(soulId, await isVerified.getAddress())).to.equal(
      "0x0000000000000000000000000000000000000000"
    );
  });

  it("only the owner can registerSoul/setVerified/issueSBT", async function () {
    const { registry, agentA, agentB } = await deployFixture();

    await expect(registry.connect(agentA).registerSoul(agentB.address)).to.be.revertedWithCustomError(
      registry,
      "OwnableUnauthorizedAccount"
    );
  });

  it("issueSBT binds a token to a soul, reflected in tokensCountBySoul/isBound", async function () {
    const { registry, sbt, owner, agentA } = await deployFixture();

    await registry.connect(owner).registerSoul(agentA.address);
    const soulId = await registry.soulOf(agentA.address);

    expect(await registry.tokensCountBySoul(soulId)).to.equal(0n);

    await registry.connect(owner).issueSBT(soulId, 1);
    expect(await registry.tokensCountBySoul(soulId)).to.equal(1n);
    expect(await registry["isBound(address,uint96,uint256)"](await sbt.getAddress(), 1, soulId)).to.equal(true);

    await registry.connect(owner).issueSBT(soulId, 2);
    expect(await registry.tokensCountBySoul(soulId)).to.equal(2n);
  });

  it("issueSBT reverts if the same badge is issued twice to the same soul", async function () {
    const { registry, owner, agentA } = await deployFixture();
    await registry.connect(owner).registerSoul(agentA.address);
    const soulId = await registry.soulOf(agentA.address);

    await registry.connect(owner).issueSBT(soulId, 1);
    await expect(registry.connect(owner).issueSBT(soulId, 1)).to.be.revertedWith("MockSoulRegistry: already bound");
  });
});
