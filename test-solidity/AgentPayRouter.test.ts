import { expect } from "chai";
import { ethers } from "hardhat";

describe("AgentPayRouter Atomic Settlement", function () {
  let router: any;
  let mockToken: any;
  let mockSoulRegistry: any;
  let owner: any;
  let seller: any;
  let buyer: any;

  beforeEach(async function () {
    [owner, seller, buyer] = await ethers.getSigners();

    // Deploy Mock Soul Registry. MockSoulRegistry's constructor requires an
    // IsVerified attribute and an SBT collection — deploying it with no args
    // (as before) threw "incorrect number of arguments to constructor", so this
    // whole suite never ran. For the KYA-revert test below the buyer has no
    // soul, so soulOf() short-circuits before isVerified() is consulted.
    const isVerifiedAttr = await (await ethers.getContractFactory("MockSoulAttribute")).deploy();
    const sbtCollection = await (await ethers.getContractFactory("MockSoulBoundTokenCollection")).deploy();
    const MockSoul = await ethers.getContractFactory("MockSoulRegistry");
    mockSoulRegistry = await MockSoul.deploy(
      await isVerifiedAttr.getAddress(),
      await sbtCollection.getAddress(),
    );

    // Deploy Mock tEURC Token
    const MockToken = await ethers.getContractFactory("tEURC");
    mockToken = await MockToken.deploy();

    // Deploy AgentPayRouter
    const Router = await ethers.getContractFactory("AgentPayRouter");
    router = await Router.deploy(await mockToken.getAddress(), await mockSoulRegistry.getAddress());
  });

  it("should revert if buyer does not pass KYA check", async function () {
    const nonce = ethers.randomBytes(32);
    
    await expect(
      router.settlePaymentAtomic(
        buyer.address,
        seller.address,
        1000,
        0,
        Math.floor(Date.now() / 1000) + 3600,
        nonce,
        27,
        ethers.ZeroHash,
        ethers.ZeroHash
      )
    ).to.be.revertedWithCustomError(router, "KYACheckFailed");
  });
});
