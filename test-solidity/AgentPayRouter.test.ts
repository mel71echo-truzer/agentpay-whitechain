import { expect } from "chai";
import { ethers } from "hardhat";

// AgentPayRouter — security suite for finding C-1 (seller-binding theft).
//
// The old contract took `seller` as an UNSIGNED parameter and was `external`
// with no access control, so a relayer could redirect the payout. The new
// contract binds seller + feeBps + resourceHash into the nonce the buyer signs,
// and gates settle behind a relayer allow-list. Each "attack" test below reverts
// on the new contract; the same manoeuvre SUCCEEDED on the old one (see the
// summary in the PR / AUDIT_REPORT.md, where the old contract is run for
// contrast).

const AMOUNT = 1_000_000n; // 1.0 tEURC (6 decimals)
const FEE_BPS = 50; // 0.5%
const FEE = (AMOUNT * BigInt(FEE_BPS)) / 10_000n; // 5000
const SELLER_AMOUNT = AMOUNT - FEE; // 995000
const RESOURCE_HASH = ethers.keccak256(ethers.toUtf8Bytes("/photo/kyiv-lavra|salt"));

const abi = ethers.AbiCoder.defaultAbiCoder();

function routerNonce(seller: string, feeBps: number, resourceHash: string): string {
  return ethers.keccak256(abi.encode(["address", "uint16", "bytes32"], [seller, feeBps, resourceHash]));
}

async function signAuth(
  buyer: any,
  teurcAddr: string,
  routerAddr: string,
  chainId: bigint,
  amount: bigint,
  seller: string,
  feeBps: number,
  resourceHash: string,
  validAfter: bigint,
  validBefore: bigint,
) {
  const nonce = routerNonce(seller, feeBps, resourceHash);
  const domain = { name: "Test EURC", version: "1", chainId, verifyingContract: teurcAddr };
  const types = {
    ReceiveWithAuthorization: [
      { name: "from", type: "address" },
      { name: "to", type: "address" },
      { name: "value", type: "uint256" },
      { name: "validAfter", type: "uint256" },
      { name: "validBefore", type: "uint256" },
      { name: "nonce", type: "bytes32" },
    ],
  };
  const msg = { from: buyer.address, to: routerAddr, value: amount, validAfter, validBefore, nonce };
  const sig = ethers.Signature.from(await buyer.signTypedData(domain, types, msg));
  return { v: sig.v, r: sig.r, s: sig.s, nonce };
}

