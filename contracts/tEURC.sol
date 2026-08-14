// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/access/Ownable2Step.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/**
 * @title tEURC — Test EURC-style stablecoin for the AgentPay PoC
 * @author AgentPay on Whitechain
 * @notice A minimal testnet stand-in for a regulated euro stablecoin: 6
 *         decimals (like real USDC/EURC), owner-only faucet mint, EIP-2612
 *         permit (via OpenZeppelin's ERC20Permit) and the full EIP-3009
 *         authorization set (transfer / receive / cancel) so an agent can
 *         authorize a payment with an off-chain signature — no on-chain
 *         `approve()` step, and no need to wait for that signature's *own*
 *         transaction to mine before a facilitator relays it.
 *
 * @dev EIP-3009 semantics follow Circle's reference FiatTokenV2 (typehashes,
 *      validAfter/validBefore windows, per-authorizer nonce bookkeeping):
 *      https://github.com/circlefin/stablecoin-evm/blob/master/contracts/v2/EIP3009.sol
 *      Rebuilt on OpenZeppelin's EIP712/ECDSA primitives (via ERC20Permit,
 *      which already extends EIP712) rather than copying Circle's own EIP712
 *      helper, to avoid two differently-shaped EIP712 implementations.
 *
 *      Ownership uses {Ownable2Step}: transferring the owner (mint authority)
 *      requires the new owner to accept, so a mistyped address cannot silently
 *      brick the faucet. Reverts use custom errors (cheaper, machine-readable
 *      selectors) instead of revert strings.
 */
