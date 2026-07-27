import { expect } from "chai";
import { ethers } from "hardhat";
import { time } from "@nomicfoundation/hardhat-network-helpers";
import type { HDNodeWallet } from "ethers";

describe("tEURC", function () {
  const ONE = 1_000_000n; // 1.000000 tEURC (6 decimals)

  async function deployFixture() {
    const [deployer, relayer, payee] = await ethers.getSigners();
    const from: HDNodeWallet = ethers.Wallet.createRandom().connect(ethers.provider);

    // fund the random signer with gas money so it *could* submit its own
    // txs (it never needs to for EIP-3009, but this keeps the fixture
    // realistic and rules out "reverted because sender is broke" false positives)
    await deployer.sendTransaction({ to: from.address, value: ethers.parseEther("1") });

    const TEURC = await ethers.getContractFactory("tEURC");
    const teurc = await TEURC.deploy();
    await teurc.waitForDeployment();

    await teurc.connect(deployer).mint(from.address, ONE * 100n);

    const network = await ethers.provider.getNetwork();
    const domain = {
      name: "Test EURC",
      version: "1",
      chainId: network.chainId,
      verifyingContract: await teurc.getAddress(),
    };

    return { teurc, deployer, relayer, payee, from, domain };
  }

  const transferAuthTypes = {
    TransferWithAuthorization: [
      { name: "from", type: "address" },
      { name: "to", type: "address" },
      { name: "value", type: "uint256" },
      { name: "validAfter", type: "uint256" },
      { name: "validBefore", type: "uint256" },
      { name: "nonce", type: "bytes32" },
    ],
  };

  async function signTransferAuth(
    signer: HDNodeWallet,
    domain: Record<string, unknown>,
    value: { from: string; to: string; value: bigint; validAfter: bigint; validBefore: bigint; nonce: string }
  ) {
    const signature = await signer.signTypedData(domain, transferAuthTypes, value);
    return ethers.Signature.from(signature);
  }

  it("transferWithAuthorization: succeeds with a valid signature and moves funds", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address,
      to: payee.address,
      value: ONE * 10n,
      validAfter: 0n,
      validBefore: now + 3600n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(from, domain, auth);

    // Relayer (not `from`, not `to`) submits it and pays gas — the whole point of EIP-3009.
    await expect(
      teurc
        .connect(relayer)
        .transferWithAuthorization(
          auth.from,
          auth.to,
          auth.value,
          auth.validAfter,
          auth.validBefore,
          auth.nonce,
          sig.v,
          sig.r,
          sig.s
        )
    )
      .to.emit(teurc, "AuthorizationUsed")
      .withArgs(auth.from, auth.nonce);

    expect(await teurc.balanceOf(payee.address)).to.equal(ONE * 10n);
    expect(await teurc.authorizationState(auth.from, auth.nonce)).to.equal(true);
  });

  it("transferWithAuthorization: reverts when the same nonce is replayed", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address,
      to: payee.address,
      value: ONE,
      validAfter: 0n,
      validBefore: now + 3600n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(from, domain, auth);
    const args = [
      auth.from,
      auth.to,
      auth.value,
      auth.validAfter,
      auth.validBefore,
      auth.nonce,
      sig.v,
      sig.r,
      sig.s,
    ] as const;

    await teurc.connect(relayer).transferWithAuthorization(...args);

    await expect(teurc.connect(relayer).transferWithAuthorization(...args)).to.be.revertedWith(
      "tEURC: authorization is used or canceled"
    );
  });

  it("transferWithAuthorization: reverts before validAfter", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address,
      to: payee.address,
      value: ONE,
      validAfter: now + 3600n, // not valid yet
      validBefore: now + 7200n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(from, domain, auth);

    await expect(
      teurc
        .connect(relayer)
        .transferWithAuthorization(
          auth.from,
          auth.to,
          auth.value,
          auth.validAfter,
          auth.validBefore,
          auth.nonce,
          sig.v,
          sig.r,
          sig.s
        )
    ).to.be.revertedWith("tEURC: authorization is not yet valid");
  });

  it("transferWithAuthorization: reverts after validBefore (expired)", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address,
      to: payee.address,
      value: ONE,
      validAfter: 0n,
      validBefore: now + 10n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(from, domain, auth);

    await time.increase(3600);

    await expect(
      teurc
        .connect(relayer)
        .transferWithAuthorization(
          auth.from,
          auth.to,
          auth.value,
          auth.validAfter,
          auth.validBefore,
          auth.nonce,
          sig.v,
          sig.r,
          sig.s
        )
    ).to.be.revertedWith("tEURC: authorization is expired");
  });

  it("transferWithAuthorization: reverts on a forged signature (wrong signer)", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();
    const impostor = ethers.Wallet.createRandom().connect(ethers.provider);

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address, // claims to be `from`...
      to: payee.address,
      value: ONE,
      validAfter: 0n,
      validBefore: now + 3600n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(impostor, domain, auth); // ...but signed by someone else

    await expect(
      teurc
        .connect(relayer)
        .transferWithAuthorization(
          auth.from,
          auth.to,
          auth.value,
          auth.validAfter,
          auth.validBefore,
          auth.nonce,
          sig.v,
          sig.r,
          sig.s
        )
    ).to.be.revertedWith("tEURC: invalid signature");
  });

  it("receiveWithAuthorization: reverts if caller is not the payee", async function () {
    const { teurc, relayer, payee, from, domain } = await deployFixture();

    const now = BigInt(await time.latest());
    const auth = {
      from: from.address,
      to: payee.address,
      value: ONE,
      validAfter: 0n,
      validBefore: now + 3600n,
      nonce: ethers.hexlify(ethers.randomBytes(32)),
    };
    const sig = await signTransferAuth(from, domain, auth);

    await expect(
      teurc
        .connect(relayer) // relayer != payee
        .receiveWithAuthorization(
          auth.from,
          auth.to,
          auth.value,
          auth.validAfter,
          auth.validBefore,
          auth.nonce,
          sig.v,
          sig.r,
          sig.s
        )
    ).to.be.revertedWith("tEURC: caller must be the payee");
  });

  it("permit (EIP-2612): approves via off-chain signature", async function () {
    const { teurc, from, payee, domain } = await deployFixture();

    const permitTypes = {
      Permit: [
        { name: "owner", type: "address" },
        { name: "spender", type: "address" },
        { name: "value", type: "uint256" },
        { name: "nonce", type: "uint256" },
        { name: "deadline", type: "uint256" },
      ],
    };
    const nonce = await teurc.nonces(from.address);
    const deadline = BigInt(await time.latest()) + 3600n;
    const value = ONE * 5n;

    const signature = await from.signTypedData(domain, permitTypes, {
      owner: from.address,
      spender: payee.address,
      value,
      nonce,
      deadline,
    });
    const sig = ethers.Signature.from(signature);

    await teurc.permit(from.address, payee.address, value, deadline, sig.v, sig.r, sig.s);

    expect(await teurc.allowance(from.address, payee.address)).to.equal(value);
  });

  it("mint: only the owner can mint", async function () {
    const { teurc, payee, from } = await deployFixture();

    await expect(teurc.connect(payee).mint(from.address, ONE)).to.be.revertedWithCustomError(
      teurc,
      "OwnableUnauthorizedAccount"
    );
  });

  it("decimals: reports 6, like real USDC/EURC", async function () {
    const { teurc } = await deployFixture();
    expect(await teurc.decimals()).to.equal(6);
  });
});