describe("AgentPayRouter — atomic settlement + C-1 seller-binding", function () {
  let owner: any, relayer: any, buyer: any, honestSeller: any, attacker: any;
  let tEURC: any, kya: any, router: any;
  let teurcAddr: string, routerAddr: string, chainId: bigint;

  async function future(): Promise<bigint> {
    return BigInt((await ethers.provider.getBlock("latest"))!.timestamp) + 3600n;
  }

  beforeEach(async function () {
    [owner, relayer, buyer, honestSeller, attacker] = await ethers.getSigners();
    chainId = (await ethers.provider.getNetwork()).chainId;

    tEURC = await (await ethers.getContractFactory("tEURC")).deploy();
    teurcAddr = await tEURC.getAddress();
    await tEURC.mint(buyer.address, AMOUNT);

    kya = await (await ethers.getContractFactory("MockRouterKYA")).deploy();
    await kya.setSoul(buyer.address, 1);
    await kya.setVerified(1, true);

    router = await (await ethers.getContractFactory("AgentPayRouter")).deploy(teurcAddr, await kya.getAddress());
    routerAddr = await router.getAddress();
    await router.setRelayer(relayer.address, true);
  });

  // ---- happy path ----

  it("happy path: relayer settles; seller gets amount-fee, owner gets fee", async function () {
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    const ownerBefore = await tEURC.balanceOf(owner.address);

    await router
      .connect(relayer)
      .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s);

    expect(await tEURC.balanceOf(honestSeller.address)).to.equal(SELLER_AMOUNT);
    expect(await tEURC.balanceOf(owner.address)).to.equal(ownerBefore + FEE);
    expect(await tEURC.balanceOf(buyer.address)).to.equal(0n);
  });

  it("floor remainder of the fee goes to the seller (matches the Python convention)", async function () {
    // amount=999, feeBps=50 -> fee = floor(999*50/10000) = floor(4.995) = 4,
    // seller = 999 - 4 = 995. The sub-unit remainder (0.995) lands in the seller
    // share, exactly like settlement.py: net = value - floor(fee).
    const amount = 999n;
    await tEURC.mint(buyer.address, amount); // buyer had 0 left after previous mint use? fresh: has AMOUNT
    // buyer currently holds AMOUNT (1_000_000) + amount; use a fresh buyer balance baseline
    const buyerStart = await tEURC.balanceOf(buyer.address);
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, amount, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    await router
      .connect(relayer)
      .settlePaymentAtomic(buyer.address, honestSeller.address, amount, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s);

    expect(await tEURC.balanceOf(honestSeller.address)).to.equal(995n); // 999 - floor(4.995)=4
    expect(await tEURC.balanceOf(buyer.address)).to.equal(buyerStart - amount);
  });

  // ---- attack #1: seller substitution (the core of C-1) ----

  it("attack #1: relayer swaps in their own seller -> revert (destination is bound to the nonce)", async function () {
    const validBefore = await future();
    // Buyer signs for the HONEST seller.
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    // Relayer submits with attacker as seller: the contract recomputes the nonce
    // over `attacker`, which differs from what the buyer signed, so tEURC's
    // signature recovery no longer yields `from`.
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(buyer.address, attacker.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWith("tEURC: invalid signature");
    expect(await tEURC.balanceOf(attacker.address)).to.equal(0n);
  });

  // ---- attack #2: inflate amount / fee past the signature ----

  it("attack #2a: relayer inflates the amount past the signed value -> revert", async function () {
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    // amount is the signed EIP-3009 `value`; a different amount fails recovery.
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT * 2n, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWith("tEURC: invalid signature");
  });

  it("attack #2b: relayer changes feeBps away from what was signed -> revert (split is bound)", async function () {
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    // feeBps is inside the nonce; changing it (still within the cap) changes the
    // nonce -> signature no longer recovers to `from`.
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, 900, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWith("tEURC: invalid signature");
  });

  it("attack #2c: feeBps above the hard cap -> FeeTooHigh (defence in depth)", async function () {
    const validBefore = await future();
    const badBps = 2000;
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, badBps, RESOURCE_HASH, 0n, validBefore,
    );
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, badBps, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWithCustomError(router, "FeeTooHigh");
  });

  // ---- attack #3: unauthorized caller ----

  it("attack #3: a non-relayer submits a fully valid authorization -> NotRelayer", async function () {
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    await expect(
      router
        .connect(attacker) // not on the allow-list
        .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWithCustomError(router, "NotRelayer");
  });

  // ---- replay ----

  it("replay: the same authorization cannot be settled twice", async function () {
    const validBefore = await future();
    const { v, r, s } = await signAuth(
      buyer, teurcAddr, routerAddr, chainId, AMOUNT, honestSeller.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    await router
      .connect(relayer)
      .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s);
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(buyer.address, honestSeller.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWith("tEURC: authorization is used or canceled");
  });

  // ---- KYA gate still enforced ----

  it("KYA: a buyer without a verified soul -> KYACheckFailed", async function () {
    const validBefore = await future();
    // honestSeller stands in as a non-KYA buyer (no soul set). Sign as them.
    const { v, r, s } = await signAuth(
      honestSeller, teurcAddr, routerAddr, chainId, AMOUNT, attacker.address, FEE_BPS, RESOURCE_HASH, 0n, validBefore,
    );
    await expect(
      router
        .connect(relayer)
        .settlePaymentAtomic(honestSeller.address, attacker.address, AMOUNT, FEE_BPS, 0n, validBefore, RESOURCE_HASH, v, r, s),
    ).to.be.revertedWithCustomError(router, "KYACheckFailed");
  });
});