contract tEURC is ERC20, ERC20Permit, Ownable2Step {
    uint8 private constant _DECIMALS = 6;

    // Computed at compile time (not hand-transcribed) to avoid any chance
    // of a typo in the hash — Solidity evaluates keccak256 of a literal
    // string as a constant expression.
    bytes32 public constant TRANSFER_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "TransferWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );

    bytes32 public constant RECEIVE_WITH_AUTHORIZATION_TYPEHASH = keccak256(
        "ReceiveWithAuthorization(address from,address to,uint256 value,uint256 validAfter,uint256 validBefore,bytes32 nonce)"
    );

    /// @dev EIP-3009 cancellation: the authorizer signs (authorizer, nonce) to
    /// burn an as-yet-unused nonce, so a leaked-but-unrelayed authorization can
    /// be revoked before anyone submits it.
    bytes32 public constant CANCEL_AUTHORIZATION_TYPEHASH = keccak256(
        "CancelAuthorization(address authorizer,bytes32 nonce)"
    );

    /// @notice authorizer => nonce => used. Anti-replay for all authorization
    /// flavors lives in one map, matching Circle's reference (a nonce used for
    /// a transfer can't later be reused for a receive or a cancel, or vice versa).
    mapping(address => mapping(bytes32 => bool)) private _authorizationStates;

    /// @notice Emitted when an authorization nonce is consumed by a transfer/receive.
    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);
    /// @notice Emitted when an authorization nonce is voided via {cancelAuthorization}.
    event AuthorizationCanceled(address indexed authorizer, bytes32 indexed nonce);

    /// @notice The recovered signer is not the stated `from`/authorizer.
    error InvalidSignature();
    /// @notice `block.timestamp <= validAfter` — the authorization is not yet valid.
    error AuthorizationNotYetValid();
    /// @notice `block.timestamp >= validBefore` — the authorization has expired.
    error AuthorizationExpired();
    /// @notice The nonce was already used or canceled for this authorizer.
    error AuthorizationAlreadyUsed();
    /// @notice `receiveWithAuthorization` was not called by the payee (`to`).
    error CallerNotPayee();

    constructor() ERC20("Test EURC", "tEURC") ERC20Permit("Test EURC") Ownable(msg.sender) {}

    /// @notice Reports 6 decimals, matching real USDC/EURC.
    function decimals() public pure override returns (uint8) {
        return _DECIMALS;
    }

    /**
     * @notice Owner-only faucet mint, for handing out test funds on testnet/local.
     * @param to Recipient of the freshly minted tEURC.
     * @param amount Amount in minimal units (6 decimals).
     */
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    /**
     * @notice Whether `nonce` has been consumed (used or canceled) by `authorizer`.
     * @dev The facilitator's off-chain validator reads this for its on-chain
     *      replay check before relaying.
     */
    function authorizationState(address authorizer, bytes32 nonce) external view returns (bool) {
        return _authorizationStates[authorizer][nonce];
    }

    /**
     * @notice Moves `value` from `from` to `to`, authorized by `from`'s
     *         off-chain EIP-712 signature.
     * @dev Callable by anyone (typically a facilitator/relayer paying the gas
     *      on the signer's behalf) — this is the whole point of EIP-3009:
     *      `from` never has to submit a transaction, only sign a message.
     * @param from Signer and payer.
     * @param to Payee.
     * @param value Amount in minimal units.
     * @param validAfter Unix time after which the authorization is valid (exclusive).
     * @param validBefore Unix time before which the authorization is valid (exclusive).
     * @param nonce Unique per-authorizer nonce (anti-replay).
     * @param v ECDSA signature component.
     * @param r ECDSA signature component.
     * @param s ECDSA signature component.
     */
    function transferWithAuthorization(
        address from,
        address to,
        uint256 value,
        uint256 validAfter,
        uint256 validBefore,
        bytes32 nonce,
        uint8 v,
        bytes32 r,
        bytes32 s
    ) external {
        _requireValidAuthorization(from, nonce, validAfter, validBefore);

        bytes32 structHash = keccak256(
            abi.encode(TRANSFER_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce)
        );
        if (ECDSA.recover(_hashTypedDataV4(structHash), v, r, s) != from) revert InvalidSignature();

        _markAuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }

    /**
     * @notice Same as {transferWithAuthorization}, but only the payee (`to`)
     *         may submit it.
     * @dev Per Circle's reference, this exists so a recipient can atomically
     *      pull a payment and act on it in the same transaction without a third
     *      party front-running/griefing the relay. {AgentPayRouter} relies on
     *      this: it is the payee, so only it can pull the funds.
     * @param from Signer and payer.
     * @param to Payee — must equal `msg.sender`.
     * @param value Amount in minimal units.
     * @param validAfter Unix time after which the authorization is valid (exclusive).
     * @param validBefore Unix time before which the authorization is valid (exclusive).
     * @param nonce Unique per-authorizer nonce (anti-replay).
     * @param v ECDSA signature component.
     * @param r ECDSA signature component.
     * @param s ECDSA signature component.
     */
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
    ) external {
        if (to != msg.sender) revert CallerNotPayee();
        _requireValidAuthorization(from, nonce, validAfter, validBefore);

        bytes32 structHash = keccak256(
            abi.encode(RECEIVE_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce)
        );
        if (ECDSA.recover(_hashTypedDataV4(structHash), v, r, s) != from) revert InvalidSignature();

        _markAuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }

    /**
     * @notice Voids an as-yet-unused authorization nonce, signed off-chain by
     *         the authorizer.
     * @dev EIP-3009 completeness: lets a signer revoke a leaked-but-unrelayed
     *      authorization before anyone submits it. Marking the nonce used means
     *      a later transfer/receive with the same nonce reverts with
     *      {AuthorizationAlreadyUsed}. No funds move.
     * @param authorizer The account that signed the original authorization.
     * @param nonce The nonce to void.
     * @param v ECDSA signature component (over the CancelAuthorization struct).
     * @param r ECDSA signature component.
     * @param s ECDSA signature component.
     */
    function cancelAuthorization(address authorizer, bytes32 nonce, uint8 v, bytes32 r, bytes32 s) external {
        if (_authorizationStates[authorizer][nonce]) revert AuthorizationAlreadyUsed();

        bytes32 structHash = keccak256(abi.encode(CANCEL_AUTHORIZATION_TYPEHASH, authorizer, nonce));
        if (ECDSA.recover(_hashTypedDataV4(structHash), v, r, s) != authorizer) revert InvalidSignature();

        _authorizationStates[authorizer][nonce] = true;
        emit AuthorizationCanceled(authorizer, nonce);
    }

    /// @dev Shared window + replay checks. `block.timestamp` is the correct and
    /// only clock for EIP-3009's validity window; the seconds-scale miner
    /// timestamp tolerance is immaterial to a 5-minute authorization window
    /// (Slither `timestamp` finding accepted — see docs/audit/06-slither.md).
    function _requireValidAuthorization(address authorizer, bytes32 nonce, uint256 validAfter, uint256 validBefore)
        private
        view
    {
        if (block.timestamp <= validAfter) revert AuthorizationNotYetValid();
        if (block.timestamp >= validBefore) revert AuthorizationExpired();
        if (_authorizationStates[authorizer][nonce]) revert AuthorizationAlreadyUsed();
    }

    function _markAuthorizationUsed(address authorizer, bytes32 nonce) private {
        _authorizationStates[authorizer][nonce] = true;
        emit AuthorizationUsed(authorizer, nonce);
    }
}
