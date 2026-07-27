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

    // Deploy Mock Soul Registry
    const MockSoul = await ethers.getContractFactory("MockSoulRegistry");
    mockSoulRegistry = await MockSoul.deploy();

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
