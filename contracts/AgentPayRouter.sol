// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import {IERC20} from "@openzeppelin/contracts/token/ERC20/IERC20.sol";
import {SafeERC20} from "@openzeppelin/contracts/token/ERC20/utils/SafeERC20.sol";
import {ReentrancyGuard} from "@openzeppelin/contracts/utils/ReentrancyGuard.sol";
import {Ownable} from "@openzeppelin/contracts/access/Ownable.sol";

// ─────────────────────────────────────────────────────────────────────────────
// ROADMAP ARTIFACT — NOT WIRED INTO THE PAYMENT FLOW.
//
// The live settlement path is facilitator/settlement.py::SettlementEngine
// (off-chain EIP-3009 relay, then custody→forward). This contract is the
// Phase 2.5 direction: receive + fee-split + payout in ONE atomic transaction,
// which removes the partial-failure window the off-chain path handles with a
// journal + a "funds held" state (finding F3). No Python deploys or calls it;
// chain.py has no artifact entry for it.
//
// SECURITY — finding C-1 (seller-binding theft) is CLOSED here:
// The earlier version was `external` with no access control and took `seller`
// as an UNSIGNED parameter, while the buyer's EIP-3009 signature only covered
// (from, to=router, value, validAfter, validBefore, nonce). Anyone who saw a
// valid authorization could resubmit it with their own address as `seller` and
// steal the payout. This version binds every money-moving parameter to the
// buyer's signature and restricts who may submit:
//   - the nonce is DERIVED as keccak256(abi.encode(seller, feeBps, resourceHash)),
//     so changing the payee, the split, or the resource changes the nonce; the
//     buyer signed over the original nonce, so tEURC's ECDSA recovery no longer
//     returns `from` and the token itself reverts. The relayer cannot forge a
//     new signature.
//   - `amount` is bound as the signed `value` (a different amount fails the
//     token's signature check); the time window is signed; the asset is the
//     immutable teurcToken.
//   - only allow-listed relayers may call settle (facilitator-relayed model);
//     even so, a relayer cannot redirect funds — the destination is in the nonce.
// ─────────────────────────────────────────────────────────────────────────────

interface IERC20Receive3009 {
    /// EIP-3009: only the payee (`to`, here the router) may submit; the token
    /// recovers `from`'s signature over the full message incl. `nonce`.
    function receiveWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external;
}

interface ISoulRegistry {
    function soulOf(address account) external view returns (uint256);
    function isVerified(uint256 soulId) external view returns (bool);
}

/**
 * @title AgentPayRouter
 * @notice Atomic escrow + fee-split router for agent-to-agent payments, with
 *         every fund-moving parameter bound to the buyer's EIP-3009 signature.
 */
contract AgentPayRouter is Ownable, ReentrancyGuard {
    using SafeERC20 for IERC20;

    address public immutable teurcToken;
    address public immutable soulRegistry;

    /// Hard cap on the fee a single settlement may carry (10%). Defence in depth
    /// even though feeBps is signed: a buyer/provider can never be bound to more.
    uint16 public constant MAX_FEE_BPS = 1000;

    /// Facilitators allowed to submit settlements (facilitator-relayed model).
    mapping(address => bool) public isRelayer;

    event RelayerSet(address indexed relayer, bool allowed);
    event AtomicPaymentSettled(
        address indexed from,
        address indexed seller,
        uint256 amount,
        uint256 sellerAmount,
        uint256 feeAmount,
        uint16 feeBps,
        bytes32 indexed nonce
    );

    error KYACheckFailed();
    error NotRelayer();
    error FeeTooHigh();

    constructor(address _teurcToken, address _soulRegistry) Ownable(msg.sender) {
        teurcToken = _teurcToken;
        soulRegistry = _soulRegistry;
    }

    /// @notice Owner manages the relayer allow-list.
    function setRelayer(address relayer, bool allowed) external onlyOwner {
        isRelayer[relayer] = allowed;
        emit RelayerSet(relayer, allowed);
    }

    /// @notice The nonce that the buyer must sign, committing to the payee, the
    /// split, and the resource. The client computes the same value off-chain.
    function computeNonce(address seller, uint16 feeBps, bytes32 resourceHash)
        public
        pure
        returns (bytes32)
    {
        return keccak256(abi.encode(seller, feeBps, resourceHash));
    }

    /**
     * @notice Atomically pull `amount` from `from` (authorized off-chain via
     * EIP-3009), take `feeBps` for the owner, and forward the rest to `seller`.
     * The destination, split and resource are bound into the nonce, so the
     * caller (relayer) cannot change where the money goes without invalidating
     * the buyer's signature.
     *
     * @param resourceHash opaque per-payment binding (e.g. keccak256(resource‖salt)),
     *        mirroring the off-chain resource binding in facilitator/payment.py.
     */
    function settlePaymentAtomic(
        address from,
        address seller,
        uint256 amount,
        uint16 feeBps,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 resourceHash,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external nonReentrant {
        if (!isRelayer[msg.sender]) revert NotRelayer();
        if (feeBps > MAX_FEE_BPS) revert FeeTooHigh();
        _requireKYA(from);

        // Bind seller + split + resource into the nonce. tEURC recovers the
        // signer over THIS nonce; if the relayer changed any of them, the
        // recomputed nonce differs from what the buyer signed, recovery no
        // longer yields `from`, and the token reverts with "invalid signature".
        bytes32 nonce = computeNonce(seller, feeBps, resourceHash);

        // Pull funds in (token enforces the signature and single-use nonce).
        IERC20Receive3009(teurcToken).receiveWithAuthorization(
            from, address(this), amount, validAfter, validBefore, nonce, v, r, s
        );

        _split(from, seller, amount, feeBps, nonce);
    }

    function _requireKYA(address from) internal view {
        uint256 soulId = ISoulRegistry(soulRegistry).soulOf(from);
        if (soulId == 0 || !ISoulRegistry(soulRegistry).isVerified(soulId)) {
            revert KYACheckFailed();
        }
    }

    /// @dev fee floored; the remainder stays in sellerAmount (seller-favouring),
    /// matching settlement.py: net = value - floor(fee). safeTransfer checks the
    /// ERC-20 return value.
    function _split(address from, address seller, uint256 amount, uint16 feeBps, bytes32 nonce) internal {
        uint256 feeAmount = (amount * feeBps) / 10000;
        uint256 sellerAmount = amount - feeAmount;

        if (feeAmount > 0) {
            IERC20(teurcToken).safeTransfer(owner(), feeAmount);
        }
        IERC20(teurcToken).safeTransfer(seller, sellerAmount);

        emit AtomicPaymentSettled(from, seller, amount, sellerAmount, feeAmount, feeBps, nonce);
    }
}
