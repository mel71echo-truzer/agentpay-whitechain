// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

import "@openzeppelin/contracts/token/ERC20/ERC20.sol";
import "@openzeppelin/contracts/token/ERC20/extensions/ERC20Permit.sol";
import "@openzeppelin/contracts/access/Ownable.sol";
import "@openzeppelin/contracts/utils/cryptography/ECDSA.sol";

/**
 * @title tEURC — Test EURC-style stablecoin for the AgentPay PoC
 *
 * A minimal testnet stand-in for a regulated euro stablecoin: 6 decimals
 * (like real USDC/EURC), owner-only mint (faucet-style, for handing out
 * test funds), EIP-2612 permit (via OpenZeppelin's ERC20Permit) and
 * EIP-3009 transferWithAuthorization/receiveWithAuthorization so an agent
 * can authorize a payment with an off-chain signature — no on-chain
 * approve() step, and no need to wait for that signature's *own*
 * transaction to mine before someone else (the facilitator) relays it.
 *
 * EIP-3009 implementation follows Circle's reference FiatTokenV2 contract
 * verbatim in spirit (typehashes, validAfter/validBefore semantics, nonce
 * bookkeeping): https://github.com/circlefin/stablecoin-evm/blob/master/contracts/v2/EIP3009.sol
 * Rebuilt here on top of OpenZeppelin's EIP712/ECDSA primitives (via
 * ERC20Permit, which already extends EIP712) instead of copying Circle's
 * own EIP712 helper, to avoid pulling in a second, differently-shaped
 * EIP712 implementation alongside OpenZeppelin's.
 */
contract tEURC is ERC20, ERC20Permit, Ownable {
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

    /// authorizer => nonce => used. Anti-replay for both authorization
    /// flavors lives in one map, matching Circle's reference (a nonce used
    /// for a transfer can't later be reused for a receive, or vice versa).
    mapping(address => mapping(bytes32 => bool)) private _authorizationStates;

    event AuthorizationUsed(address indexed authorizer, bytes32 indexed nonce);

    constructor() ERC20("Test EURC", "tEURC") ERC20Permit("Test EURC") Ownable(msg.sender) {}

    function decimals() public pure override returns (uint8) {
        return _DECIMALS;
    }

    /// @notice Owner-only faucet mint, for handing out test funds on testnet/local.
    function mint(address to, uint256 amount) external onlyOwner {
        _mint(to, amount);
    }

    function authorizationState(address authorizer, bytes32 nonce) external view returns (bool) {
        return _authorizationStates[authorizer][nonce];
    }

    /**
     * @notice Moves `value` from `from` to `to`, authorized by `from`'s
     * off-chain EIP-712 signature. Callable by anyone (typically a
     * facilitator/relayer paying the gas on the signer's behalf) — this is
     * the whole point of EIP-3009: `from` never has to submit a
     * transaction, only sign a message.
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
        address signer = ECDSA.recover(_hashTypedDataV4(structHash), v, r, s);
        require(signer == from, "tEURC: invalid signature");

        _markAuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }

    /**
     * @notice Same as transferWithAuthorization, but only the payee (`to`)
     * may submit it. Per Circle's reference, this exists so a recipient can
     * atomically pull a payment and act on it in the same transaction
     * without a third party front-running/griefing the relay.
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
        require(to == msg.sender, "tEURC: caller must be the payee");
        _requireValidAuthorization(from, nonce, validAfter, validBefore);

        bytes32 structHash = keccak256(
            abi.encode(RECEIVE_WITH_AUTHORIZATION_TYPEHASH, from, to, value, validAfter, validBefore, nonce)
        );
        address signer = ECDSA.recover(_hashTypedDataV4(structHash), v, r, s);
        require(signer == from, "tEURC: invalid signature");

        _markAuthorizationUsed(from, nonce);
        _transfer(from, to, value);
    }

    function _requireValidAuthorization(address authorizer, bytes32 nonce, uint256 validAfter, uint256 validBefore)
        private
        view
    {
        require(block.timestamp > validAfter, "tEURC: authorization is not yet valid");
        require(block.timestamp < validBefore, "tEURC: authorization is expired");
        require(!_authorizationStates[authorizer][nonce], "tEURC: authorization is used or canceled");
    }

    function _markAuthorizationUsed(address authorizer, bytes32 nonce) private {
        _authorizationStates[authorizer][nonce] = true;
        emit AuthorizationUsed(authorizer, nonce);
    }
}
